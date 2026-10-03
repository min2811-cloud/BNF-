"""
앱 전체에서 쓰는 기준값을 한곳에 모아둔 파일.
숫자를 바꾸고 싶으면 이 파일만 고치면 된다 (다른 파일은 안 건드려도 됨).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def now_kst() -> datetime:
    """한국 시간 현재 시각. Streamlit Cloud 서버는 UTC라 그냥 datetime.now()를 쓰면
    오전 9시 전에는 날짜가 하루 전으로 나온다."""
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


# --- 사장님 매매 규칙 (2026-10-03 확정, 백테스트: workflows/bnf_backtest.md) ---
# 10시 급락 스캔 -> 14시50분에도 -5% 이하면 매수 -> -4% 손절 / +10% 익절 / 3거래일째 매도
SCAN_HOUR_LABEL = "오전 10시"
BUY_CHECK_HOUR = 14           # 이 시각(시) 전에 매수 확인을 누르면 "아직 이르다" 안내
BUY_CHECK_LABEL = "오후 2시 50분"
BUY_BUDGET_WON = 1_000_000    # 종목당 이 금액 이내로 최대 수량, 1주가 이보다 비싸면 1주
STOP_LOSS_PCT = -4.0          # 매수가 대비 이만큼 떨어지면 즉시 손절
TAKE_PROFIT_PCT = 10.0        # 매수가 대비 이만큼 오르면 즉시 익절
HOLD_TRADING_DAYS = 3         # 매수일로부터 이 거래일째에 매도(휴일은 건너뜀)
MARKET_PANIC_KOSPI_DROP_PCT = -1.0  # 코스피가 이 값 이하면 "시장 같이 하락"으로 표시(참고용)


def buy_quantity(price: float) -> int:
    """규칙 수량: 100만원 이내 최대 수량, 1주가 100만원 넘으면 1주."""
    if price <= 0:
        return 0
    return max(1, int(BUY_BUDGET_WON // price))


# --- 우량주 유니버스 ---
# 코스피 종목마스터 파일 기준이라 시장은 코스피로 고정 (app/universe.py 참고)
# 2026-09-24: BNF 본인은 일본 대형주 700~800개를 다 봤고, 국내 적용 사례에서는
# "코스피+코스닥 통합 시총 상위 200위"를 쓴 것을 확인해서 150->200으로 올림.
# 코스닥은 아직 통합 안 함(app/universe.py 주석 참고 — 종목마스터 필드 구조 문제).
TOP_N = 200                 # 시가총액 상위 몇 종목까지 "우량주"로 볼지

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
SHEET_SCANS = "scans"

# --- 한국투자증권(KIS) Open API ---
# 시세 조회만 쓰므로 실전투자 도메인을 쓴다 (모의투자 도메인은 주문 시뮬레이션용).
KIS_BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_TOKEN_CACHE_TTL_SEC = 60 * 60 * 20  # access_token 유효기간(24시간)보다 짧게 캐시
