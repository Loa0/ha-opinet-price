"""Opinet / Tmap / GeoAPI HTTP 클라이언트 — API 호출 전용"""

import json
import logging
from urllib.parse import quote

import async_timeout

from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

# ── API 엔드포인트 ──────────────────────────────────────────────
TMAP_ROUTE_URL = "https://apis.openapi.sk.com/tmap/routes?version=1"
TMAP_GEO_URL = "https://apis.openapi.sk.com/tmap/geo/reversegeocoding?version=1"
GEOCODE_URL = "https://geo.ychome.kozow.com"
OPINET_DETAIL_URL = "https://www.opinet.co.kr/api/detailById.do"
OPINET_SEARCH_URL = "https://www.opinet.co.kr/api/searchByName.do"
# ─────────────────────────────────────────────────────────────


async def _fetch_detail_by_id(session, api_key: str, uni_id: str) -> dict | None:
    """detailById.do → (addr, name) 반환 (기존 호환용)"""
    full = await _fetch_detail_by_id_full(session, api_key, uni_id)
    if full:
        return {"addr": full.get("NEW_ADR", ""), "name": full.get("OS_NM", "")}
    return None


async def _fetch_detail_by_id_full(session, api_key: str, uni_id: str) -> dict | None:
    """detailById.do → aroundAll.do OIL 형식으로 변환 (OIL[0] 기반 + 키 정규화 + OIL_PRICE 평탄화)"""
    url = f"{OPINET_DETAIL_URL}?code={api_key}&id={uni_id}&out=json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with async_timeout.timeout(10):
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    _LOGGER.warning("detailById.do failed for %s: HTTP %s", uni_id, resp.status)
                    return None
                text = await resp.text()
                data = json.loads(text)
                result = data.get("RESULT", {}) or {}
                if not isinstance(result, dict):
                    _LOGGER.warning("detailById.do: unexpected result type for %s", uni_id)
                    return None

                oil_arr = result.get("OIL") or []
                if not isinstance(oil_arr, list) or not oil_arr:
                    _LOGGER.warning("detailById.do: no OIL array for %s", uni_id)
                    return None

                base = dict(oil_arr[0]) if isinstance(oil_arr[0], dict) else {}

                # 키 정규화: detailById는 _CO, _5 접미사 → aroundAll 형식(_CD, _YN_5)으로
                _KEY_MAP = {
                    "POLL_DIV_CO": "POLL_DIV_CD",
                    "GPOLL_DIV_CO": "GPOLL_DIV_CD",
                    "GOOD_YN5": "GOOD_YN_5",
                }
                for old_k, new_k in _KEY_MAP.items():
                    if old_k in base:
                        base[new_k] = base.pop(old_k)

                # OIL_PRICE 중첩 배열 → 첫 번째 항목의 PRICE 추출
                oil_price = base.pop("OIL_PRICE", None)
                if isinstance(oil_price, list) and oil_price:
                    first = oil_price[0]
                    if isinstance(first, dict):
                        base["PRICE"] = first.get("PRICE", 0)
                        base["TRADE_DT"] = first.get("TRADE_DT", "")
                        base["TRADE_TM"] = first.get("TRADE_TM", "")

                # PRODCD가 OIL_PRICE에만 있고 base에 없으면 채워줌
                if "PRODCD" not in base and isinstance(oil_price, list) and oil_price:
                    first = oil_price[0]
                    if isinstance(first, dict):
                        base["PRODCD"] = first.get("PRODCD", "")

                if base.get("UNI_ID"):
                    return base
                _LOGGER.warning("detailById.do: no UNI_ID for %s", uni_id)
    except Exception as e:
        _LOGGER.warning("detailById.do error for %s: %s", uni_id, e)
    return None


async def _fetch_search_by_name(session, api_key: str, osnm: str, area: str = "") -> list[dict]:
    """searchByName.do → 상호 검색 → [{UNI_ID, OS_NM, NEW_ADR, ...}]"""
    params = f"code={api_key}&osnm={quote(osnm)}&out=json"
    if area:
        params += f"&area={area}"
    url = f"{OPINET_SEARCH_URL}?{params}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        async with async_timeout.timeout(10):
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    _LOGGER.warning("searchByName.do failed: HTTP %s", resp.status)
                    return []
                text = await resp.text()
                data = json.loads(text)
                result = data.get("RESULT", {}) or {}
                return (result.get("OIL") or []) if isinstance(result, dict) else []
    except Exception as e:
        _LOGGER.warning("searchByName.do error: %s", e)
    return []


async def _fetch_station_coords(
    session, api_key: str, uid: str, vworld_key: str, uni_id: str, known_addr: str = None
) -> tuple:
    """UNI_ID → (result, detail_called, vworld_called).
    GeoAPI /station/{uni_id}(Redis) 우선, MISS → detailById + /geocode.
    known_addr 제공 시 detailById 스킵하고 바로 /geocode 호출.
    """
    # 1. GeoAPI /station/{uni_id} → Redis 캐시 조회
    if not known_addr:
        station_url = f"{GEOCODE_URL}/station/{quote(uni_id, safe='')}"
        try:
            async with async_timeout.timeout(5):
                async with session.get(station_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("lat") is not None and data.get("lng") is not None:
                            return (data, False, False)
        except Exception as e:
            _LOGGER.warning("Station cache lookup failed for %s: %s", uni_id, e)

    # 2. MISS → detailById.do → 도로명주소 (known_addr 있으면 스킵)
    detail_called = False
    if known_addr:
        addr = known_addr
    else:
        _LOGGER.warning("Station %s: cache MISS, calling detailById.do", uni_id)
        detail = await _fetch_detail_by_id(session, api_key, uni_id)
        if not detail or not detail["addr"]:
            _LOGGER.warning("Station %s: detailById.do failed or no addr", uni_id)
            return (None, True, False)
        addr = detail["addr"]
        detail_called = True

    # 3. GeoAPI /geocode → VWorld
    params = f"address={quote(addr)}&uid={quote(uid)}&uni_id={quote(uni_id, safe='')}"
    if vworld_key:
        params += f"&api_key={quote(vworld_key)}"
    geo_url = f"{GEOCODE_URL}/geocode?{params}"
    try:
        async with async_timeout.timeout(10):
            async with session.get(geo_url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") == "ok":
                        result = {
                            "addr": addr,
                            "lat": float(data["lat"]),
                            "lng": float(data["lng"]),
                        }
                        vworld_called = not data.get("cached", True)
                        return (result, detail_called, vworld_called)
                    elif data.get("status") == "rate_limited":
                        _LOGGER.warning("GeoAPI rate limited for uid=%s", uid)
                else:
                    _LOGGER.debug("GeoAPI HTTP %s for %s", resp.status, uni_id)
    except Exception as e:
        _LOGGER.debug("GeoAPI call failed for %s: %s", uni_id, e)

    return (None, detail_called, False)


async def _fetch_tmap_distance(session, tmap_key, start_lat, start_lon, end_lat, end_lon) -> int | None:
    """Tmap API로 주행거리(m) 조회"""
    headers = {
        "appKey": tmap_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    body = json.dumps({
        "startX": start_lon,
        "startY": start_lat,
        "endX": end_lon,
        "endY": end_lat,
        "reqCoordType": "WGS84GEO",
        "resCoordType": "WGS84GEO",
    })
    try:
        async with async_timeout.timeout(10):
            async with session.post(TMAP_ROUTE_URL, headers=headers, data=body) as resp:
                if resp.status != 200:
                    _LOGGER.debug("Tmap API error: %s", resp.status)
                    return None
                data = await resp.json()
                features = data.get("features", [])
                if features:
                    props = features[0].get("properties", {})
                    return props.get("totalDistance", None)
    except Exception as e:
        _LOGGER.debug("Tmap API call failed: %s", e)
    return None


async def _fetch_tmap_address(session, tmap_key, lat, lon) -> tuple:
    """Tmap 역지오코딩으로 주소 조회 → (전체주소, 간략주소)"""
    url = f"{TMAP_GEO_URL}&lat={lat}&lon={lon}&coordType=WGS84GEO&addressType=A00"
    headers = {"appKey": tmap_key, "Accept": "application/json"}
    try:
        async with async_timeout.timeout(5):
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return "", ""
                data = await resp.json()
                addr_info = data.get("addressInfo", {})
                full = addr_info.get("fullAddress", "")
                parts = full.split(" ", 1)
                short = parts[1] if len(parts) > 1 and parts[1] else full
                return full, short
    except Exception as e:
        _LOGGER.debug("Tmap geocoding failed: %s", e)
    return "", ""
