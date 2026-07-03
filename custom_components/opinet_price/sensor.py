import logging
import math
import asyncio
import async_timeout
import json
import hashlib
from urllib.parse import quote

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity, UpdateFailed
from homeassistant.helpers.device_registry import DeviceInfo

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_RADIUS,
    CONF_PRODCD,
    CONF_LOCATION_ENTITY,
    CONF_POLL_DIV,
    CONF_SELF_ONLY,
    CONF_HIGHWAY_FILTER,
    CONF_MAX_DISTANCE,
    CONF_TMAP_KEY,
    CONF_SORT_ORDER,
    CONF_FAVORITES,
    CONF_VWORLD_KEY,
    PROD_CODES,
)

_LOGGER = logging.getLogger(__name__)

TMAP_ROUTE_URL = "https://apis.openapi.sk.com/tmap/routes?version=1"
TMAP_GEO_URL = "https://apis.openapi.sk.com/tmap/geo/reversegeocoding?version=1"

# ── GeoAPI 설정 ──────────────────────────────────────────────
GEOCODE_URL = "https://geo.ychome.kozow.com"
OPINET_DETAIL_URL = "https://www.opinet.co.kr/api/detailById.do"
# ─────────────────────────────────────────────────────────────

async def async_setup_entry(hass, entry, async_add_entities):
    api_key = entry.options.get(CONF_API_KEY, entry.data.get(CONF_API_KEY))
    radius_raw = entry.options.get(CONF_RADIUS, entry.data.get(CONF_RADIUS, 5.0))
    # km → m 변환 (하위 호환: 100 초과면 이미 m 단위)
    if isinstance(radius_raw, (int, float)):
        if radius_raw < 100:
            radius = int(float(radius_raw) * 1000)
        else:
            radius = int(radius_raw)
    else:
        radius = int(radius_raw)
    prodcd = entry.data.get(CONF_PRODCD, "B027")
    location_entity = entry.data.get(CONF_LOCATION_ENTITY)
    
    poll_div = entry.options.get(
        CONF_POLL_DIV,
        entry.data.get(
            CONF_POLL_DIV,
            entry.options.get("poll_div", entry.data.get("poll_div"))
        )
    )
    self_only = entry.options.get(CONF_SELF_ONLY, entry.data.get(CONF_SELF_ONLY, False))
    highway_filter = entry.options.get(CONF_HIGHWAY_FILTER, entry.data.get(CONF_HIGHWAY_FILTER, "전체"))
    show_distance = entry.options.get(CONF_MAX_DISTANCE, entry.data.get(CONF_MAX_DISTANCE, True))
    if isinstance(show_distance, str):
        show_distance = show_distance.lower() not in ("false", "0", "no")
    show_distance = bool(show_distance)
    tmap_key = entry.options.get(CONF_TMAP_KEY, entry.data.get(CONF_TMAP_KEY, ""))
    sort_order = entry.options.get(CONF_SORT_ORDER, entry.data.get(CONF_SORT_ORDER, "가격순"))
    vworld_key = entry.options.get(CONF_VWORLD_KEY, entry.data.get(CONF_VWORLD_KEY, ""))
    
    _LOGGER.debug(
        "Setting up Opinet Price entry. options: %s, data: %s, poll_div: %s, self_only: %s, highway_filter: %s, sort: %s",
        entry.options,
        entry.data,
        poll_div,
        self_only,
        highway_filter,
        sort_order,
    )

    coordinator = OpinetDataUpdateCoordinator(
        hass,
        entry,
        api_key,
        radius,
        prodcd,
        location_entity,
        poll_div,
        self_only,
        highway_filter,
        tmap_key,
        sort_order,
        vworld_key,
    )
    await coordinator.async_config_entry_first_refresh()

    # 상위 10개 주유소에 대한 개별 센서 생성
    sensors = []
    for i in range(10):
        sensors.append(OpinetStationSensor(coordinator, entry, i, location_entity, show_distance))
    
    # 즐겨찾기 센서
    favorites = entry.options.get(CONF_FAVORITES, [])
    for i, uni_id in enumerate(favorites):
        sensors.append(OpinetStationSensor(coordinator, entry, i, location_entity, show_distance, uni_id=uni_id))
    
    async_add_entities(sensors)
    
    # API 사용량 센서
    async_add_entities([OpinetApiUsageSensor(coordinator, entry)])
    
    # store coordinator for OptionsFlow access
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

class KatecConverter:
    def __init__(self):
        self.wgs84_a = 6378137.0
        self.wgs84_es = 0.00669437999014
        self.bessel_a = 6377397.155
        self.bessel_es = 0.00667437223131
        self.dx, self.dy, self.dz = -115.80, 474.99, 674.11
        self.rx = 1.16 / 3600 * (math.pi / 180)
        self.ry = -2.31 / 3600 * (math.pi / 180)
        self.rz = -1.63 / 3600 * (math.pi / 180)
        self.ds = 6.43 / 1000000
        self.lon0, self.lat0 = 128.0 * (math.pi / 180), 38.0 * (math.pi / 180)
        self.k0, self.x0, self.y0 = 0.9999, 400000.0, 600000.0

    def wgs84_to_katec(self, lat, lon):
        lat_r, lon_r = math.radians(lat), math.radians(lon)
        v = self.wgs84_a / math.sqrt(1 - self.wgs84_es * math.sin(lat_r)**2)
        x = v * math.cos(lat_r) * math.cos(lon_r)
        y = v * math.cos(lat_r) * math.sin(lon_r)
        z = v * (1 - self.wgs84_es) * math.sin(lat_r)
        s = 1 - self.ds
        bx = -self.dx + s * (x + self.rz * y - self.ry * z)
        by = -self.dy + s * (-self.rz * x + y + self.rx * z)
        bz = -self.dz + s * (self.ry * x - self.rx * y + z)
        blon = math.atan2(by, bx)
        p = math.sqrt(bx**2 + by**2)
        blat = math.atan2(bz, p * (1 - self.bessel_es))
        for _ in range(5):
            v_b = self.bessel_a / math.sqrt(1 - self.bessel_es * math.sin(blat)**2)
            blat = math.atan2(bz + self.bessel_es * v_b * math.sin(blat), p)
        e2 = self.bessel_es / (1 - self.bessel_es)
        ml0 = self._meridian(self.lat0)
        d_lon = blon - self.lon0
        sin_b, cos_b, tan_b = math.sin(blat), math.cos(blat), math.tan(blat)
        v_final = self.bessel_a / math.sqrt(1 - self.bessel_es * sin_b**2)
        t, c, a_val = tan_b**2, e2 * cos_b**2, d_lon * cos_b
        m = self._meridian(blat)
        kx = self.k0 * v_final * (a_val + (1-t+c)*a_val**3/6 + (5-18*t+t**2+72*c-58*e2)*a_val**5/120) + self.x0
        ky = self.k0 * (m - ml0 + v_final * tan_b * (a_val**2/2 + (5-t+9*c+4*c**2)*a_val**4/24 + (61-58*t+t**4+600*c-330*e2)*a_val**6/720)) + self.y0
        return kx, ky

    def _meridian(self, lat):
        return self.bessel_a * ((1 - 0.00667437223131/4 - 3*(0.00667437223131**2)/64) * lat - (3*0.00667437223131/8 + 3*(0.00667437223131**2)/32) * math.sin(2*lat) + (15*(0.00667437223131**2)/256) * math.sin(4*lat))

def katec_to_wgs84(gis_x, gis_y):
    """Opinet GIS_X_COOR/GIS_Y_COOR (Bessel KATEC) → WGS84 (lat, lon)"""
    try:
        kx = float(gis_x); ky = float(gis_y)
    except (TypeError, ValueError):
        return None, None
    
    # Bessel 1841 parameters
    a = 6377397.155
    es = 0.00667437223131
    lon0 = 128.0 * math.pi / 180
    lat0 = 38.0 * math.pi / 180
    k0 = 0.9999
    x0 = 400000.0
    y0 = 600000.0
    dx, dy, dz = 115.80, -474.99, -674.11
    
    kx -= x0; ky -= y0
    
    # Meridian distance
    def _m(lat):
        return a * ((1-es/4-3*es**2/64-5*es**3/256)*lat
               - (3*es/8+3*es**2/32+45*es**3/1024)*math.sin(2*lat)
               + (15*es**2/256+45*es**3/1024)*math.sin(4*lat)
               - (35*es**3/3072)*math.sin(6*lat))
    
    M0 = _m(lat0)
    M = M0 + ky / k0
    e1 = (1 - math.sqrt(1 - es)) / (1 + math.sqrt(1 - es))
    mu = M / (a * (1 - es/4 - 3*es**2/64 - 5*es**3/256))
    
    phi1 = mu + (3*e1/2 - 27*e1**3/32)*math.sin(2*mu) \
           + (21*e1**2/16 - 55*e1**4/32)*math.sin(4*mu) \
           + (151*e1**3/96)*math.sin(6*mu)
    
    sin_p, cos_p, tan_p = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    N1 = a / math.sqrt(1 - es * sin_p**2)
    T, C = tan_p**2, es / (1 - es) * cos_p**2
    D = kx / (N1 * k0)
    
    blat = phi1 - (N1*tan_p/(a*(1-es)/(1-es*sin_p**2)**1.5)) * (
        D**2/2 - (5+3*T+10*C-4*C**2-9*es/(1-es))*D**4/24
        + (61+90*T+298*C+45*T**2-252*es/(1-es)-3*C**2)*D**6/720
    )
    blon = lon0 + (D - (1+2*T+C)*D**3/6
          + (5-2*C+28*T-3*C**2+8*es/(1-es)+24*T**2)*D**5/120) / cos_p
    
    # Bessel → WGS84 Molodensky
    sin_b, cos_b = math.sin(blat), math.cos(blat)
    sin_l, cos_l = math.sin(blon), math.cos(blon)
    v = a / math.sqrt(1 - es * sin_b**2)
    bx = v * cos_b * cos_l
    by = v * cos_b * sin_l
    bz = v * (1 - es) * sin_b
    wx = bx + dx; wy = by + dy; wz = bz + dz
    
    wa = 6378137.0; wes = 0.00669437999014
    p = math.sqrt(wx**2 + wy**2)
    wlat = math.atan2(wz, p * (1 - wes))
    for _ in range(5):
        vw = wa / math.sqrt(1 - wes * math.sin(wlat)**2)
        wlat = math.atan2(wz + wes * vw * math.sin(wlat), p)
    wlon = math.atan2(wy, wx)
    
    return math.degrees(wlat), math.degrees(wlon)


async def _fetch_detail_by_id(session, api_key: str, uni_id: str) -> dict | None:
    """detailById.do → NEW_ADR(도로명주소) 반환"""
    url = f"{OPINET_DETAIL_URL}?code={api_key}&id={uni_id}&out=json"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    try:
        async with async_timeout.timeout(10):
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    oil = (data.get("RESULT", {}) or {}).get("OIL")
                    if oil:
                        return {
                            "addr": oil.get("NEW_ADR", ""),
                            "name": oil.get("OS_NM", ""),
                        }
                    _LOGGER.warning("detailById.do: no OIL data for %s, response: %s", uni_id, data)
                else:
                    _LOGGER.warning("detailById.do failed for %s: HTTP %s", uni_id, resp.status)
    except Exception as e:
        _LOGGER.warning("detailById.do error for %s: %s", uni_id, e)
    return None


async def _fetch_station_coords(session, api_key: str, uid: str, vworld_key: str, uni_id: str) -> tuple[dict | None, bool]:
    """UNI_ID → ({addr, lat, lng}, detail_called).
    GeoAPI /station/{uni_id}(Redis) 우선, MISS → detailById + /geocode"""
    # 1. GeoAPI /station/{uni_id} → Redis 캐시 조회
    station_url = f"{GEOCODE_URL}/station/{quote(uni_id, safe='')}"
    try:
        async with async_timeout.timeout(5):
            async with session.get(station_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("lat") is not None and data.get("lng") is not None:
                        _LOGGER.debug("Station %s: cache HIT → %s (%.6f, %.6f)",
                                      uni_id, data.get("addr", ""), data["lat"], data["lng"])
                        return (data, False)
    except Exception as e:
        _LOGGER.warning("Station cache lookup failed for %s: %s", uni_id, e)

    # 2. MISS → detailById.do → 도로명주소
    _LOGGER.warning("Station %s: cache MISS, calling detailById.do", uni_id)
    detail = await _fetch_detail_by_id(session, api_key, uni_id)
    if not detail or not detail["addr"]:
        _LOGGER.warning("Station %s: detailById.do failed or no addr", uni_id)
        return (None, True)

    addr = detail["addr"]

    # 3. GeoAPI /geocode → VWorld (GeoAPI가 Redis에 station:{uni_id} 저장)
    params = f"address={quote(addr)}&uid={quote(uid)}&uni_id={quote(uni_id, safe='')}"
    if vworld_key:
        params += f"&api_key={quote(vworld_key)}"
    geo_url = f"{GEOCODE_URL}/geocode?{params}"
    try:
        async with async_timeout.timeout(10):
            async with session.get(geo_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        result = {"addr": addr, "lat": float(data["lat"]), "lng": float(data["lng"])}
                        _LOGGER.debug("Station %s: %s → (%.6f, %.6f) [cached=%s]",
                                      uni_id, addr, result["lat"], result["lng"], data.get("cached"))
                        return (result, True)
                    elif data.get("status") == "rate_limited":
                        _LOGGER.warning("GeoAPI rate limited for uid=%s", uid)
                else:
                    _LOGGER.debug("GeoAPI HTTP %s for %s", resp.status, uni_id)
    except Exception as e:
        _LOGGER.debug("GeoAPI call failed for %s: %s", uni_id, e)

    return (None, True)

async def _fetch_tmap_distance(session, tmap_key, start_lat, start_lon, end_lat, end_lon):
    """Tmap API로 주행거리(m) 조회"""
    headers = {
        "appKey": tmap_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps({
        "startX": start_lon,
        "startY": start_lat,
        "endX": end_lon,
        "endY": end_lat,
        "reqCoordType": "WGS84GEO",
        "resCoordType": "WGS84GEO",
    })
    try:
        async with async_timeout.timeout(10):
            async with session.post(TMAP_ROUTE_URL, headers=headers, data=body) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    features = data.get("features", [])
                    if features:
                        props = features[0].get("properties", {})
                        return props.get("totalDistance", None)
                else:
                    _LOGGER.debug("Tmap API error: %s", resp.status)
    except Exception as e:
        _LOGGER.debug("Tmap API call failed: %s", e)
    return None

async def _fetch_tmap_address(session, tmap_key, lat, lon):
    """Tmap 역지오코딩으로 주소 조회 → (전체주소, 간략주소)"""
    url = f"{TMAP_GEO_URL}&lat={lat}&lon={lon}&coordType=WGS84GEO&addressType=A00"
    headers = {"appKey": tmap_key, "Accept": "application/json"}
    try:
        async with async_timeout.timeout(5):
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    addr_info = data.get("addressInfo", {})
                    full = addr_info.get("fullAddress", "")
                    # 간략주소: 시/도 제외
                    parts = full.split(" ", 1)
                    short = parts[1] if len(parts) > 1 and parts[1] else full
                    return full, short
    except Exception as e:
        _LOGGER.debug("Tmap geocoding failed: %s", e)
    return "", ""

class OpinetDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api_key, radius, prodcd, location_entity, poll_div=None, self_only=False, highway_filter="전체", tmap_key="", sort_order="가격순", vworld_key=""):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
        )
        self.config_entry = entry
        self.api_key = api_key
        self.radius = radius
        self.prodcd = prodcd
        self.location_entity = location_entity
        self.poll_div = poll_div
        self.self_only = self_only
        self.highway_filter = highway_filter
        self.tmap_key = tmap_key
        self.sort_order = sort_order
        self.vworld_key = vworld_key
        self.converter = KatecConverter()
        self.opinet_call_count = 0
        self.tmap_call_count = 0

    async def _async_update_data(self):
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        if self.location_entity:
            loc = self.hass.states.get(self.location_entity)
            if loc:
                if "Location" in loc.attributes and isinstance(loc.attributes["Location"], list):
                    lat, lon = loc.attributes["Location"][0], loc.attributes["Location"][1]
                elif "latitude" in loc.attributes:
                    lat, lon = loc.attributes["latitude"], loc.attributes["longitude"]
                elif "lat" in loc.attributes:
                    lat, lon = loc.attributes["lat"], loc.attributes["lon"]
        
        kx, ky = self.converter.wgs84_to_katec(lat, lon)
        kx_int, ky_int = int(kx), int(ky)
        
        url = f"https://www.opinet.co.kr/api/aroundAll.do?code={self.api_key}&x={kx_int}&y={ky_int}&radius={int(self.radius)}&prodcd={self.prodcd}&sort=1&out=json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

        _LOGGER.debug(
            "Calling Opinet API. URL: %s, Params - API Key: %s, Radius: %s, Prod Code: %s, Location Entity: %s",
            url,
            self.api_key,
            self.radius,
            self.prodcd,
            self.location_entity,
        )

        try:
            async with async_timeout.timeout(15):
                session = async_get_clientsession(self.hass)
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"API Error: {response.status}")
                    
                    body = (await response.text()).strip()
                    res = json.loads(body)
                    self.opinet_call_count += 1
                    
                    result_data = res.get("RESULT", {})
                    stations = []
                    if isinstance(result_data, dict):
                        stations = result_data.get("OIL", [])
                    else:
                        _LOGGER.warning("Opinet API returned error or unexpected RESULT format: %s", res)
                    
                    _LOGGER.debug("Retrieved %d stations from Opinet API", len(stations))
                    
                    # 1. 브랜드 필터링 적용
                    if self.poll_div and stations:
                        if isinstance(self.poll_div, list):
                            allowed_brands = self.poll_div
                        else:
                            allowed_brands = [b.strip() for b in self.poll_div.split(",") if b.strip()]
                        
                        if allowed_brands:
                            stations = [s for s in stations if s.get("POLL_DIV_CD") in allowed_brands]
                            _LOGGER.debug(
                                "Filtered stations by brand(s) %s: %d stations remaining",
                                allowed_brands,
                                len(stations),
                            )
                    else:
                        _LOGGER.debug("Brand filtering not applied. poll_div: %s", self.poll_div)

                    # 2. 셀프 필터링 적용
                    if self.self_only and stations:
                        stations = [s for s in stations if "셀프" in s.get("OS_NM", "")]
                        _LOGGER.debug("Filtered stations by self-service: %d stations remaining", len(stations))

                    # 3. 고속도로 필터링 적용
                    if self.highway_filter and self.highway_filter != "전체" and stations:
                        if self.highway_filter == "고속도로만":
                            stations = [
                                s for s in stations 
                                if s.get("POLL_DIV_CD") == "RTX" or "휴게소" in s.get("OS_NM", "")
                            ]
                        elif self.highway_filter == "고속도로 제외":
                            stations = [
                                s for s in stations 
                                if not (s.get("POLL_DIV_CD") == "RTX" or "휴게소" in s.get("OS_NM", ""))
                            ]
                        _LOGGER.debug(
                            "Filtered stations by highway filter (%s): %d stations remaining",
                            self.highway_filter,
                            len(stations),
                        )
                    
                    # 4. GeoAPI로 정확한 좌표 획득 (UNI_ID → Redis(/station/{uni_id}) → MISS → detailById → VWorld)
                    if stations:
                        uid = self.config_entry.entry_id
                        vw_key = self.vworld_key.strip() if self.vworld_key else ""
                        geo_tasks = [_fetch_station_coords(session, self.api_key, uid, vw_key, s.get("UNI_ID", "")) for s in stations]
                        geo_results = await asyncio.gather(*geo_tasks, return_exceptions=True)
                        detail_calls = 0
                        for i, s in enumerate(stations):
                            r = geo_results[i]
                            if isinstance(r, tuple) and len(r) == 2:
                                coords, called = r
                                if called:
                                    detail_calls += 1
                                if isinstance(coords, dict) and coords.get("lat") is not None:
                                    s["_GEO_LAT"] = coords["lat"]
                                    s["_GEO_LNG"] = coords["lng"]
                                    s["_GEO_ADDR"] = coords.get("addr", "")
                            elif isinstance(r, Exception):
                                detail_calls += 1
                                _LOGGER.debug("Station coord error for %s: %s", s.get("OS_NM"), r)
                        self.opinet_call_count += detail_calls

                    # 5. Tmap 주행거리 + 주소 조회 (GeoAPI 좌표 우선, 없으면 KATEC)
                    if self.tmap_key and stations:
                        _LOGGER.debug("Fetching Tmap driving distances for %d stations", len(stations))
                        dist_tasks = []
                        addr_tasks = []
                        addr_indices = []
                        for i, s in enumerate(stations):
                            # GeoAPI 좌표 우선, 없으면 KATEC 변환
                            gis_x = s.get("GIS_X_COOR")
                            gis_y = s.get("GIS_Y_COOR")
                            if "_GEO_LAT" in s and "_GEO_LNG" in s:
                                end_lat, end_lon = s["_GEO_LAT"], s["_GEO_LNG"]
                            else:
                                end_lat, end_lon = katec_to_wgs84(gis_x, gis_y)
                            if end_lat is not None and end_lon is not None:
                                dist_tasks.append(_fetch_tmap_distance(session, self.tmap_key, lat, lon, end_lat, end_lon))
                                addr_tasks.append(_fetch_tmap_address(session, self.tmap_key, end_lat, end_lon))
                                addr_indices.append(i)
                            else:
                                dist_tasks.append(None)
                        
                        tmap_distances = await asyncio.gather(*dist_tasks, return_exceptions=True)
                        tmap_addresses = await asyncio.gather(*addr_tasks, return_exceptions=True) if addr_tasks else []
                        self.tmap_call_count += len(addr_indices)
                        
                        for i, s in enumerate(stations):
                            dist = tmap_distances[i]
                            if isinstance(dist, (int, float)):
                                s["_TMAP_DISTANCE"] = dist
                            elif isinstance(dist, Exception):
                                _LOGGER.debug("Tmap distance error for %s: %s", s.get("OS_NM"), dist)
                        
                        for j, idx in enumerate(addr_indices):
                            addr = tmap_addresses[j] if j < len(tmap_addresses) else ("", "")
                            if isinstance(addr, tuple) and len(addr) == 2:
                                stations[idx]["_TMAP_ADDRESS"] = addr[0]
                                stations[idx]["_TMAP_SHORT_ADDR"] = addr[1]
                            elif isinstance(addr, Exception):
                                _LOGGER.debug("Tmap address error for %s: %s", stations[idx].get("OS_NM"), addr)
                    
                    # 정렬
                    if self.sort_order == "주행거리순":
                        stations.sort(key=lambda x: float(x.get("_TMAP_DISTANCE", 1e9)))
                        _LOGGER.debug("Sorted stations by Tmap driving distance")
                    elif stations:
                        stations.sort(key=lambda x: int(x["PRICE"]))
                    
                    return stations
        except Exception as e:
            _LOGGER.error("Exception in Opinet API update: %s", e, exc_info=True)
            raise UpdateFailed(f"Error communicating with API: {e}")

class OpinetStationSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, index, location_entity, show_distance=True, uni_id=None):
        super().__init__(coordinator)
        self._index = index
        self._uni_id = uni_id
        self._location_entity = location_entity
        self._show_distance = show_distance
        if uni_id:
            self._attr_unique_id = f"opinet_price_{self._location_entity or 'home'}_fav_{uni_id}"
        else:
            self._attr_unique_id = f"opinet_price_{self._location_entity or 'home'}_{index + 1}"
        self._attr_icon = "mdi:gas-station"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="오피넷 주유소",
            manufacturer="Opinet",
            model="주유소 가격 비교",
        )

    @property
    def name(self):
        if self._uni_id:
            return f"즐겨찾기 {self._index + 1}"
        return f"{self._index + 1}위"

    def _get_station(self):
        """Return the station dict or None. Supports both index and uni_id lookup."""
        stations = self.coordinator.data
        if not stations:
            return None
        if self._uni_id:
            for s in stations:
                if s.get("UNI_ID") == self._uni_id:
                    return s
            return None
        if len(stations) > self._index:
            return stations[self._index]
        return None

    @property
    def state(self):
        s = self._get_station()
        if s:
            base = f"{s['OS_NM']}:\n{int(s['PRICE']):,}원"
            if self._show_distance:
                # Tmap 주행거리 우선, 없으면 Opinet 직선거리
                tmap_dist = s.get("_TMAP_DISTANCE")
                if tmap_dist is not None:
                    base += f" ({float(tmap_dist)/1000:.1f}km)"
                else:
                    dist = s.get("DISTANCE", 0)
                    base += f" ({float(dist)/1000:.1f}km)"
            return base
        return "검색 결과 없음"

    @property
    def extra_state_attributes(self):
        s = self._get_station()
        if s:
            # 주소: GeoAPI 도로명주소 > Tmap 역지오코딩 > VAN_ADR
            full_addr = s.get("_GEO_ADDR") or s.get("_TMAP_ADDRESS") or s.get("VAN_ADR") or ""
            if not full_addr:
                full_addr = f"{s['OS_NM']} (주소 정보 없음)"
            short_addr = s.get("_TMAP_SHORT_ADDR") or ""
            if not short_addr:
                parts = full_addr.split(" ", 1)
                short_addr = parts[1] if len(parts) > 1 and parts[1] else full_addr
            # 거리: Tmap 주행거리 우선
            tmap_dist = s.get("_TMAP_DISTANCE")
            dist_str = f"{float(tmap_dist)/1000:.1f} km" if tmap_dist is not None else f"{float(s.get('DISTANCE', 0))/1000:.1f} km"
            attrs = {
                "주유소명": s["OS_NM"],
                "가격": int(s["PRICE"]),
                "주소": full_addr,
                "간략주소": short_addr,
                "브랜드": s["POLL_DIV_CD"],
                "거리": dist_str,
            }
            if not self._uni_id:
                attrs["순위"] = self._index + 1
            return attrs
        return {}

class OpinetApiUsageSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"opinet_price_api_usage_{entry.entry_id}"
        self._attr_name = "API 사용량"
        self._attr_icon = "mdi:counter"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="오피넷 주유소",
            manufacturer="Opinet",
            model="주유소 가격 비교",
        )

    @property
    def state(self):
        return f"오피넷 {self.coordinator.opinet_call_count}회 | Tmap {self.coordinator.tmap_call_count}회"

    @property
    def extra_state_attributes(self):
        return {
            "오피넷_사용": self.coordinator.opinet_call_count,
            "오피넷_제한": 1500,
            "Tmap_사용": self.coordinator.tmap_call_count,
            "Tmap_제한": 30000,
        }
