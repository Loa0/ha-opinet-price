"""좌표변환 유틸리티 — KATEC ↔ WGS84 변환 + 가격 추출"""

import math


def katec_to_wgs84(gis_x, gis_y) -> tuple:
    """Opinet GIS_X_COOR/GIS_Y_COOR (Bessel KATEC) → WGS84 (lat, lon)"""
    try:
        kx = float(gis_x)
        ky = float(gis_y)
    except (TypeError, ValueError):
        return None, None

    # Bessel 1841 parameters
    a = 6377397.155
    es = 0.00667437223131
    lon0 = 128.0 * math.pi / 180
    lat0 = 38.0 * math.pi / 180
    k0 = 0.9999
    x0 = 400000.0
    y0 = 600000.0
    dx, dy, dz = 115.80, -474.99, -674.11

    kx -= x0
    ky -= y0

    def _m(lat):
        return a * (
            (1 - es / 4 - 3 * es ** 2 / 64 - 5 * es ** 3 / 256) * lat
            - (3 * es / 8 + 3 * es ** 2 / 32 + 45 * es ** 3 / 1024) * math.sin(2 * lat)
            + (15 * es ** 2 / 256 + 45 * es ** 3 / 1024) * math.sin(4 * lat)
            - (35 * es ** 3 / 3072) * math.sin(6 * lat)
        )

    M0 = _m(lat0)
    M = M0 + ky / k0
    e1 = (1 - math.sqrt(1 - es)) / (1 + math.sqrt(1 - es))
    mu = M / (a * (1 - es / 4 - 3 * es ** 2 / 64 - 5 * es ** 3 / 256))

    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
    )

    sin_p, cos_p, tan_p = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    N1 = a / math.sqrt(1 - es * sin_p ** 2)
    T, C = tan_p ** 2, es / (1 - es) * cos_p ** 2
    D = kx / (N1 * k0)

    blat = phi1 - (
        N1
        * tan_p
        / (a * (1 - es) / (1 - es * sin_p ** 2) ** 1.5)
    ) * (
        D ** 2 / 2
        - (5 + 3 * T + 10 * C - 4 * C ** 2 - 9 * es / (1 - es)) * D ** 4 / 24
        + (61 + 90 * T + 298 * C + 45 * T ** 2 - 252 * es / (1 - es) - 3 * C ** 2) * D ** 6 / 720
    )
    blon = lon0 + (
        D
        - (1 + 2 * T + C) * D ** 3 / 6
        + (5 - 2 * C + 28 * T - 3 * C ** 2 + 8 * es / (1 - es) + 24 * T ** 2) * D ** 5 / 120
    ) / cos_p

    # Bessel → WGS84 Molodensky
    sin_b, cos_b = math.sin(blat), math.cos(blat)
    sin_l, cos_l = math.sin(blon), math.cos(blon)
    v = a / math.sqrt(1 - es * sin_b ** 2)
    bx = v * cos_b * cos_l
    by = v * cos_b * sin_l
    bz = v * (1 - es) * sin_b
    wx = bx + dx
    wy = by + dy
    wz = bz + dz

    wa = 6378137.0
    wes = 0.00669437999014
    p = math.sqrt(wx ** 2 + wy ** 2)
    wlat = math.atan2(wz, p * (1 - wes))
    for _ in range(5):
        vw = wa / math.sqrt(1 - wes * math.sin(wlat) ** 2)
        wlat = math.atan2(wz + wes * vw * math.sin(wlat), p)
    wlon = math.atan2(wy, wx)

    return math.degrees(wlat), math.degrees(wlon)


def _get_price(s: dict) -> int:
    """detailById(OIL 배열) / aroundAll(PRICE) 호환 가격 추출"""
    try:
        p = s.get("PRICE") or s.get("OIL_PRICE")
        if isinstance(p, list):
            for item in p:
                if isinstance(item, dict):
                    return int(item.get("PRICE", 0))
            return 0
        return int(p or 0)
    except (ValueError, TypeError):
        return 0
