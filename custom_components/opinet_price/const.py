DOMAIN = "opinet_price"
CONF_API_KEY = "api_key"
CONF_RADIUS = "radius"
CONF_PRODCD = "prodcd"
CONF_LOCATION_ENTITY = "location_entity"
CONF_POLL_DIV = "주유소 필터"
CONF_SELF_ONLY = "셀프주유소만"
CONF_HIGHWAY_FILTER = "고속도로"
CONF_MAX_DISTANCE = "거리 표시"
CONF_TMAP_KEY = "tmap_api_key"
CONF_SORT_ORDER = "정렬 순서"
CONF_FAVORITES = "즐겨찾기"
FAV_LABELS = "fav_labels"
CONF_REFRESH_DISTANCE = "갱신 이동거리"
CONF_REFRESH_ENABLED = "이동 갱신"
CONF_VWORLD_KEY = "vworld_api_key"
CONF_RANK_COUNT = "rank_count"

HIGHWAY_OPTIONS = ["전체", "고속도로만", "고속도로 제외"]
SORT_OPTIONS = ["가격순", "주행거리순"]

PROD_CODES = {
    "B027": "휘발유",
    "D047": "경유",
    "B034": "고급휘발유",
    "C004": "실내등유",
    "K015": "자동차부탄"
}

BRAND_CODES = {
    "SKE": "SK에너지",
    "GSC": "GS칼텍스",
    "HDO": "현대오일뱅크",
    "SOL": "S-OIL",
    "RTE": "자영알뜰",
    "RTX": "고속도로알뜰",
    "NHO": "농협알뜰",
    "ETC": "자가상표",
    "E1G": "E1",
    "SKG": "SK가스"
}


AREA_CODES = {
    "전국": "",
    "서울": "01", "경기": "02", "강원": "03", "충북": "04",
    "충남": "05", "전북": "06", "전남광주": "20", "경북": "08",
    "경남": "09", "부산": "10", "제주": "11", "대구": "14",
    "인천": "15", "대전": "17", "울산": "18", "세종": "19",
}
