import logging
import math
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_PRODCD,
    CONF_LOCATION_ENTITY,
    CONF_POLL_DIV,
    CONF_RADIUS,
    CONF_SELF_ONLY,
    CONF_HIGHWAY_FILTER,
    CONF_MAX_DISTANCE,
    CONF_TMAP_KEY,
    CONF_SORT_ORDER,
    CONF_REFRESH_DISTANCE,
    CONF_REFRESH_ENABLED,
    CONF_VWORLD_KEY,
)

_LOGGER = logging.getLogger(__name__)

# ponytail: haversine in km
def _haversine_km(lat1, lon1, lat2, lon2):
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to the latest version, filling in missing options."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version > 1:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    updated = False

    # data defaults
    if CONF_SORT_ORDER not in data:
        data[CONF_SORT_ORDER] = "가격순"
        updated = True
    if CONF_TMAP_KEY not in data:
        data[CONF_TMAP_KEY] = ""
        updated = True

    # options defaults
    if CONF_SELF_ONLY not in options:
        options[CONF_SELF_ONLY] = False
        updated = True
    if CONF_HIGHWAY_FILTER not in options:
        options[CONF_HIGHWAY_FILTER] = "전체"
        updated = True
    if CONF_MAX_DISTANCE not in options:
        options[CONF_MAX_DISTANCE] = True
        updated = True
    if CONF_SORT_ORDER not in options:
        options[CONF_SORT_ORDER] = "가격순"
        updated = True
    if CONF_TMAP_KEY not in options:
        options[CONF_TMAP_KEY] = ""
        updated = True
    if CONF_VWORLD_KEY not in data:
        data[CONF_VWORLD_KEY] = ""
        updated = True
    if CONF_VWORLD_KEY not in options:
        options[CONF_VWORLD_KEY] = ""
        updated = True

    if updated:
        hass.config_entries.async_update_entry(
            entry, data=data, options=options
        )

    _LOGGER.debug("Migration complete for entry, version now %s", entry.version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        # sensor 먼저 로드 → coordinator 생성 후 button/device_tracker 로드
        try:
            await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
        except ConfigEntryNotReady:
            raise  # 그대로 전파
        except Exception as e:
            _LOGGER.error("Sensor setup failed: %s", e, exc_info=True)
            raise ConfigEntryNotReady(f"Sensor setup failed: {e}")

        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is None:
            raise ConfigEntryNotReady("Sensor coordinator not initialized")

        await hass.config_entries.async_forward_entry_setups(entry, ["button", "device_tracker"])

        # ponytail: init last_refresh_location from current state
        coordinator.last_refresh_location = _get_current_location(hass, entry)

        refresh_distance = entry.options.get(CONF_REFRESH_DISTANCE, 10)
        refresh_enabled = entry.options.get(CONF_REFRESH_ENABLED, True)

        # 1. 갱신 시간 크론: 1,2,9,12,16,19시 KST
        async def _refresh_on_schedule(now):
            kst_now = dt_util.as_local(now)
            if kst_now.minute == 0 and kst_now.hour in (1, 2, 9, 12, 16, 19):
                _LOGGER.debug("Scheduled refresh at %s KST", kst_now)
                await coordinator.async_refresh()
                coordinator.last_refresh_location = _get_current_location(hass, entry)

        # 2. 카운터 리셋
        async def _reset_counters(now):
            kst_now = dt_util.as_local(now)
            if kst_now.hour == 0 and kst_now.minute == 0:
                coordinator.opinet_call_count = 0
                if kst_now.day == 1:
                    coordinator.tmap_call_count = 0

        schedule_unsub = async_track_time_interval(hass, _refresh_on_schedule, timedelta(minutes=1))
        reset_unsub = async_track_time_interval(hass, _reset_counters, timedelta(minutes=1))

        # 3. 이동 감지
        location_entity = entry.data.get(CONF_LOCATION_ENTITY)
        move_unsub = None

        async def _on_location_change(event):
            rd = entry.options.get(CONF_REFRESH_DISTANCE, 10)
            re = entry.options.get(CONF_REFRESH_ENABLED, True)
            if not re:
                return
            new_loc = _get_current_location(hass, entry)
            if new_loc is None or coordinator.last_refresh_location is None:
                return
            dist = _haversine_km(coordinator.last_refresh_location[0], coordinator.last_refresh_location[1],
                                 new_loc[0], new_loc[1])
            if dist >= rd:
                _LOGGER.debug("Movement detected: %.1f km >= %d km, refreshing", dist, rd)
                await coordinator.async_refresh()
                coordinator.last_refresh_location = new_loc

        if location_entity:
            move_unsub = async_track_state_change_event(hass, [location_entity], _on_location_change)

        # cleanup
        async def _cleanup():
            schedule_unsub()
            reset_unsub()
            if move_unsub:
                move_unsub()

        entry.async_on_unload(entry.add_update_listener(
            lambda h, e: update_listener(h, e)))
        entry.async_on_unload(_cleanup)

        return True
    except ConfigEntryNotReady as err:
        _LOGGER.error("Config entry not ready for %s: %s", entry.title, err, exc_info=True)
        raise
    except Exception as err:
        _LOGGER.error("Failed to setup entry %s: %s", entry.title, err, exc_info=True)
        raise


def _get_current_location(hass: HomeAssistant, entry: ConfigEntry):
    """Get (lat, lon) from location entity or HA config."""
    location_entity = entry.data.get(CONF_LOCATION_ENTITY)
    if location_entity:
        loc = hass.states.get(location_entity)
        if loc:
            if "Location" in loc.attributes and isinstance(loc.attributes["Location"], list):
                return loc.attributes["Location"][0], loc.attributes["Location"][1]
            elif "latitude" in loc.attributes:
                return loc.attributes["latitude"], loc.attributes["longitude"]
            elif "lat" in loc.attributes:
                return loc.attributes["lat"], loc.attributes["lon"]
    return hass.config.latitude, hass.config.longitude


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — update coordinator settings then reload."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        coordinator.refresh_distance = entry.options.get(CONF_REFRESH_DISTANCE, 10)
        coordinator.refresh_enabled = entry.options.get(CONF_REFRESH_ENABLED, True)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "device_tracker"])
