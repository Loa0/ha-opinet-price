"""Opinet 주유소 센서 — 표시 로직 전용"""

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_API_KEY, CONF_RADIUS, CONF_PRODCD, CONF_LOCATION_ENTITY, \
    CONF_POLL_DIV, CONF_SELF_ONLY, CONF_HIGHWAY_FILTER, CONF_MAX_DISTANCE, \
    CONF_TMAP_KEY, CONF_SORT_ORDER, CONF_FAVORITES, FAV_LABELS, CONF_VWORLD_KEY, \
    CONF_RANK_COUNT
from .coordinator import OpinetDataUpdateCoordinator
from ._coord_util import _get_price

_LOGGER = logging.getLogger(__name__)


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
    rank_count = entry.options.get(CONF_RANK_COUNT, 10)
    if not isinstance(rank_count, int):
        rank_count = int(rank_count)

    _LOGGER.debug(
        "Setting up Opinet Price entry. options: %s, data: %s, poll_div: %s, "
        "self_only: %s, highway_filter: %s, sort: %s",
        entry.options, entry.data, poll_div, self_only, highway_filter, sort_order,
    )

    coordinator = OpinetDataUpdateCoordinator(
        hass, entry, api_key, radius, prodcd, location_entity,
        poll_div, self_only, highway_filter, tmap_key, sort_order, vworld_key,
        rank_count=rank_count,
    )
    await coordinator.async_config_entry_first_refresh()

    sensors = []
    for i in range(rank_count):
        sensors.append(OpinetStationSensor(coordinator, entry, i, location_entity, show_distance))

    favorites = entry.options.get(CONF_FAVORITES, [])
    fav_labels = entry.options.get(FAV_LABELS, {})
    for i, uni_id in enumerate(favorites):
        lbl = fav_labels.get(uni_id, {})
        fav_label = lbl.get("name", "") or ""
        sensors.append(OpinetStationSensor(
            coordinator, entry, i, location_entity, show_distance,
            uni_id=uni_id, fav_label=fav_label,
        ))

    async_add_entities(sensors)
    async_add_entities([OpinetApiUsageSensor(coordinator, entry)])

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator


class OpinetStationSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, entry, index, location_entity, show_distance=True,
                 uni_id=None, fav_label=""):
        super().__init__(coordinator)
        self._index = index
        self._uni_id = uni_id
        self._location_entity = location_entity
        self._show_distance = show_distance
        self._fav_label = fav_label
        if uni_id:
            self._attr_unique_id = f"opinet_price_{entry.entry_id}_fav_{uni_id}"
        else:
            self._attr_unique_id = f"opinet_price_{entry.entry_id}_{index + 1}"
        self._attr_icon = "mdi:gas-station"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="오피넷 주유소",
            manufacturer="Opinet",
            model="주유소 가격 비교",
        )

    @property
    def name(self):
        if self._uni_id and self._fav_label:
            return self._fav_label
        if self._uni_id:
            s = self._get_station()
            return s["OS_NM"] if s else "즐겨찾기"
        return f"{self._index + 1}위"

    def _get_station(self):
        stations = self.coordinator.data
        fav_stations = getattr(self.coordinator, 'fav_data', [])
        if not stations:
            return None
        if self._uni_id:
            for s in stations:
                if s.get("UNI_ID") == self._uni_id:
                    return s
            for s in fav_stations:
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
            base = f"{s['OS_NM']}:\n{_get_price(s):,}원"
            if self._show_distance:
                tmap_dist = s.get("_TMAP_DISTANCE")
                if tmap_dist is not None:
                    base += f" ({float(tmap_dist) / 1000:.1f}km)"
                elif s.get("_IS_FAV_ONLY"):
                    pass
                else:
                    dist = s.get("DISTANCE", 0)
                    if dist:
                        base += f" ({float(dist) / 1000:.1f}km)"
            return base
        return "검색 결과 없음"

    @property
    def extra_state_attributes(self):
        s = self._get_station()
        if s:
            full_addr = s.get("_GEO_ADDR") or s.get("_TMAP_ADDRESS") or s.get("VAN_ADR") or ""
            if not full_addr:
                full_addr = f"{s['OS_NM']} (주소 정보 없음)"
            short_addr = s.get("_TMAP_SHORT_ADDR") or ""
            if not short_addr:
                parts = full_addr.split(" ", 1)
                short_addr = parts[1] if len(parts) > 1 and parts[1] else full_addr
            tmap_dist = s.get("_TMAP_DISTANCE")
            if tmap_dist is not None:
                dist_str = f"{float(tmap_dist) / 1000:.1f} km"
            elif s.get("_IS_FAV_ONLY"):
                dist_str = "권외"
            else:
                dist_str = f"{float(s.get('DISTANCE', 0)) / 1000:.1f} km"
            attrs = {
                "주유소명": s["OS_NM"],
                "가격": _get_price(s),
                "주소": full_addr,
                "간략주소": short_addr,
                "브랜드": s.get("POLL_DIV_CD", ""),
                "거리": dist_str,
                "위도": s.get("_GEO_LAT", ""),
                "경도": s.get("_GEO_LNG", ""),
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
        c = self.coordinator
        parts = [f"오피넷 {c.opinet_call_count}회"]
        if c.opinet_errors:
            parts.append(f"⚠{c.opinet_errors}")
        parts.append(f"| VWorld {c.vworld_call_count}회")
        if c.vworld_errors:
            parts.append(f"⚠{c.vworld_errors}")
        parts.append(f"| Tmap {c.tmap_call_count}회")
        if c.tmap_errors:
            parts.append(f"⚠{c.tmap_errors}")

        # 할당량 경고
        warnings = []
        if c.opinet_call_count > 400:
            warnings.append("Opinet 500회 임박")
        if c.tmap_call_count > 90000:
            warnings.append("Tmap 10만회 임박")
        if c.opinet_errors > 10:
            warnings.append(f"Opinet 에러 {c.opinet_errors}회")
        if c.tmap_errors > 10:
            warnings.append(f"Tmap 에러 {c.tmap_errors}회")

        base = " ".join(parts)
        if warnings:
            base += f"\n⚠️ {'/'.join(warnings)}"
        return base

    @property
    def extra_state_attributes(self):
        c = self.coordinator
        return {
            "opinet_calls": c.opinet_call_count,
            "opinet_errors": c.opinet_errors,
            "vworld_calls": c.vworld_call_count,
            "vworld_errors": c.vworld_errors,
            "tmap_calls": c.tmap_call_count,
            "tmap_errors": c.tmap_errors,
        }
