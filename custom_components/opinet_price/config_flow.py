import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_RADIUS,
    CONF_PRODCD,
    CONF_LOCATION_ENTITY,
    CONF_POLL_DIV,
    CONF_SELF_ONLY,
    CONF_HIGHWAY_FILTER,
    CONF_MAX_DISTANCE,
    CONF_TMAP_KEY,
    CONF_SORT_ORDER,
    CONF_FAVORITES,
    PROD_CODES,
    BRAND_CODES,
    HIGHWAY_OPTIONS,
    SORT_OPTIONS,
)

class OpinetPriceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input.get(CONF_SORT_ORDER) == "주행거리순" and not user_input.get(CONF_TMAP_KEY, "").strip():
                errors[CONF_TMAP_KEY] = "주행거리순 선택 시 Tmap API 키가 필요합니다"
            if not errors:
                return self.async_create_entry(title="오피넷 주유소", data=user_input)

        sort_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=SORT_OPTIONS,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
                vol.Required(CONF_SORT_ORDER, default="가격순"): sort_selector,
                vol.Optional(CONF_TMAP_KEY, default=""): str,
                vol.Optional(CONF_PRODCD, default="B027"): vol.In(PROD_CODES),
                vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["device_tracker", "person"])
                ),
            }),
            errors=errors,
        )


    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OpinetPriceOptionsFlowHandler()

class OpinetPriceOptionsFlowHandler(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        """Step 1: filters."""
        if user_input is not None:
            self._filter_data = user_input
            return await self.async_step_favorites()

        brand_options = [{"value": code, "label": name} for code, name in BRAND_CODES.items()]
        brand_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=brand_options,
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )
        radius_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0.5, max=20, step=0.1,
                unit_of_measurement="km", mode=selector.NumberSelectorMode.SLIDER)
        )
        self_only_selector = selector.BooleanSelector()
        highway_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(options=HIGHWAY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
        )
        show_distance_selector = selector.BooleanSelector()
        sort_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(options=SORT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
        )

        current_value = self.config_entry.options.get(
            CONF_POLL_DIV, self.config_entry.data.get(CONF_POLL_DIV,
                self.config_entry.options.get("poll_div", self.config_entry.data.get("poll_div")))
        )
        if isinstance(current_value, str) and current_value:
            default_brands = [b.strip() for b in current_value.split(",") if b.strip()]
        elif isinstance(current_value, list):
            default_brands = current_value
        else:
            default_brands = []

        defaults = lambda key, fallback: self.config_entry.options.get(key, self.config_entry.data.get(key, fallback))

        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Optional(CONF_RADIUS, default=defaults(CONF_RADIUS, 5.0)): radius_selector,
            vol.Optional(CONF_POLL_DIV, default=default_brands): brand_selector,
            vol.Optional(CONF_SELF_ONLY, default=defaults(CONF_SELF_ONLY, False)): self_only_selector,
            vol.Optional(CONF_HIGHWAY_FILTER, default=defaults(CONF_HIGHWAY_FILTER, "전체")): highway_selector,
            vol.Optional(CONF_MAX_DISTANCE, default=defaults(CONF_MAX_DISTANCE, True)): show_distance_selector,
            vol.Optional(CONF_SORT_ORDER, default=defaults(CONF_SORT_ORDER, "가격순")): sort_selector,
            vol.Optional(CONF_TMAP_KEY, default=defaults(CONF_TMAP_KEY, "")): str,
        }))

    async def async_step_favorites(self, user_input=None):
        """Step 2: favorites selection."""
        if user_input is not None:
            data = dict(self._filter_data)
            data[CONF_FAVORITES] = user_input.get(CONF_FAVORITES, [])
            return self.async_create_entry(title="", data=data)

        stations = []
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        if coordinator and coordinator.data:
            stations = coordinator.data

        if not stations:
            return self.async_show_form(step_id="favorites", data_schema=vol.Schema({}),
                description_placeholder={"info": "데이터가 없습니다. 센서 데이터 수집 후 다시 시도하세요."})

        fav_options = []
        for s in sorted(stations, key=lambda s: int(s.get("PRICE", 999999))):
            uni_id = s.get("UNI_ID")
            if not uni_id:
                continue
            fav_options.append({"value": uni_id, "label": f"{s['OS_NM']}: {int(s['PRICE']):,}원"})

        default_favs = self.config_entry.options.get(CONF_FAVORITES, [])

        return self.async_show_form(step_id="favorites", data_schema=vol.Schema({
            vol.Optional(CONF_FAVORITES, default=default_favs): selector.SelectSelector(
                selector.SelectSelectorConfig(options=fav_options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN))
        }))
