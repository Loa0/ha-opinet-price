import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        _LOGGER.error("Coordinator not found for %s", entry.entry_id)
        return
    async_add_entities([OpinetRefreshButton(coordinator, entry)])


class OpinetRefreshButton(CoordinatorEntity, ButtonEntity):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"opinet_price_refresh_{entry.entry_id}"
        self._attr_name = "유가 정보 갱신"
        self._attr_icon = "mdi:refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="오피넷 주유소",
            manufacturer="Opinet",
            model="주유소 가격 비교",
        )

    async def async_press(self):
        await self.coordinator.async_request_refresh()
