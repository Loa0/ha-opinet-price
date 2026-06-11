import logging
import math
import async_timeout
import json
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity, UpdateFailed

from .const import DOMAIN, CONF_API_KEY, CONF_RADIUS, CONF_PRODCD, CONF_LOCATION_ENTITY, PROD_CODES

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    api_key = entry.data.get(CONF_API_KEY)
    radius = entry.data.get(CONF_RADIUS, 5000)
    prodcd = entry.data.get(CONF_PRODCD, "B027")
    location_entity = entry.data.get(CONF_LOCATION_ENTITY)

    coordinator = OpinetDataUpdateCoordinator(hass, api_key, radius, prodcd, location_entity)
    await coordinator.async_config_entry_first_refresh()

    # 상위 10개 주유소에 대한 개별 센서 생성
    sensors = []
    for i in range(10):
        sensors.append(OpinetStationSensor(coordinator, i, location_entity))
    
    # 통합 센서 추가: 10개 주유소 정보를 하나의 엔티티로 제공
    sensors.append(OpinetCombinedSensor(coordinator, location_entity, radius, prodcd))
    
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
    def __init__(self, hass, api_key, radius, prodcd, location_entity):
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=3),
        )
        self.api_key = api_key
        self.radius = radius
        self.prodcd = prodcd
        self.location_entity = location_entity
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
        
        url = f"https://www.opinet.co.kr/api/aroundAll.do?code={self.api_key}&x={kx_int}&y={ky_int}&radius={self.radius}&prodcd={self.prodcd}&sort=1&out=json"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

        try:
            async with async_timeout.timeout(15):
                session = async_get_clientsession(self.hass)
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        raise UpdateFailed(f"API Error: {response.status}")
                    
                    body = (await response.text()).strip()
                    res = json.loads(body)
                    stations = res.get("RESULT", {}).get("OIL", [])
                    if stations:
                        stations.sort(key=lambda x: int(x["PRICE"]))
                        return stations
                    return []
        except Exception as e:
            raise UpdateFailed(f"Error communicating with API: {e}")

class OpinetStationSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, index, location_entity):
        super().__init__(coordinator)
        self._index = index
        self._location_entity = location_entity
        self._attr_unique_id = f"opinet_price_{self._location_entity or 'home'}_{index + 1}"
        self._attr_icon = "mdi:gas-station"

    @property
    def name(self):
        return f"{self._index + 1}위"

    @property
    def state(self):
        stations = self.coordinator.data
        if stations and len(stations) > self._index:
            s = stations[self._index]
            return f"{s['OS_NM']}: {int(s['PRICE']):,}원"
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

class OpinetCombinedSensor(CoordinatorEntity, SensorEntity):
    """하나의 통합 엔티티로 10개 주유소 정보를 모두 제공하는 센서"""

    def __init__(self, coordinator, location_entity, radius, prodcd):
        """통합 센서 초기화.

        Args:
            coordinator: OpinetDataUpdateCoordinator 인스턴스
            location_entity: 위치 추적 엔티티 ID
            radius: 검색 반경 (미터)
            prodcd: 유종 코드 (예: B027=휘발유)
        """
        super().__init__(coordinator)
        self._location_entity = location_entity
        self._radius = radius
        self._prodcd = prodcd
        # 고유 ID에 'combined'를 포함하여 개별 센서와 구분
        self._attr_unique_id = f"opinet_price_{self._location_entity or 'home'}_combined"
        self._attr_icon = "mdi:gas-station"
        self._attr_name = "오피넷 주유소 목록"

    @property
    def state(self):
        """가장 저렴한 주유소 정보를 state로 표시"""
        stations = self.coordinator.data
        if stations and len(stations) > 0:
            cheapest = stations[0]  # 가격순 정렬되어 있으므로 첫 번째가 최저가
            return f"최저가: {cheapest['OS_NM']} {int(cheapest['PRICE']):,}원"
        return "주유소 정보 없음"

    @property
    def extra_state_attributes(self):
        """10개 주유소 목록과 검색 기준 정보를 attributes로 제공"""
        stations = self.coordinator.data
        if not stations:
            return {
                "주유소목록": [],
                "최저가주유소": None,
                "최저가격": None,
                "검색기준": {
                    "위치": self._location_entity or "홈",
                    "반경": f"{int(self._radius / 1000)}km",
                    "유종": PROD_CODES.get(self._prodcd, self._prodcd)
                }
            }

        # 주유소 목록 구성 (각 주유소 정보를 딕셔너리로 변환)
        juyuso_list = []
        for idx, s in enumerate(stations[:10]):
            full_addr = s.get("VAN_ADR", "주소 정보 없음")
            # 간략 주소: 첫 번째 공백 기준 앞부분(시/도)을 제외한 나머지
            parts = full_addr.split(" ", 1)
            short_addr = parts[1] if len(parts) > 1 and len(parts[1]) > 0 else full_addr
            try:
                distance_km = f"{float(s['DISTANCE']) / 1000:.1f} km"
            except (ValueError, KeyError):
                distance_km = "정보 없음"

            juyuso_list.append({
                "순위": idx + 1,
                "주유소명": s["OS_NM"],
                "가격": int(s["PRICE"]),
                "주소": full_addr,
                "간략주소": short_addr,
                "브랜드": s.get("POLL_DIV_CD", "정보 없음"),
                "거리": distance_km
            })

        cheapest = stations[0]

        return {
            "주유소목록": juyuso_list,
            "최저가주유소": cheapest["OS_NM"],
            "최저가격": int(cheapest["PRICE"]),
            "검색기준": {
                "위치": self._location_entity or "홈",
                "반경": f"{int(self._radius / 1000)}km",
                "유종": PROD_CODES.get(self._prodcd, self._prodcd)
            }
        }
