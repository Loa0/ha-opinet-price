import logging
import math
import async_timeout
import json
from datetime import timedelta

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
    PROD_CODES,
)

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    api_key = entry.data.get(CONF_API_KEY)
    radius = int(entry.options.get(CONF_RADIUS, entry.data.get(CONF_RADIUS, 5000)))
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
    
    _LOGGER.debug(
        "Setting up Opinet Price entry. options: %s, data: %s, poll_div: %s, self_only: %s, highway_filter: %s",
        entry.options,
        entry.data,
        poll_div,
        self_only,
        highway_filter,
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
    )
    await coordinator.async_config_entry_first_refresh()

    # 상위 10개 주유소에 대한 개별 센서 생성
    sensors = []
    for i in range(10):
        sensors.append(OpinetStationSensor(coordinator, entry, i, location_entity, show_distance))
    
    async_add_entities(sensors)

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

class OpinetDataUpdateCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api_key, radius, prodcd, location_entity, poll_div=None, self_only=False, highway_filter="전체"):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=3),
        )
        self.config_entry = entry
        self.api_key = api_key
        self.radius = radius
        self.prodcd = prodcd
        self.location_entity = location_entity
        self.poll_div = poll_div
        self.self_only = self_only
        self.highway_filter = highway_filter
        self.converter = KatecConverter()

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
                    
                    if stations:
                        stations.sort(key=lambda x: int(x["PRICE"]))
                    
                    return stations
        except Exception as e:
            _LOGGER.error("Exception in Opinet API update: %s", e, exc_info=True)
            raise UpdateFailed(f"Error communicating with API: {e}")

class OpinetStationSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, index, location_entity, show_distance=True):
        super().__init__(coordinator)
        self._index = index
        self._location_entity = location_entity
        self._show_distance = show_distance
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
        return f"{self._index + 1}위"

    @property
    def state(self):
        stations = self.coordinator.data
        if stations and len(stations) > self._index:
            s = stations[self._index]
            base = f"{s['OS_NM']}: {int(s['PRICE']):,}원"
            if self._show_distance:
                base += f" ({float(s['DISTANCE'])/1000:.1f}km)"
            return base
        return "검색 결과 없음"

    @property
    def extra_state_attributes(self):
        stations = self.coordinator.data
        if stations and len(stations) > self._index:
            s = stations[self._index]
            full_addr = s.get("VAN_ADR", "주소 정보 없음")
            # 간략 주소: 첫 번째 공백 기준 앞부분(시/도)을 제외한 나머지
            parts = full_addr.split(" ", 1)
            short_addr = parts[1] if len(parts) > 1 and len(parts[1]) > 0 else full_addr
            return {
                "주유소명": s["OS_NM"],
                "가격": int(s["PRICE"]),
                "주소": full_addr,
                "간략주소": short_addr,
                "브랜드": s["POLL_DIV_CD"],
                "거리": f"{float(s['DISTANCE'])/1000:.1f} km",
                "순위": self._index + 1
            }
        return {}
