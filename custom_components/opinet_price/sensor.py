import logging
import math
import async_timeout
from datetime import timedelta

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_API_KEY, CONF_RADIUS, CONF_PRODCD, CONF_LOCATION_ENTITY

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(hours=3)

async def async_setup_entry(hass, entry, async_add_entities):
    api_key = entry.data.get(CONF_API_KEY)
    radius = entry.data.get(CONF_RADIUS, 5000)
    prodcd = entry.data.get(CONF_PRODCD, "B027")
    location_entity = entry.data.get(CONF_LOCATION_ENTITY)

    async_add_entities([OpinetCheapestSensor(api_key, radius, prodcd, location_entity)], True)

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

class OpinetCheapestSensor(SensorEntity):
    def __init__(self, api_key, radius, prodcd, location_entity):
        self._api_key = api_key
        self._radius = radius
        self._prodcd = prodcd
        self._location_entity = location_entity
        self._state = None
        self._attr = {
            "station_name": "검색 중...",
            "address": "검색 중...",
            "nearby_stations": []
        }
        self._converter = KatecConverter()
        self._name = "오피넷 최저가 주유소"

    @property
    def name(self):
        return self._name

    @property
    def unique_id(self):
        return f"opinet_cheapest_{self._location_entity or 'home'}"

    @property
    def state(self):
        return self._state

    @property
    def extra_state_attributes(self):
        return self._attr

    @property
    def unit_of_measurement(self):
        return "원"

    @property
    def icon(self):
        return "mdi:gas-station"

    async def async_update(self):
        lat, lon = self.hass.config.latitude, self.hass.config.longitude
        if self._location_entity:
            loc = self.hass.states.get(self._location_entity)
            if loc:
                if "Location" in loc.attributes and isinstance(loc.attributes["Location"], list):
                    lat, lon = loc.attributes["Location"][0], loc.attributes["Location"][1]
                elif "latitude" in loc.attributes:
                    lat, lon = loc.attributes["latitude"], loc.attributes["longitude"]
                elif "lat" in loc.attributes:
                    lat, lon = loc.attributes["lat"], loc.attributes["lon"]
        
        kx, ky = self._converter.wgs84_to_katec(lat, lon)
        url = f"https://www.opinet.co.kr/api/aroundAll.do?code={self._api_key}&x={kx}&y={ky}&radius={self._radius}&prodcd={self._prodcd}&sort=1&out=json"
        
        try:
            async with async_timeout.timeout(10):
                session = async_get_clientsession(self.hass)
                async with session.get(url) as response:
                    res = await response.json()
                    stations = res.get("RESULT", {}).get("OIL", [])
                    if stations:
                        # 이미 Opinet API에서 최저가 순(sort=1)으로 주지만 한 번 더 정렬 확인
                        stations.sort(key=lambda x: int(x["PRICE"]))
                        cheapest = stations[0]
                        self._state = cheapest["PRICE"]
                        self._name = f"최저가: {cheapest['OS_NM']}"
                        self._attr = {
                            "주유소명": cheapest["OS_NM"],
                            "가격": cheapest["PRICE"],
                            "주소": cheapest.get("VAN_ADR", "주소 정보 없음"),
                            "브랜드": cheapest["POLL_DIV_CD"],
                            "거리": f"{float(cheapest['DISTANCE'])/1000:.1f} km",
                            "주변 주유소": [f"{s['OS_NM']}: {s['PRICE']}원 ({float(s['DISTANCE'])/1000:.1f}km)" for s in stations[:5]]
                        }
                    else:
                        self._state = "검색 결과 없음"
                        self._attr = {"주변 주유소": []}
        except Exception as e:
            _LOGGER.error("Error updating Opinet sensor: %s", e)
