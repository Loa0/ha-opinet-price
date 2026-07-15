"""Opinet Price 통합구성요소 — 진입점"""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    DOMAIN, CONF_API_KEY, CONF_PRODCD, CONF_LOCATION_ENTITY,
    CONF_POLL_DIV, CONF_RADIUS, CONF_SELF_ONLY, CONF_HIGHWAY_FILTER,
    CONF_MAX_DISTANCE, CONF_TMAP_KEY, CONF_SORT_ORDER, CONF_VWORLD_KEY,
    CONF_REFRESH_DISTANCE, CONF_REFRESH_ENABLED,
)
from .refresh_manager import RefreshManager

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entry to the latest version."""
    _LOGGER.debug("Migrating config entry from version %s", entry.version)

    if entry.version > 1:
        return True

    data = dict(entry.data)
    options = dict(entry.options)
    updated = False

    defaults_data = {
        CONF_SORT_ORDER: "가격순",
        CONF_TMAP_KEY: "",
        CONF_VWORLD_KEY: "",
    }
    defaults_options = {
        CONF_SELF_ONLY: False,
        CONF_HIGHWAY_FILTER: "전체",
        CONF_MAX_DISTANCE: True,
        CONF_SORT_ORDER: "가격순",
        CONF_TMAP_KEY: "",
        CONF_VWORLD_KEY: "",
    }

    for key, val in defaults_data.items():
        if key not in data:
            data[key] = val
            updated = True
    for key, val in defaults_options.items():
        if key not in options:
            options[key] = val
            updated = True

    if updated:
        hass.config_entries.async_update_entry(entry, data=data, options=options)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """설정 진입 — sensor/button/device_tracker 로드 + RefreshManager 시작"""
    try:
        try:
            await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
        except ConfigEntryNotReady:
            raise
        except Exception as e:
            _LOGGER.error("Sensor setup failed: %s", e, exc_info=True)
            raise ConfigEntryNotReady(f"Sensor setup failed: {e}") from e

        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is None:
            raise ConfigEntryNotReady("Sensor coordinator not initialized")

        await hass.config_entries.async_forward_entry_setups(entry, ["button", "device_tracker"])

        # RefreshManager: 스케줄 갱신 + 카운터 리셋 + 이동 감지
        refresh_mgr = RefreshManager(hass, entry, coordinator)
        hass.data.setdefault(DOMAIN, {})["_refresh_mgr"] = refresh_mgr

        entry.async_on_unload(
            entry.add_update_listener(lambda h, e: update_listener(h, e))
        )
        entry.async_on_unload(refresh_mgr.cleanup)

        return True
    except ConfigEntryNotReady as err:
        _LOGGER.error("Config entry not ready for %s: %s", entry.title, err, exc_info=True)
        raise
    except Exception as err:
        _LOGGER.error("Failed to setup entry %s: %s", entry.title, err, exc_info=True)
        raise


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """옵션 변경 → coordinator 반영 후 리로드"""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator:
        coordinator.refresh_distance = entry.options.get(CONF_REFRESH_DISTANCE, 10)
        coordinator.refresh_enabled = entry.options.get(CONF_REFRESH_ENABLED, True)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(
        entry, ["sensor", "button", "device_tracker"]
    )
