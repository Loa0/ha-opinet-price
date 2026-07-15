"""RefreshManager — 스케줄 갱신, 카운터 리셋, 이동 감지"""

import logging
import math
from datetime import timedelta

from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_LOCATION_ENTITY, CONF_REFRESH_DISTANCE, CONF_REFRESH_ENABLED

_LOGGER = logging.getLogger(__name__)


def _haversine_km(lat1, lon1, lat2, lon2):
    """Haversine formula — distance in km"""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(a))


class RefreshManager:
    """스케줄 갱신, 카운터 리셋, 이동 감지 통합 관리"""

    def __init__(self, hass, entry, coordinator):
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator

        # 이동 감지 초기화
        coordinator.last_refresh_location = self._get_current_location()
        self._unsubs = []

        self._setup_scheduled_refresh()
        self._setup_counter_reset()
        self._setup_movement_refresh()

    def _get_current_location(self):
        """Get (lat, lon) from location entity or HA config."""
        location_entity = self.entry.data.get(CONF_LOCATION_ENTITY)
        if location_entity:
            loc = self.hass.states.get(location_entity)
            if loc:
                if "Location" in loc.attributes and isinstance(loc.attributes["Location"], list):
                    return loc.attributes["Location"][0], loc.attributes["Location"][1]
                elif "latitude" in loc.attributes:
                    return loc.attributes["latitude"], loc.attributes["longitude"]
                elif "lat" in loc.attributes:
                    return loc.attributes["lat"], loc.attributes["lon"]
        return self.hass.config.latitude, self.hass.config.longitude

    def _setup_scheduled_refresh(self):
        """매분 체크 → 지정 시각(1,2,9,12,16,19시)에 갱신"""
        async def _refresh_on_schedule(now):
            kst_now = dt_util.as_local(now)
            if kst_now.minute == 0 and kst_now.hour in (1, 2, 9, 12, 16, 19):
                _LOGGER.debug("Scheduled refresh at %s KST", kst_now)
                await self.coordinator.async_refresh()
                self.coordinator.last_refresh_location = self._get_current_location()

        unsub = async_track_time_interval(
            self.hass, _refresh_on_schedule, timedelta(minutes=1)
        )
        self._unsubs.append(unsub)

    def _setup_counter_reset(self):
        """매일 00시 Opinet 카운터 리셋, 매월 1일 Tmap 카운터 리셋"""
        async def _reset_counters(now):
            kst_now = dt_util.as_local(now)
            if kst_now.hour == 0 and kst_now.minute == 0:
                self.coordinator.opinet_call_count = 0
                if kst_now.day == 1:
                    self.coordinator.tmap_call_count = 0

        unsub = async_track_time_interval(
            self.hass, _reset_counters, timedelta(minutes=1)
        )
        self._unsubs.append(unsub)

    def _setup_movement_refresh(self):
        """위치 엔티티 변경 감지 → 일정 거리 이상 이동 시 갱신"""
        location_entity = self.entry.data.get(CONF_LOCATION_ENTITY)
        if not location_entity:
            return

        async def _on_location_change(event):
            rd = self.entry.options.get(CONF_REFRESH_DISTANCE, 10)
            re = self.entry.options.get(CONF_REFRESH_ENABLED, True)
            if not re:
                return
            new_loc = self._get_current_location()
            last_loc = self.coordinator.last_refresh_location
            if new_loc is None or last_loc is None:
                return
            dist = _haversine_km(last_loc[0], last_loc[1], new_loc[0], new_loc[1])
            if dist >= rd:
                _LOGGER.debug("Movement detected: %.1f km >= %d km, refreshing", dist, rd)
                await self.coordinator.async_refresh()
                self.coordinator.last_refresh_location = new_loc

        unsub = async_track_state_change_event(
            self.hass, [location_entity], _on_location_change
        )
        self._unsubs.append(unsub)

    def cleanup(self):
        """구독 해제"""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
