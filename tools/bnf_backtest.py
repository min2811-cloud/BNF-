"""
BNF 매매법 백테스트 엔진 (v3).

2026-02-01부터 오늘까지, 코스피 시가총액 상위 종목(기본 200개, 개별주만 —
ETF/ETN/리츠 등 제외)을 대상으로 두 가지 "매수 신호"를 각각 시뮬레이션하고
비교한다.

신호(signal_mode):
  - "급락5%": 전일 대비 진입시각 체결가 등락률 <= DROP_THRESHOLD_PCT(-5%).
    지금까지 이 프로젝트가 쓰던 방식(사장님이 지정한 변형 규칙).
  - "BNF원전": 최근 BNF_INDICATOR_WINDOW_DAYS(10)거래일 내 이격도가 한 번이라도
    BNF_DISPARITY_MAX(80, 즉 -20%) 이하였음 AND 같은 기간 내 RSI가 한 번이라도
    BNF_RSI_MAX(35) 밑으로 내려간 적 있음 AND 그날 MACD 히스토그램이 막
    음(-)에서 양(+)으로 전환. BNF 리서치에서 확인한 원전 방식에 가장 가까운
    신호이되, 실제 200종목으로 돌려보니 "이격도<=80과 RSI<30과 MACD전환이
    전부 같은 날"인 경우가 거의 0에 가까웠다(대형주는 RSI가 그렇게까지 안
    내려가는 데다, MACD전환은 반등이 어느 정도 진행된 뒤에야 나오는 후행
    신호라 이격도·RSI 저점 시점과 잘 안 겹친다). 그래서 이격도·RSI 둘 다
    "최근 며칠 내 충족이면 인정"으로, RSI 임계값도 30->35로 완화했다(사장님
    확인 후 적용, 2026-09-24). 신호는 "그날 종가"로만 확정할 수 있으므로,
    매수는 신호 확정일의 **다음 거래일**에 한다(급락5%는 신호일 당일 매수).

매수 시점(entry_mode, 두 신호 모두 공통 — BNF가 "장 시작 후 30분, 마감 전
30분을 집중 관찰"했다는 리서치 내용을 반영):
  - "9시30분": 매수일 오전 9시30분(장 시작 30분 후) 체결가
  - "마감30분전": 매수일 15시(정규장 마감 15:30의 30분 전) 체결가
  두 시각 모두 KIS 시간별시세 API로 실제 조회한다(app/kis_client.py
  get_intraday_price_at).

손절(stop_loss_mode, 세 가지를 동시에 비교 — 추가 API 호출 없이 같은 일봉
데이터로 계산):
  - "이전저점": 신호일 이전 STOP_LOSS_LOOKBACK_DAYS거래일 저가 중 최솟값
  - "고정-5%": 매수가 대비 -5%
  - "고정-8%": 매수가 대비 -8%
  손절 미발동 시 이격도100 익절 -> +10% 익절 -> 3거래일 만기 매도 순으로 적용(공통).

부가 태깅:
  - market_panic: 신호일에 코스피 지수 자체도 MARKET_PANIC_KOSPI_DROP_PCT 이하로
    빠졌는지(=업종 전체가 아니라 "시장 전체가 같이 빠졌는지"의 근사치. 원래는
    종목별 업종 지수와 비교하는 게 더 정확하지만, 종목마다 업종 지수를 추가로
    조회해야 해서 이번엔 코스피 지수로 근사했다 — 정밀하게 하려면 후속 작업 필요)
  - return_pct_after_fee: 매도세+수수료 왕복 약 ROUND_TRIP_FEE_PCT(0.2%p)를
    뺀 근사 세후 수익률. 실제 세율·수수료는 증권사/시점마다 다르므로 근사치.

사장님 규칙(v6, signal "사장님규칙" — 위 비교와 별도로 실행·저장):
  10시 체결가 -5% 이하 AND 14시50분 체결가 -5% 이하 -> 14시50분 매수(100만원 이내,
  1주가 넘으면 1주). 장중 -4% 손절 / +10% 익절 / 3거래일째 종가 매도. 원 단위 손익과
  거래일별 필요 자금도 계산한다. 결과: .tmp/bnf_owner_trades.csv,
  .tmp/bnf_owner_capital.csv, .tmp/bnf_owner_meta.json

결과물(원본 데이터만 — 집계/분석은 tools/bnf_html_report.py에서):
  .tmp/bnf_backtest_trades.csv  — 트레이드 단위 원본 기록
  .tmp/bnf_backtest_meta.json   — 실행 정보, 파라미터, 실패 종목, 코스피 벤치마크

실행: python -m tools.bnf_backtest              (전체: 사장님 규칙 + 기존 비교)
      python -m tools.bnf_backtest --owner-only (사장님 규칙만, 약 10분)
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from app import config as app_config
from app import indicators
from app import kis_client as kc
from app import universe

# 순차 호출이 너무 느려서(200종목 x 시각별 체결가 수천 건) 병렬로 조회한다.
# KIS 실전계좌 시세 조회 제한이 대략 초당 20건 수준이라 알려져 있어, 그 밑으로
# 여유 있게 잡았다. (토큰 발급 자체는 1분에 1회 제한이라 별개 — 토큰은 최초 1회
# 순차 호출로 미리 캐시해두고 시작하므로 병렬 구간에서는 재발급이 일어나지 않는다.)
INTRADAY_WORKERS = 16
DAILY_FETCH_WORKERS = 16

SIGNAL_START = "20260201"
TODAY = pd.Timestamp.today().strftime("%Y%m%d")
WARMUP_DAYS = 60  # 25일 이평/RSI/MACD/손절선 계산용 워밍업 여유(달력일)
HOLD_TRADING_DAYS = 3
TAKE_PROFIT_PCT = 10.0
DISPARITY_EXIT = 100.0

DROP_PCT = app_config.DROP_THRESHOLD_PCT          # -5.0, "급락5%" 신호 기준
BNF_DISPARITY_MAX = app_config.DISPARITY_BUY_MAX   # 80  (=이격도 -20%), "BNF원전" 신호 기준
BNF_RSI_MAX = 35  # 원 리서치 기준(30)에서 완화 — 2026-09-24 사용자 확인 후 적용
BNF_INDICATOR_WINDOW_DAYS = 10  # 이격도·RSI 둘 다 이 기간 내 한 번이라도 충족이면 인정
STOP_LOOKBACK = app_config.STOP_LOSS_LOOKBACK_DAYS  # 20

STOP_LOSS_MODES = ["이전저점", "고정-5%", "고정-8%"]
SIGNAL_MODES = ["급락5%", "BNF원전"]
ENTRY_MODES = ["9시30분", "마감30분전"]
ENTRY_TIME_STR = {"9시30분": "093000", "마감30분전": "150000"}

MARKET_PANIC_KOSPI_DROP_PCT = -1.0  # 코스피 지수가 당일 이 값 이하면 "시장 전체 패닉"으로 태깅
ROUND_TRIP_FEE_PCT = 0.2  # 매도세+수수료 왕복 근사치(%p). 증권사/시점마다 다름 — 근사값.

# --- 사장님 규칙(v6, 2026-10-03 확정) ---
# 10시 스캔 -5% 이하 & 14시50분에도 -5% 이하면 매수, 종목당 100만원 이내(1주가 넘으면 1주),
# 장중 -4% 손절 / +10% 익절, 아니면 3거래일째 매도(휴일은 거래일이 아니므로 자동 연장).
OWNER_SCAN_TIME = "100000"
OWNER_ENTRY_TIME = "145000"
OWNER_BUDGET_WON = 1_000_000
OWNER_STOP_PCT = -4.0
OWNER_TAKE_PROFIT_PCT = 10.0

OUT_DIR = Path(".tmp")
OWNER_TRADES_PATH = OUT_DIR / "bnf_owner_trades.csv"
OWNER_CAPITAL_PATH = OUT_DIR / "bnf_owner_capital.csv"
OWNER_META_PATH = OUT_DIR / "bnf_owner_meta.json"


@dataclass
class Trade:
    ticker: str
    name: str
    signal_mode: str
    entry_mode: str
    stop_loss_mode: str
    signal_date: str
    entry_date: str
    entry_price: float
    stop_loss_price: float
    exit_date: str | None
    exit_price: float | None
    exit_reason: str
    holding_trading_days: int | None
    return_pct: float | None
    return_pct_after_fee: float | None
    market_panic: bool
    kospi_change_on_signal_day: float | None
    drop_pct_on_signal: float | None
    disparity_on_signal: float | None


def _simulate_exit(
    df: pd.DataFrame, entry_idx: int, entry_price: float, stop_loss_price: float
) -> tuple[int | None, str | None]:
    """entry_idx 다음날부터 최대 HOLD_TRADING_DAYS 거래일 동안 매도 조건을 찾는다."""
    n = len(df)
    close = df["close"]
    last_available = n - 1
    maturity_idx = entry_idx + HOLD_TRADING_DAYS
    scan_upto = min(maturity_idx, last_available)

    for j in range(entry_idx + 1, scan_upto + 1):
        low_j = float(df["low"].iloc[j])
        if low_j <= stop_loss_price:
            return j, "손절"

        window = close.iloc[: j + 1]
        disparity = (
            indicators.calc_disparity(window)
            if len(window) >= app_config.DISPARITY_MA_PERIOD
            else None
        )
        if disparity is not None and disparity >= DISPARITY_EXIT:
            return j, "이격도100_익절"

        cur_close = float(close.iloc[j])
        ret = (cur_close / entry_price - 1) * 100
        if ret >= TAKE_PROFIT_PCT:
            return j, "10%_익절"

        if j == maturity_idx:
            return j, "3일만기_매도"

    return None, None


def _exit_price_for(df: pd.DataFrame, exit_idx: int, exit_reason: str, stop_loss_price: float) -> float:
    if exit_reason == "손절":
        open_j = float(df["open"].iloc[exit_idx])
        return min(stop_loss_price, open_j)
    return float(df["close"].iloc[exit_idx])


def _stop_loss_price_for(mode: str, df: pd.DataFrame, signal_idx: int, entry_price: float) -> float:
    if mode == "이전저점":
        if signal_idx >= STOP_LOOKBACK:
            return indicators.calc_stop_loss_price(df.iloc[:signal_idx], STOP_LOOKBACK)
        if signal_idx > 0:
            return float(df["low"].iloc[:signal_idx].min())
        return entry_price
    if mode == "고정-5%":
        return entry_price * 0.95
    if mode == "고정-8%":
        return entry_price * 0.92
    raise ValueError(f"알 수 없는 stop_loss_mode: {mode}")


def _build_trades_for_signal(
    ticker: str,
    name: str,
    signal_mode: str,
    entry_mode: str,
    df: pd.DataFrame,
    signal_idx: int,
    entry_idx: int,
    entry_price: float,
    drop_pct_on_signal: float | None,
    disparity_on_signal: float | None,
    kospi_change_by_date: dict,
) -> list[Trade]:
    signal_date = df.loc[signal_idx, "date"]
    entry_date = df.loc[entry_idx, "date"]
    kospi_chg = kospi_change_by_date.get(signal_date)
    market_panic = bool(kospi_chg is not None and kospi_chg <= MARKET_PANIC_KOSPI_DROP_PCT)

    trades: list[Trade] = []
    for stop_mode in STOP_LOSS_MODES:
        stop_loss_price = _stop_loss_price_for(stop_mode, df, signal_idx, entry_price)
        exit_idx, exit_reason = _simulate_exit(df, entry_idx, entry_price, stop_loss_price)

        if exit_idx is None:
            trades.append(
                Trade(
                    ticker=ticker,
                    name=name,
                    signal_mode=signal_mode,
                    entry_mode=entry_mode,
                    stop_loss_mode=stop_mode,
                    signal_date=str(signal_date.date()),
                    entry_date=str(entry_date.date()),
                    entry_price=entry_price,
                    stop_loss_price=stop_loss_price,
                    exit_date=None,
                    exit_price=None,
                    exit_reason="진행중",
                    holding_trading_days=None,
                    return_pct=None,
                    return_pct_after_fee=None,
                    market_panic=market_panic,
                    kospi_change_on_signal_day=kospi_chg,
                    drop_pct_on_signal=drop_pct_on_signal,
                    disparity_on_signal=disparity_on_signal,
                )
            )
            continue

        exit_price = _exit_price_for(df, exit_idx, exit_reason, stop_loss_price)
        exit_date = df.loc[exit_idx, "date"]
        ret = (exit_price / entry_price - 1) * 100
        trades.append(
            Trade(
                ticker=ticker,
                name=name,
                signal_mode=signal_mode,
                entry_mode=entry_mode,
                stop_loss_mode=stop_mode,
                signal_date=str(signal_date.date()),
                entry_date=str(entry_date.date()),
                entry_price=entry_price,
                stop_loss_price=stop_loss_price,
                exit_date=str(exit_date.date()),
                exit_price=exit_price,
                exit_reason=exit_reason,
                holding_trading_days=exit_idx - entry_idx,
                return_pct=ret,
                return_pct_after_fee=ret - ROUND_TRIP_FEE_PCT,
                market_panic=market_panic,
                kospi_change_on_signal_day=kospi_chg,
                drop_pct_on_signal=drop_pct_on_signal,
                disparity_on_signal=disparity_on_signal,
            )
        )
    return trades


def _primary_exit_idx(trades: list[Trade], anchor_idx: int, last_idx: int) -> int:
    """블로킹(같은 종목 중복 신호 무시) 판단 기준 = "이전저점" 손절 변형의 청산 시점."""
    primary = next(t for t in trades if t.stop_loss_mode == "이전저점")
    if primary.exit_date is None:
        return last_idx
    return anchor_idx + primary.holding_trading_days


def _fetch_prices_parallel(ticker: str, keyed_requests: list[tuple]) -> dict:
    """keyed_requests: (키, 날짜문자열, 시각문자열) 목록을 병렬로 조회해서 {키: 가격} 반환.
    순차 호출이 병목이라 여러 스레드로 동시에 쏜다(같은 종목이라도 날짜가 다르므로
    독립적인 호출이라 병렬화해도 안전하다)."""
    if not keyed_requests:
        return {}

    def _fetch_one(item: tuple) -> tuple:
        key, date_str, time_str = item
        try:
            price = kc.get_intraday_price_at(ticker, date_str, time_str)
        except Exception:
            price = None
        return key, price

    results: dict = {}
    with ThreadPoolExecutor(max_workers=INTRADAY_WORKERS) as ex:
        for key, price in ex.map(_fetch_one, keyed_requests):
            results[key] = price
    return results


def simulate_drop5_intraday(
    ticker: str,
    name: str,
    df: pd.DataFrame,
    signal_start: pd.Timestamp,
    entry_mode: str,
    kospi_change_by_date: dict,
) -> tuple[list[Trade], int, int]:
    """entry_mode(예: "9시30분"/"마감30분전")별 체결가 기준 -5% 급락 신호.

    그날 저가조차 -5%에 못 미쳤으면 어느 시각이든 -5%가 안 됐을 것이므로
    (저가는 하루 중 최저가라 항상 다른 모든 체결가보다 같거나 낮음) 그런 날은
    후보에서 아예 뺀다. 남은 후보일은 가격을 병렬로 먼저 다 조회해두고(블로킹
    로직과 무관하게, 나중에 블로킹으로 스킵될 몇 건까지 약간 더 조회하게 되지만
    병렬 조회로 얻는 속도 이득이 훨씬 크다), 그 다음 날짜 순서대로 신호 확정·
    포지션 블로킹을 순차 적용한다.
    """
    time_str = ENTRY_TIME_STR[entry_mode]
    df = df.reset_index(drop=True)
    close = df["close"]
    low = df["low"]
    prev_close = close.shift(1)
    low_drop_pct = (low - prev_close) / prev_close * 100
    n = len(df)

    candidates: list[int] = []
    for i in range(n):
        date_i = df.loc[i, "date"]
        if date_i < signal_start:
            continue
        ld = low_drop_pct.iloc[i]
        if pd.isna(ld) or ld > DROP_PCT:
            continue
        candidates.append(i)

    price_map = _fetch_prices_parallel(
        ticker, [(i, df.loc[i, "date"].strftime("%Y%m%d"), time_str) for i in candidates]
    )

    trades: list[Trade] = []
    blocked_until = -1
    real_signals = 0

    for i in candidates:
        if i <= blocked_until:
            continue
        price_at_time = price_map.get(i)
        if price_at_time is None:
            continue

        prev_close_val = float(prev_close.iloc[i])
        drop_at_time = (price_at_time - prev_close_val) / prev_close_val * 100
        if drop_at_time > DROP_PCT:
            continue

        real_signals += 1
        entry_price = float(price_at_time)
        new_trades = _build_trades_for_signal(
            ticker, name, "급락5%", entry_mode, df, i, i, entry_price,
            float(drop_at_time), None, kospi_change_by_date,
        )
        trades.extend(new_trades)
        blocked_until = _primary_exit_idx(new_trades, i, n - 1)

    return trades, len(candidates), real_signals


def simulate_bnf_original(
    ticker: str,
    name: str,
    df: pd.DataFrame,
    signal_start: pd.Timestamp,
    entry_mode: str,
    kospi_change_by_date: dict,
) -> tuple[list[Trade], int]:
    """최근 며칠 내 이격도<=80 & RSI<35 + 당일 MACD 음->양 전환 신호.
    매수는 신호 다음 거래일의 entry_mode 시각 체결가. (가격 조회는 병렬,
    신호 확정·블로킹은 simulate_drop5_intraday와 같은 방식으로 순차 처리)"""
    time_str = ENTRY_TIME_STR[entry_mode]
    df = df.reset_index(drop=True)
    close = df["close"]
    disparity_s = indicators.calc_disparity_series(close)
    disparity_recent_ok = disparity_s.rolling(BNF_INDICATOR_WINDOW_DAYS, min_periods=1).min() <= BNF_DISPARITY_MAX
    rsi_s = indicators.calc_rsi_series(close)
    rsi_recent_oversold = rsi_s.rolling(BNF_INDICATOR_WINDOW_DAYS, min_periods=1).min() < BNF_RSI_MAX
    macd_s = indicators.calc_macd_hist_series(close)
    macd_turned = (macd_s.shift(1) <= 0) & (macd_s > 0)
    n = len(df)

    candidates: list[tuple[int, int]] = []  # (signal_idx, entry_idx)
    for i in range(n):
        date_i = df.loc[i, "date"]
        if date_i < signal_start:
            continue
        if i + 1 >= n:
            continue  # 다음 거래일 데이터가 아직 없음 -> 매수 불가, 스킵

        if pd.isna(disparity_recent_ok.iloc[i]) or pd.isna(rsi_recent_oversold.iloc[i]):
            continue
        cond = (
            bool(disparity_recent_ok.iloc[i])
            and bool(rsi_recent_oversold.iloc[i])
            and bool(macd_turned.iloc[i])
        )
        if not cond:
            continue
        candidates.append((i, i + 1))

    price_map = _fetch_prices_parallel(
        ticker,
        [(i, df.loc[entry_idx, "date"].strftime("%Y%m%d"), time_str) for i, entry_idx in candidates],
    )

    trades: list[Trade] = []
    blocked_until = -1
    skipped_no_price = 0

    for i, entry_idx in candidates:
        if i <= blocked_until:
            continue
        price_at_time = price_map.get(i)
        if price_at_time is None:
            skipped_no_price += 1
            continue
        entry_price = float(price_at_time)

        new_trades = _build_trades_for_signal(
            ticker, name, "BNF원전", entry_mode, df, i, entry_idx, entry_price,
            None, float(disparity_s.iloc[i]), kospi_change_by_date,
        )
        trades.extend(new_trades)
        blocked_until = _primary_exit_idx(new_trades, entry_idx, n - 1)

    return trades, skipped_no_price


@dataclass
class OwnerTrade:
    ticker: str
    name: str
    signal_date: str  # = 매수일(10시 스캔 + 14시50분 매수가 같은 날)
    drop_pct_at_scan: float  # 10시 전일 대비 등락률
    drop_pct_at_entry: float  # 14시50분 전일 대비 등락률
    entry_price: float
    quantity: int
    invested: float
    exit_date: str | None
    exit_price: float | None
    exit_reason: str
    holding_trading_days: int | None
    return_pct: float | None
    return_pct_after_fee: float | None
    pnl_won: float | None
    pnl_won_after_fee: float | None
    market_panic: bool
    kospi_change_on_signal_day: float | None


def _simulate_owner_exit(
    df: pd.DataFrame, entry_idx: int, entry_price: float
) -> tuple[int | None, float | None, str | None]:
    """사장님 규칙 청산: 장중 즉시 -4% 손절 / +10% 익절, 아니면 3거래일째 종가 매도.

    일봉(시가·고가·저가·종가)만으로 판정하므로:
      - 시가부터 손절선 이하(갭하락)면 시가에 손절, 익절선 이상(갭상승)이면 시가에 익절
      - 같은 날 저가가 손절선, 고가가 익절선을 둘 다 찍었으면 어느 쪽이 먼저인지
        모르므로 보수적으로 손절 처리
      - 매수일 당일(14:50~15:30)은 종가가 이미 손절선 이하면 당일 손절로 본다
    """
    stop = entry_price * (1 + OWNER_STOP_PCT / 100)
    take = entry_price * (1 + OWNER_TAKE_PROFIT_PCT / 100)

    if float(df["close"].iloc[entry_idx]) <= stop:
        return entry_idx, float(df["close"].iloc[entry_idx]), "손절(-4%)"

    maturity_idx = entry_idx + HOLD_TRADING_DAYS
    for j in range(entry_idx + 1, min(maturity_idx, len(df) - 1) + 1):
        o, h, l, c = (float(df[k].iloc[j]) for k in ("open", "high", "low", "close"))
        if o <= stop:
            return j, o, "손절(-4%)"
        if o >= take:
            return j, o, "익절(+10%)"
        if l <= stop:
            return j, stop, "손절(-4%)"
        if h >= take:
            return j, take, "익절(+10%)"
        if j == maturity_idx:
            return j, c, "3일만기_매도"
    return None, None, None


def simulate_owner_rule(
    ticker: str,
    name: str,
    df: pd.DataFrame,
    signal_start: pd.Timestamp,
    kospi_change_by_date: dict,
) -> tuple[list[OwnerTrade], int]:
    """사장님 규칙: 10시 체결가가 전일 대비 -5% 이하 AND 14시50분 체결가도 -5% 이하면
    14시50분 체결가로 매수. 100만원 이내 최대 수량(1주가 100만원 넘으면 1주).
    같은 종목은 보유 중이면 다시 사지 않는다(종목당 동시 1포지션)."""
    df = df.reset_index(drop=True)
    prev_close = df["close"].shift(1)
    low_drop_pct = (df["low"] - prev_close) / prev_close * 100
    n = len(df)

    candidates = [
        i for i in range(n)
        if df.loc[i, "date"] >= signal_start
        and pd.notna(low_drop_pct.iloc[i]) and low_drop_pct.iloc[i] <= DROP_PCT
    ]
    requests_ = []
    for i in candidates:
        d = df.loc[i, "date"].strftime("%Y%m%d")
        requests_.append(((i, "scan"), d, OWNER_SCAN_TIME))
        requests_.append(((i, "entry"), d, OWNER_ENTRY_TIME))
    price_map = _fetch_prices_parallel(ticker, requests_)

    trades: list[OwnerTrade] = []
    blocked_until = -1
    for i in candidates:
        if i <= blocked_until:
            continue
        scan_p, entry_p = price_map.get((i, "scan")), price_map.get((i, "entry"))
        if scan_p is None or entry_p is None:
            continue
        pc = float(prev_close.iloc[i])
        scan_drop = (scan_p - pc) / pc * 100
        entry_drop = (entry_p - pc) / pc * 100
        if scan_drop > DROP_PCT or entry_drop > DROP_PCT:
            continue

        entry_price = float(entry_p)
        qty = max(1, int(OWNER_BUDGET_WON // entry_price))
        invested = qty * entry_price
        signal_date = df.loc[i, "date"]
        kospi_chg = kospi_change_by_date.get(signal_date)
        exit_idx, exit_price, reason = _simulate_owner_exit(df, i, entry_price)

        if exit_idx is None:
            ret = ret_fee = pnl = pnl_fee = None
            exit_date, hold_days, reason = None, None, "진행중"
            blocked_until = n - 1
        else:
            ret = (exit_price / entry_price - 1) * 100
            ret_fee = ret - ROUND_TRIP_FEE_PCT
            pnl = qty * (exit_price - entry_price)
            pnl_fee = pnl - invested * ROUND_TRIP_FEE_PCT / 100
            exit_date = str(df.loc[exit_idx, "date"].date())
            hold_days = exit_idx - i
            blocked_until = exit_idx

        trades.append(
            OwnerTrade(
                ticker=ticker, name=name, signal_date=str(signal_date.date()),
                drop_pct_at_scan=scan_drop, drop_pct_at_entry=entry_drop,
                entry_price=entry_price, quantity=qty, invested=invested,
                exit_date=exit_date, exit_price=exit_price, exit_reason=reason,
                holding_trading_days=hold_days, return_pct=ret, return_pct_after_fee=ret_fee,
                pnl_won=pnl, pnl_won_after_fee=pnl_fee,
                market_panic=bool(kospi_chg is not None and kospi_chg <= MARKET_PANIC_KOSPI_DROP_PCT),
                kospi_change_on_signal_day=kospi_chg,
            )
        )
    return trades, len(candidates)


def _owner_capital_by_day(trades_df: pd.DataFrame, trading_days: list[pd.Timestamp]) -> pd.DataFrame:
    """거래일별로 14시50분 매수 직후 들고 있는 포지션 수·투입금액 합계.
    (그날 매도된 포지션은 매수 전에 팔렸다고 보고 제외 — 근사)"""
    if trades_df.empty:
        return pd.DataFrame(columns=["date", "positions", "capital", "new_buys"])
    entry = pd.to_datetime(trades_df["signal_date"])
    exit_ = pd.to_datetime(trades_df["exit_date"])
    rows = []
    for d in trading_days:
        held = (entry <= d) & (exit_.isna() | (exit_ > d) | (entry == d))
        rows.append(
            {
                "date": str(d.date()),
                "positions": int(held.sum()),
                "capital": float(trades_df.loc[held, "invested"].sum()),
                "new_buys": int((entry == d).sum()),
            }
        )
    return pd.DataFrame(rows)


def run_owner_rule(
    universe_list, daily_by_ticker: dict, signal_start_dt: pd.Timestamp,
    kospi_change_by_date: dict, failed: list[dict], benchmark: dict | None,
) -> None:
    all_trades: list[OwnerTrade] = []
    total_candidates = 0
    for k, stock in enumerate(universe_list, start=1):
        df = daily_by_ticker.get(stock.ticker)
        if df is None:
            continue
        t, cand = simulate_owner_rule(stock.ticker, stock.name, df, signal_start_dt, kospi_change_by_date)
        all_trades.extend(t)
        total_candidates += cand
        print(f"[사장님규칙 {k}/{len(universe_list)}] {stock.ticker} {stock.name}: 후보 {cand}일 -> 매수 {len(t)}건", flush=True)

    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    trades_df.to_csv(OWNER_TRADES_PATH, index=False, encoding="utf-8-sig")

    trading_days = sorted(
        {d for df in daily_by_ticker.values() for d in df["date"] if d >= signal_start_dt}
    )
    cap_df = _owner_capital_by_day(trades_df, trading_days)
    cap_df.to_csv(OWNER_CAPITAL_PATH, index=False, encoding="utf-8-sig")

    meta = {
        "run_at": pd.Timestamp.now().isoformat(),
        "signal_start": SIGNAL_START,
        "signal_end": TODAY,
        "universe_size": len(universe_list),
        "failed_tickers": failed,
        "benchmark_kospi": benchmark,
        "candidate_days": total_candidates,
        "trades": len(all_trades),
        "max_capital": float(cap_df["capital"].max()) if len(cap_df) else 0.0,
        "max_capital_date": str(cap_df.loc[cap_df["capital"].idxmax(), "date"]) if len(cap_df) else None,
        "max_positions": int(cap_df["positions"].max()) if len(cap_df) else 0,
        "params": {
            "scan_time": OWNER_SCAN_TIME,
            "entry_time": OWNER_ENTRY_TIME,
            "drop_threshold_pct": DROP_PCT,
            "budget_won": OWNER_BUDGET_WON,
            "stop_pct": OWNER_STOP_PCT,
            "take_profit_pct": OWNER_TAKE_PROFIT_PCT,
            "hold_trading_days": HOLD_TRADING_DAYS,
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
            "market_panic_kospi_drop_pct": MARKET_PANIC_KOSPI_DROP_PCT,
        },
    }
    with open(OWNER_META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(
        f"\n사장님규칙 완료: 후보 {total_candidates}일 -> 매수 {len(all_trades)}건, "
        f"최대 필요자금 {meta['max_capital']:,.0f}원({meta['max_capital_date']})"
    )
    print(f"저장: {OWNER_TRADES_PATH}, {OWNER_CAPITAL_PATH}, {OWNER_META_PATH}")


def main() -> None:
    owner_only = "--owner-only" in sys.argv
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    universe_list = universe.get_top_market_cap_universe(app_config.TOP_N)
    print(f"유니버스 {len(universe_list)}종목 확보 (코스피 시가총액 상위, 개별주만, 현재 스냅샷)")

    signal_start_dt = pd.Timestamp(SIGNAL_START)
    fetch_start = (signal_start_dt - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y%m%d")

    kospi_change_by_date: dict = {}
    benchmark = None
    try:
        idx_df = kc.get_kospi_index_daily_range(fetch_start, TODAY)
        idx_chg = idx_df["close"].pct_change() * 100
        for d, c in zip(idx_df["date"], idx_chg):
            kospi_change_by_date[d] = float(c) if pd.notna(c) else None
        idx_in_range = idx_df[idx_df["date"] >= signal_start_dt].reset_index(drop=True)
        if len(idx_in_range) >= 2:
            start_close = float(idx_in_range.iloc[0]["close"])
            end_close = float(idx_in_range.iloc[-1]["close"])
            benchmark = {
                "start_date": str(idx_in_range.iloc[0]["date"].date()),
                "end_date": str(idx_in_range.iloc[-1]["date"].date()),
                "start_close": start_close,
                "end_close": end_close,
                "return_pct": (end_close / start_close - 1) * 100,
            }
            print(f"코스피 지수 벤치마크: {benchmark['return_pct']:.2f}% (market_panic 태깅에도 재사용)")
    except Exception as e:  # noqa: BLE001
        print(f"코스피 지수 조회 실패(벤치마크·market_panic 태깅 생략): {e}")

    all_trades: list[Trade] = []
    failed: list[dict] = []
    am_stats = {
        "급락5%": {m: {"candidate_days": 0, "real_signals": 0} for m in ENTRY_MODES},
        "BNF원전": {m: {"skipped_no_price": 0} for m in ENTRY_MODES},
    }

    print(f"일봉 데이터 {len(universe_list)}종목 병렬 조회 중...", flush=True)

    def _fetch_daily(stock) -> tuple[str, str, pd.DataFrame | None, str | None]:
        try:
            df = kc.get_daily_ohlcv_range(stock.ticker, fetch_start, TODAY)
            return stock.ticker, stock.name, df, None
        except Exception as e:  # noqa: BLE001
            return stock.ticker, stock.name, None, str(e)

    daily_by_ticker: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=DAILY_FETCH_WORKERS) as ex:
        for ticker, name, df, err in ex.map(_fetch_daily, universe_list):
            if err is not None:
                failed.append({"ticker": ticker, "name": name, "error": err})
            elif len(df) < app_config.DISPARITY_MA_PERIOD + 1:
                failed.append({"ticker": ticker, "name": name, "error": f"데이터 부족({len(df)}행)"})
            else:
                daily_by_ticker[ticker] = df

    print(f"일봉 조회 완료: {len(daily_by_ticker)}종목 성공, {len(failed)}종목 실패. 신호 계산 시작...", flush=True)

    run_owner_rule(universe_list, daily_by_ticker, signal_start_dt, kospi_change_by_date, failed, benchmark)
    if owner_only:
        return

    for i, stock in enumerate(universe_list, start=1):
        ticker, name = stock.ticker, stock.name
        df = daily_by_ticker.get(ticker)
        if df is None:
            continue

        progress_bits = []
        for entry_mode in ENTRY_MODES:
            t_drop, cand, real = simulate_drop5_intraday(
                ticker, name, df, signal_start_dt, entry_mode, kospi_change_by_date
            )
            am_stats["급락5%"][entry_mode]["candidate_days"] += cand
            am_stats["급락5%"][entry_mode]["real_signals"] += real
            all_trades.extend(t_drop)

            t_bnf, skipped = simulate_bnf_original(
                ticker, name, df, signal_start_dt, entry_mode, kospi_change_by_date
            )
            am_stats["BNF원전"][entry_mode]["skipped_no_price"] += skipped
            all_trades.extend(t_bnf)

            progress_bits.append(f"급락5%({entry_mode}){len(t_drop)//3}/BNF원전({entry_mode}){len(t_bnf)//3}")

        print(f"[{i}/{len(universe_list)}] {ticker} {name}: " + ", ".join(progress_bits), flush=True)

    trades_df = pd.DataFrame([asdict(t) for t in all_trades])
    trades_path = OUT_DIR / "bnf_backtest_trades.csv"
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")

    meta = {
        "run_at": pd.Timestamp.now().isoformat(),
        "signal_start": SIGNAL_START,
        "signal_end": TODAY,
        "universe_size": len(universe_list),
        "total_signal_rows": len(all_trades),
        "failed_tickers": failed,
        "benchmark_kospi": benchmark,
        "am_stats": am_stats,
        "params": {
            "drop_threshold_pct": DROP_PCT,
            "bnf_disparity_max": BNF_DISPARITY_MAX,
            "bnf_rsi_max": BNF_RSI_MAX,
            "bnf_indicator_window_days": BNF_INDICATOR_WINDOW_DAYS,
            "hold_trading_days": HOLD_TRADING_DAYS,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "disparity_exit": DISPARITY_EXIT,
            "stop_loss_lookback_days": STOP_LOOKBACK,
            "stop_loss_modes": STOP_LOSS_MODES,
            "signal_modes": SIGNAL_MODES,
            "entry_modes": ENTRY_MODES,
            "market_panic_kospi_drop_pct": MARKET_PANIC_KOSPI_DROP_PCT,
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
            "universe_basis": (
                "코스피 시가총액 상위(개별주만, ETF/ETN/리츠 등 제외), 현재 스냅샷 소급 적용. "
                "코스닥은 종목마스터 필드 구조 미확인으로 아직 미포함."
            ),
        },
    }
    meta_path = OUT_DIR / "bnf_backtest_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n완료: 총 트레이드 행(손절 3종 포함) {len(all_trades)}건, 조회 실패 {len(failed)}종목")
    for entry_mode in ENTRY_MODES:
        s = am_stats["급락5%"][entry_mode]
        print(f"급락5%({entry_mode}): 후보 {s['candidate_days']}일 -> 실신호 {s['real_signals']}건")
    print(f"저장: {trades_path}, {meta_path}")


if __name__ == "__main__":
    main()
