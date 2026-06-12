import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
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
    return await hass.config_entries.async_unload_platforms(entry, ["sensor"])

