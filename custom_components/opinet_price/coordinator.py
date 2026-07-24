"""OpinetDataUpdateCoordinator — 데이터 갱신 및 조정 로직"""

import asyncio
import json
import logging
from urllib.parse import quote

import async_timeout

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, CONF_API_KEY, CONF_RADIUS, CONF_PRODCD, CONF_LOCATION_ENTITY,
    CONF_POLL_DIV, CONF_SELF_ONLY, CONF_HIGHWAY_FILTER, CONF_MAX_DISTANCE,
    CONF_TMAP_KEY, CONF_SORT_ORDER, CONF_FAVORITES, FAV_LABELS,
    CONF_VWORLD_KEY,
)
from ._coord_util import katec_to_wgs84, _get_price
from .api_client import (
    _fetch_detail_by_id_full, _fetch_station_coords,
    _fetch_tmap_distance, _fetch_tmap_address,
)

_LOGGER = logging.getLogger(__name__)

OPINET_AROUND_URL = "https://www.opinet.co.kr/api/aroundAll.do"


class KatecConverter:
    """KATEC 좌표변환 (Bessel → WGS84) — kept for backward compat with __init__.py"""

    def wgs84_to_katec(self, lat, lon):
        """WGS84 → KATEC (GIS_X/Y) — aroundAll API용"""
        return _wgs84_to_katec(lat, lon)


def _wgs84_to_katec(lat, lon):
    """WGS84 (lat, lon) → KATEC (x, y) — aroundAll API 파라미터용"""
    import math

    # WGS84 parameters
    wgs84_a = 6378137.0
    wgs84_es = 0.00669437999014
    # Bessel 1841 parameters
    bessel_a = 6377397.155
    bessel_es = 0.00667437223131
    dx, dy, dz = -115.80, 474.99, 674.11
    rx = 1.16 / 3600 * (math.pi / 180)
    ry = -2.31 / 3600 * (math.pi / 180)
    rz = -1.63 / 3600 * (math.pi / 180)
    ds = 6.43 / 1000000
    lon0, lat0 = 128.0 * (math.pi / 180), 38.0 * (math.pi / 180)
    k0, x0, y0 = 0.9999, 400000.0, 600000.0

    lat_r, lon_r = math.radians(lat), math.radians(lon)
    v = wgs84_a / math.sqrt(1 - wgs84_es * math.sin(lat_r) ** 2)
    x = v * math.cos(lat_r) * math.cos(lon_r)
    y = v * math.cos(lat_r) * math.sin(lon_r)
    z = v * (1 - wgs84_es) * math.sin(lat_r)
    s = 1 - ds
    bx = -dx + s * (x + rz * y - ry * z)
    by = -dy + s * (-rz * x + y + rx * z)
    bz = -dz + s * (ry * x - rx * y + z)
    blon = math.atan2(by, bx)
    p = math.sqrt(bx ** 2 + by ** 2)
    blat = math.atan2(bz, p * (1 - bessel_es))
    for _ in range(5):
        v_b = bessel_a / math.sqrt(1 - bessel_es * math.sin(blat) ** 2)
        blat = math.atan2(bz + bessel_es * v_b * math.sin(blat), p)
    e2 = bessel_es / (1 - bessel_es)

    def _meridian(lat_m):
        return bessel_a * (
            (1 - bessel_es / 4 - 3 * (bessel_es ** 2) / 64) * lat_m
            - (3 * bessel_es / 8 + 3 * (bessel_es ** 2) / 32) * math.sin(2 * lat_m)
            + (15 * (bessel_es ** 2) / 256) * math.sin(4 * lat_m)
        )

    ml0 = _meridian(lat0)
    d_lon = blon - lon0
    sin_b, cos_b, tan_b = math.sin(blat), math.cos(blat), math.tan(blat)
    v_final = bessel_a / math.sqrt(1 - bessel_es * sin_b ** 2)
    t, c, a_val = tan_b ** 2, e2 * cos_b ** 2, d_lon * cos_b
    m = _meridian(blat)
    kx = (
        k0
        * v_final
        * (
            a_val
            + (1 - t + c) * a_val ** 3 / 6
            + (5 - 18 * t + t ** 2 + 72 * c - 58 * e2) * a_val ** 5 / 120
        )
        + x0
    )
    ky = (
        k0
        * (
            m
            - ml0
            + v_final
            * tan_b
            * (
                a_val ** 2 / 2
                + (5 - t + 9 * c + 4 * c ** 2) * a_val ** 4 / 24
                + (61 - 58 * t + t ** 4 + 600 * c - 330 * e2) * a_val ** 6 / 720
            )
        )
        + y0
    )
    return kx, ky


class OpinetDataUpdateCoordinator(DataUpdateCoordinator):
    """Opinet 데이터 갱신 Coordinator — API 호출 + 필터링 + GeoAPI + Tmap 통합"""

    def __init__(
        self, hass, entry, api_key, radius, prodcd, location_entity,
        poll_div=None, self_only=False, highway_filter="전체",
        tmap_key="", sort_order="가격순", vworld_key="",
        rank_count=10,
    ):
        super().__init__(hass, _LOGGER, name=DOMAIN)
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
        self.rank_count = rank_count
        self.opinet_call_count = 0
        self.vworld_call_count = 0
        self.tmap_call_count = 0
        self.opinet_errors = 0
        self.vworld_errors = 0
        self.tmap_errors = 0
        self.fav_data = []  # favorites separate from ranking

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

        kx, ky = _wgs84_to_katec(lat, lon)
        kx_int, ky_int = int(kx), int(ky)

        url = (
            f"{OPINET_AROUND_URL}?code={self.api_key}"
            f"&x={kx_int}&y={ky_int}&radius={int(self.radius)}"
            f"&prodcd={self.prodcd}&sort=1&out=json"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

        _LOGGER.debug(
            "Calling Opinet API. URL: %s, Params - API Key: %s, Radius: %s, Prod Code: %s",
            url, self.api_key, self.radius, self.prodcd,
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

                    # 1. 브랜드 필터링
                    stations = self._apply_brand_filter(stations)
                    # 2. 셀프 필터링
                    stations = self._apply_self_filter(stations)
                    # 3. 고속도로 필터링
                    stations = self._apply_highway_filter(stations)
                    # 3.5. 즐겨찾기 fallback
                    stations = await self._fetch_favorites_fallback(session, stations)
                    # 3.6. 랭킹 수 제한 (Tmap API 절약)
                    stations = self._apply_rank_limit(stations)
                    # 4. GeoAPI 좌표 획득
                    stations = await self._fetch_geo_coords(session, stations)
                    # 5. Tmap 거리/주소
                    stations = await self._fetch_tmap_data(session, lat, lon, stations)
                    # 정렬
                    stations = self._sort_stations(stations)

                    return stations
        except Exception as e:
            _LOGGER.error("Exception in Opinet API update: %s", e, exc_info=True)
            raise UpdateFailed(f"Error communicating with API: {e}")

    def _apply_brand_filter(self, stations):
        if not self.poll_div or not stations:
            return stations
        if isinstance(self.poll_div, list):
            allowed_brands = self.poll_div
        else:
            allowed_brands = [b.strip() for b in self.poll_div.split(",") if b.strip()]
        if allowed_brands:
            stations = [s for s in stations if s.get("POLL_DIV_CD") in allowed_brands]
        return stations

    def _apply_self_filter(self, stations):
        if self.self_only and stations:
            stations = [s for s in stations if "셀프" in s.get("OS_NM", "")]
        return stations

    def _apply_highway_filter(self, stations):
        if not self.highway_filter or self.highway_filter == "전체" or not stations:
            return stations
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
        return stations

    def _apply_rank_limit(self, stations):
        """랭킹 상위 N개만 유지 (Tmap API 호출 수 절약)"""
        if not stations:
            return stations
        if len(stations) <= self.rank_count:
            return stations
        # favorites는 따로 보관되므로 메인 랭킹에서만 제한
        return stations[:self.rank_count]

    async def _fetch_favorites_fallback(self, session, stations):
        favs = self.config_entry.options.get(CONF_FAVORITES, [])
        if not favs:
            return stations
        existing_ids = {s.get("UNI_ID") for s in stations}
        missing_favs = [f for f in favs if f not in existing_ids]
        if not missing_favs:
            return stations

        _LOGGER.warning(
            "Fav fallback: total=%d in_around=%d missing=%d",
            len(favs), len(existing_ids), len(missing_favs),
        )
        fav_tasks = [_fetch_detail_by_id_full(session, self.api_key, uid) for uid in missing_favs]
        fav_results = await asyncio.gather(*fav_tasks, return_exceptions=True)
        fav_stations = []
        fav_geo_addrs = []
        for i, r in enumerate(fav_results):
            if isinstance(r, dict) and r.get("UNI_ID"):
                r["_IS_FAV_ONLY"] = True
                fav_stations.append(r)
                self.opinet_call_count += 1
                addr = r.get("NEW_ADR") or r.get("VAN_ADR", "")
                fav_geo_addrs.append(addr if addr else None)
            elif isinstance(r, Exception):
                _LOGGER.warning("detailById.do error for fav %s: %s", missing_favs[i], r)
            else:
                _LOGGER.warning("detailById.do returned None for fav %s", missing_favs[i])

        if fav_stations:
            uid = self.config_entry.entry_id
            vw_key = self.vworld_key.strip() if self.vworld_key else ""
            fav_geo_tasks = [
                _fetch_station_coords(session, self.api_key, uid, vw_key, s["UNI_ID"], known_addr=addr)
                for s, addr in zip(fav_stations, fav_geo_addrs)
            ]
            fav_geo_results = await asyncio.gather(*fav_geo_tasks, return_exceptions=True)
            for i, r in enumerate(fav_geo_results):
                if isinstance(r, tuple) and len(r) == 3:
                    coords, _, vw_called = r
                    if vw_called:
                        self.vworld_call_count += 1
                    if isinstance(coords, dict) and coords.get("lat") is not None:
                        fav_stations[i]["_GEO_LAT"] = coords["lat"]
                        fav_stations[i]["_GEO_LNG"] = coords["lng"]
                        fav_stations[i]["_GEO_ADDR"] = coords.get("addr", "")
                elif isinstance(r, Exception):
                    _LOGGER.debug("Fav GeoAPI error for %s: %s", fav_stations[i].get("OS_NM"), r)
        self.fav_data = fav_stations
        return stations

    async def _fetch_geo_coords(self, session, stations):
        if not stations:
            return stations
        uid = self.config_entry.entry_id
        vw_key = self.vworld_key.strip() if self.vworld_key else ""
        geo_indices = [i for i, s in enumerate(stations) if "_GEO_LAT" not in s]
        if not geo_indices:
            return stations
        geo_tasks = [
            (i, _fetch_station_coords(
                session, self.api_key, uid, vw_key, stations[i].get("UNI_ID", "")
            ))
            for i in geo_indices
        ]
        geo_results = await asyncio.gather(*[t[1] for t in geo_tasks], return_exceptions=True)
        for (i, _), r in zip(geo_tasks, geo_results):
            s = stations[i]
            if isinstance(r, tuple) and len(r) == 3:
                coords, d_called, vw_called = r
                if d_called:
                    self.opinet_call_count += 1
                if vw_called:
                    self.vworld_call_count += 1
                if isinstance(coords, dict) and coords.get("lat") is not None:
                    s["_GEO_LAT"] = coords["lat"]
                    s["_GEO_LNG"] = coords["lng"]
                    s["_GEO_ADDR"] = coords.get("addr", "")
            elif isinstance(r, Exception):
                self.tmap_call_count += 1
                _LOGGER.debug("Station coord error for %s", s.get("OS_NM"))
                self.opinet_errors += 1
        return stations

    async def _fetch_tmap_data(self, session, lat, lon, stations):
        if not self.tmap_key or not stations:
            return stations
        dist_tasks = []
        dist_indices = []
        addr_tasks = []
        addr_indices = []

        for i, s in enumerate(stations):
            if s.get("_IS_FAV_ONLY"):
                continue
            gis_x = s.get("GIS_X_COOR")
            gis_y = s.get("GIS_Y_COOR")
            if "_GEO_LAT" in s and "_GEO_LNG" in s:
                end_lat, end_lon = s["_GEO_LAT"], s["_GEO_LNG"]
            else:
                end_lat, end_lon = katec_to_wgs84(gis_x, gis_y)
            if end_lat is not None and end_lon is not None:
                dist_tasks.append(_fetch_tmap_distance(session, self.tmap_key, lat, lon, end_lat, end_lon))
                dist_indices.append(i)
                addr_tasks.append(_fetch_tmap_address(session, self.tmap_key, end_lat, end_lon))
                addr_indices.append(i)

        if dist_tasks:
            tmap_distances = await asyncio.gather(*dist_tasks, return_exceptions=True)
            for j, i in enumerate(dist_indices):
                dist = tmap_distances[j]
                if isinstance(dist, (int, float)):
                    stations[i]["_TMAP_DISTANCE"] = dist
                elif isinstance(dist, Exception):
                    self.tmap_errors += 1
                    _LOGGER.debug("Tmap distance error for %s", stations[i].get("OS_NM"))

        if addr_tasks:
            tmap_addresses = await asyncio.gather(*addr_tasks, return_exceptions=True)
            self.tmap_call_count += len(addr_indices)
            for j, idx in enumerate(addr_indices):
                addr = tmap_addresses[j]
                if isinstance(addr, tuple) and len(addr) == 2:
                    stations[idx]["_TMAP_ADDRESS"] = addr[0]
                    stations[idx]["_TMAP_SHORT_ADDR"] = addr[1]
                elif isinstance(addr, Exception):
                    self.tmap_errors += 1
                    _LOGGER.debug("Tmap address error for %s", stations[idx].get("OS_NM"))
        return stations

    def _sort_stations(self, stations):
        if self.sort_order == "주행거리순":
            stations.sort(key=lambda x: float(x.get("_TMAP_DISTANCE") or 1e9))
        elif stations:
            stations.sort(key=lambda x: (_get_price(x), float(x.get("_TMAP_DISTANCE") or 1e9)))
        return stations
