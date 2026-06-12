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
    PROD_CODES,
    BRAND_CODES,
    HIGHWAY_OPTIONS,
)

class OpinetPriceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            return self.async_create_entry(title="오피넷 주유소", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_API_KEY): str,
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        brand_options = [{"value": code, "label": name} for code, name in BRAND_CODES.items()]
        brand_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=brand_options,
                multiple=True,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        radius_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=500,
                max=20000,
                step=100,
                unit_of_measurement="m",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        )

        self_only_selector = selector.BooleanSelector()

        highway_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=HIGHWAY_OPTIONS,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        )

        distance_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.5,
                max=20.0,
                step=0.1,
                unit_of_measurement="km",
                mode=selector.NumberSelectorMode.SLIDER,
            )
        )

        current_value = self.config_entry.options.get(
            CONF_POLL_DIV,
            self.config_entry.data.get(
                CONF_POLL_DIV,
                self.config_entry.options.get(
                    "poll_div",
                    self.config_entry.data.get("poll_div")
                )
            )
        )
        if isinstance(current_value, str) and current_value:
            default_value = [b.strip() for b in current_value.split(",") if b.strip()]
        elif isinstance(current_value, list):
            default_value = current_value
        else:
            default_value = []

        default_self_only = self.config_entry.options.get(
            CONF_SELF_ONLY,
            self.config_entry.data.get(CONF_SELF_ONLY, False)
        )

        default_highway = self.config_entry.options.get(
            CONF_HIGHWAY_FILTER,
            self.config_entry.data.get(CONF_HIGHWAY_FILTER, "전체")
        )

        default_max_distance = self.config_entry.options.get(
            CONF_MAX_DISTANCE,
            self.config_entry.data.get(CONF_MAX_DISTANCE, self.config_entry.options.get(
                CONF_RADIUS,
                self.config_entry.data.get(CONF_RADIUS, 5000)
            ) / 1000.0)
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_RADIUS,
                    default=self.config_entry.options.get(
                        CONF_RADIUS,
                        self.config_entry.data.get(CONF_RADIUS, 5000)
                    )
                ): radius_selector,
                vol.Optional(
                    CONF_POLL_DIV,
                    default=default_value
                ): brand_selector,
                vol.Optional(
                    CONF_SELF_ONLY,
                    default=default_self_only
                ): self_only_selector,
                vol.Optional(
                    CONF_HIGHWAY_FILTER,
                    default=default_highway
                ): highway_selector,
                vol.Optional(
                    CONF_MAX_DISTANCE,
                    default=default_max_distance
                ): distance_selector,
            })
        )



