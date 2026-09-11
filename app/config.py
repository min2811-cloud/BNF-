"""
앱 전체에서 쓰는 기준값을 한곳에 모아둔 파일.
숫자를 바꾸고 싶으면 이 파일만 고치면 된다 (다른 파일은 안 건드려도 됨).
"""

# --- 우량주 유니버스 ---
# 코스피 종목마스터 파일 기준이라 시장은 코스피로 고정 (app/universe.py 참고)
TOP_N = 150                # 시가총액 상위 몇 종목까지 "우량주"로 볼지

# --- 급락 판정 ---
DROP_THRESHOLD_PCT = -5.0  # 전일 대비 등락률이 이 값 이하면 "급락"으로 본다

# --- 이격도(disparity) ---
DISPARITY_MA_PERIOD = 25   # 이격도 계산용 이동평균 기간(일)
DISPARITY_BUY_MAX = 80     # 이격도가 이 값 이하면 매수 조건 충족
DISPARITY_SELL_TARGET = 100  # 이격도가 이 값 이상으로 올라오면 익절 신호

# --- RSI ---
RSI_PERIOD = 14
RSI_OVERSOLD = 30          # RSI가 이 값 미만이면 과매도(매수 조건 충족)

# --- MACD ---
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# --- 손절 ---
STOP_LOSS_LOOKBACK_DAYS = 20  # "이전 저점" = 매수일 기준 최근 N거래일 저가 중 최솟값

# --- 일봉 조회 개수 ---
# MACD(26일) 계산에 필요한 워밍업 기간 + 여유분을 확보하기 위해 넉넉히 100개 요청
DAILY_CANDLE_COUNT = 100

# --- 구글시트 ---
SPREADSHEET_NAME = "BNF매매법_데이터"
SHEET_RECOMMENDATIONS = "recommendations"
SHEET_HOLDINGS = "holdings"

# --- 한국투자증권(KIS) Open API ---
# 시세 조회만 쓰므로 실전투자 도메인을 쓴다 (모의투자 도메인은 주문 시뮬레이션용).
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN_CACHE_TTL_SEC = 60 * 60 * 20  # access_token 유효기간(24시간)보다 짧게 캐시
