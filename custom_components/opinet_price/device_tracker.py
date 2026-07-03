"""Device tracker for Opinet gas stations."""
import logging

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_FAVORITES
from .sensor import katec_to_wgs84

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    stations = coordinator.data or []

    entities = []
    # 1~10위
    for i, station in enumerate(stations[:10]):
        lat, lon = katec_to_wgs84(station.get("GIS_X_COOR"), station.get("GIS_Y_COOR"))
        if lat is not None and lon is not None:
            entities.append(OpinetDeviceTracker(coordinator, entry, i, lat, lon))

    # 즐겨찾기
    favorites = entry.options.get(CONF_FAVORITES, [])
    for fav_id in favorites:
        for s in stations:
            if s.get("UNI_ID") == fav_id:
                lat, lon = katec_to_wgs84(s.get("GIS_X_COOR"), s.get("GIS_Y_COOR"))
                if lat is not None and lon is not None:
                    entities.append(OpinetDeviceTracker(coordinator, entry, 0, lat, lon, uni_id=fav_id))
                break

    _LOGGER.debug("Added %d device_tracker entities", len(entities))
    async_add_entities(entities)


class OpinetDeviceTracker(CoordinatorEntity, TrackerEntity):
    def __init__(self, coordinator, entry, index, lat, lon, uni_id=None):
        super().__init__(coordinator)
        self._index = index
        self._uni_id = uni_id
        self._lat = lat
        self._lon = lon
        if uni_id:
            self._attr_unique_id = f"opinet_price_tracker_{entry.entry_id}_fav_{uni_id}"
        else:
            self._attr_unique_id = f"opinet_price_tracker_{entry.entry_id}_{index}"
        self._attr_icon = "mdi:gas-station"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="오피넷 주유소",
            manufacturer="Opinet",
            model="주유소 가격 비교",
        )

    def _get_station(self):
        stations = self.coordinator.data
        if not stations:
            return None
        if self._uni_id:
            for s in stations:
                if s.get("UNI_ID") == self._uni_id:
                    return s
            return None
        if self._index >= len(stations):
            return None
        return stations[self._index]

    @property
    def name(self):
        s = self._get_station()
        if not s:
            return f"주유소 #{self._index}" if not self._uni_id else "즐겨찾기"
        if self._uni_id:
            return f"★ {s['OS_NM']}"
        return f"{self._index + 1}위"

    @property
    def extra_state_attributes(self):
        s = self._get_station()
        if not s:
            return {}
        tmap_dist = s.get("_TMAP_DISTANCE")
        dist_str = f"{float(tmap_dist) / 1000:.1f} km" if tmap_dist is not None else f"{float(s.get('DISTANCE', 0)) / 1000:.1f} km"
        attrs = {
            "상호명": s["OS_NM"],
            "가격": int(s["PRICE"]),
            "가격표시": f"{int(s['PRICE']):,}원",
            "주소": s.get("_GEO_ADDR") or s.get("_TMAP_ADDRESS") or s.get("VAN_ADR", ""),
            "브랜드": s["POLL_DIV_CD"],
            "거리": dist_str,
        }
        if not self._uni_id:
            attrs["순위"] = self._index + 1
        return attrs

    @property
    def latitude(self):
        s = self._get_station()
        if s:
            # GeoAPI 좌표 우선, 없으면 KATEC 변환
            if "_GEO_LAT" in s and "_GEO_LNG" in s:
                return s["_GEO_LAT"]
            lat, _ = katec_to_wgs84(s.get("GIS_X_COOR"), s.get("GIS_Y_COOR"))
            return lat
        return self._lat

    @property
    def longitude(self):
        s = self._get_station()
        if s:
            if "_GEO_LAT" in s and "_GEO_LNG" in s:
                return s["_GEO_LNG"]
            _, lon = katec_to_wgs84(s.get("GIS_X_COOR"), s.get("GIS_Y_COOR"))
            return lon
        return self._lon

    @property
    def source_type(self):
        return SourceType.GPS
