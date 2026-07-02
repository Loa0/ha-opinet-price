import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
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
)

_LOGGER = logging.getLogger(__name__)


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

    if updated:
        hass.config_entries.async_update_entry(
            entry, data=data, options=options
        )

    _LOGGER.debug("Migration complete for entry, version now %s", entry.version)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "button", "device_tracker"])
        entry.async_on_unload(entry.add_update_listener(update_listener))
        return True
    except ConfigEntryNotReady as err:
        _LOGGER.error("Config entry not ready for %s: %s", entry.title, err, exc_info=True)
        raise
    except Exception as err:
        _LOGGER.error("Failed to setup entry %s: %s", entry.title, err, exc_info=True)
        raise


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, ["sensor", "button", "device_tracker"])
