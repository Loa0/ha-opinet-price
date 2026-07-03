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
    CONF_REFRESH_DISTANCE,
    CONF_REFRESH_ENABLED,
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
            selector.SelectSelectorConfig(options=SORT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN))

        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required(CONF_API_KEY): str,
            vol.Required(CONF_SORT_ORDER, default="가격순"): sort_selector,
            vol.Optional(CONF_TMAP_KEY, default=""): str,
            vol.Optional(CONF_PRODCD, default="B027"): vol.In(PROD_CODES),
            vol.Optional(CONF_LOCATION_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["device_tracker", "person"])),
        }), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OpinetPriceOptionsFlowHandler()

class OpinetPriceOptionsFlowHandler(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        return self.async_show_menu(
            step_id="init",
            menu_options={"api": "API 키", "filters": "필터 설정", "favorites": "즐겨찾기 관리", "refresh": "갱신 설정"},
        )

    async def async_step_api(self, user_input=None):
        if user_input is not None:
            # 기존 옵션 유지
            user_input.update({k: v for k, v in self.config_entry.options.items()
                              if k not in user_input})
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(step_id="api", data_schema=vol.Schema({
            vol.Required(CONF_API_KEY, default=self.config_entry.options.get(CONF_API_KEY, self.config_entry.data.get(CONF_API_KEY, ""))): str,
            vol.Optional(CONF_TMAP_KEY, default=self.config_entry.options.get(CONF_TMAP_KEY, self.config_entry.data.get(CONF_TMAP_KEY, ""))): str,
        }))

    async def async_step_filters(self, user_input=None):
        if user_input is not None:
            user_input.update({k: v for k, v in self.config_entry.options.items()
                              if k not in user_input})
            return self.async_create_entry(title="", data=user_input)

        brand_options = [{"value": code, "label": name} for code, name in BRAND_CODES.items()]
        opts = self.config_entry.options
        dat = self.config_entry.data

        current_value = opts.get(CONF_POLL_DIV, dat.get(CONF_POLL_DIV,
            opts.get("poll_div", dat.get("poll_div"))))
        default_brands = [b.strip() for b in current_value.split(",") if b.strip()] if isinstance(current_value, str) and current_value else (current_value if isinstance(current_value, list) else [])

        return self.async_show_form(step_id="filters", data_schema=vol.Schema({
            vol.Optional(CONF_RADIUS, default=opts.get(CONF_RADIUS, dat.get(CONF_RADIUS, 5.0))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0.5, max=20, step=0.1, unit_of_measurement="km", mode=selector.NumberSelectorMode.SLIDER)),
            vol.Optional(CONF_POLL_DIV, default=default_brands): selector.SelectSelector(
                selector.SelectSelectorConfig(options=brand_options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_SELF_ONLY, default=opts.get(CONF_SELF_ONLY, dat.get(CONF_SELF_ONLY, False))): selector.BooleanSelector(),
            vol.Optional(CONF_HIGHWAY_FILTER, default=opts.get(CONF_HIGHWAY_FILTER, dat.get(CONF_HIGHWAY_FILTER, "전체"))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=HIGHWAY_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
            vol.Optional(CONF_MAX_DISTANCE, default=opts.get(CONF_MAX_DISTANCE, dat.get(CONF_MAX_DISTANCE, True))): selector.BooleanSelector(),
            vol.Optional(CONF_SORT_ORDER, default=opts.get(CONF_SORT_ORDER, dat.get(CONF_SORT_ORDER, "가격순"))): selector.SelectSelector(
                selector.SelectSelectorConfig(options=SORT_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)),
        }))

    async def async_step_favorites(self, user_input=None):
        if user_input is not None:
            user_input.update({k: v for k, v in self.config_entry.options.items()
                              if k not in user_input})
            return self.async_create_entry(title="", data=user_input)

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

        return self.async_show_form(step_id="favorites", data_schema=vol.Schema({
            vol.Optional(CONF_FAVORITES, default=self.config_entry.options.get(CONF_FAVORITES, [])): selector.SelectSelector(
                selector.SelectSelectorConfig(options=fav_options, multiple=True, mode=selector.SelectSelectorMode.DROPDOWN))
        }))

    async def async_step_refresh(self, user_input=None):
        if user_input is not None:
            user_input.update({k: v for k, v in self.config_entry.options.items()
                              if k not in user_input})
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options
        default_dist = opts.get(CONF_REFRESH_DISTANCE, 10)
        return self.async_show_form(step_id="refresh", data_schema=vol.Schema({
            vol.Optional(CONF_REFRESH_ENABLED, default=opts.get(CONF_REFRESH_ENABLED, True)): selector.BooleanSelector(),
            vol.Optional(CONF_REFRESH_DISTANCE, default=float(default_dist)): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5.0, max=20.0, step=5.0, unit_of_measurement="km", mode=selector.NumberSelectorMode.SLIDER)),
        }), description_placeholder={
            "info": "월 예상 사용량 (하루 200km 기준)\n"
                   "5km  → 오피넷 46회/일 (3.1%) | Tmap 27,600회/월 (92%)\n"
                   "10km → 오피넷 26회/일 (1.7%) | Tmap 15,600회/월 (52%)\n"
                   "15km → 오피넷 18회/일 (1.2%) | Tmap 10,400회/월 (35%)\n"
                   "20km → 오피넷 14회/일 (0.9%) | Tmap 7,800회/월 (26%)\n\n"
                   "오피넷 일 1,500회 | Tmap 월 30,000회"
        })
