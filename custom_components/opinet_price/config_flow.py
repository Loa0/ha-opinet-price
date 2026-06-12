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
            # 주행거리순 선택 시 Tmap API 키 필수
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
                min=0.5,
                max=20,
                step=0.1,
                unit_of_measurement="km",
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

        show_distance_selector = selector.BooleanSelector()

        sort_selector = selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=SORT_OPTIONS,
                mode=selector.SelectSelectorMode.DROPDOWN,
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

        default_radius = self.config_entry.options.get(
            CONF_RADIUS,
            self.config_entry.data.get(CONF_RADIUS, 5.0)
        )
        # 하위 호환: 기존 m 단위(>100)를 km로 변환
        if isinstance(default_radius, (int, float)) and default_radius > 100:
            default_radius = default_radius / 1000.0

        default_show_distance = self.config_entry.options.get(
            CONF_MAX_DISTANCE,
            self.config_entry.data.get(CONF_MAX_DISTANCE, True)
        )

        default_sort = self.config_entry.options.get(
            CONF_SORT_ORDER,
            self.config_entry.data.get(CONF_SORT_ORDER, "가격순")
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    CONF_RADIUS,
                    default=default_radius
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
                    default=default_show_distance
                ): show_distance_selector,
                vol.Optional(
                    CONF_SORT_ORDER,
                    default=default_sort
                ): sort_selector,
                vol.Optional(
                    CONF_TMAP_KEY,
                    default=self.config_entry.options.get(
                        CONF_TMAP_KEY,
                        self.config_entry.data.get(CONF_TMAP_KEY, "")
                    )
                ): str,
            })
        )



