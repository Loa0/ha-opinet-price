"""Device tracker for Opinet gas stations."""
import logging

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .sensor import katec_to_wgs84

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    stations = coordinator.data or []

    entities = []
    for i, station in enumerate(stations):
        lat, lon = katec_to_wgs84(station.get("GIS_X_COOR"), station.get("GIS_Y_COOR"))
        if lat is not None and lon is not None:
            entities.append(OpinetDeviceTracker(coordinator, entry, i, lat, lon))

    _LOGGER.debug("Added %d device_tracker entities", len(entities))
    async_add_entities(entities)


class OpinetDeviceTracker(CoordinatorEntity, TrackerEntity):
    def __init__(self, coordinator, entry, index, lat, lon):
        super().__init__(coordinator)
        self._index = index
        self._lat = lat
        self._lon = lon
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
        if not stations or self._index >= len(stations):
            return None
        return stations[self._index]

    @property
    def name(self):
        s = self._get_station()
        return f"{self._index + 1}위" if s else f"주유소 #{self._index}"

    @property
    def extra_state_attributes(self):
        s = self._get_station()
        if not s:
            return {}
        tmap_dist = s.get("_TMAP_DISTANCE")
        dist_str = f"{float(tmap_dist) / 1000:.1f} km" if tmap_dist is not None else f"{float(s.get('DISTANCE', 0)) / 1000:.1f} km"
        return {
            "상호명": s["OS_NM"],
            "가격": int(s["PRICE"]),
            "가격표시": f"{int(s['PRICE']):,}원",
            "주소": s.get("_TMAP_ADDRESS") or s.get("VAN_ADR", ""),
            "브랜드": s["POLL_DIV_CD"],
            "거리": dist_str,
        }

    @property
    def latitude(self):
        s = self._get_station()
        if s:
            lat, _ = katec_to_wgs84(s.get("GIS_X_COOR"), s.get("GIS_Y_COOR"))
            return lat
        return self._lat

    @property
    def longitude(self):
        s = self._get_station()
        if s:
            _, lon = katec_to_wgs84(s.get("GIS_X_COOR"), s.get("GIS_Y_COOR"))
            return lon
        return self._lon

    @property
    def source_type(self):
        return SourceType.GPS
