"""
이격도 / RSI / MACD 히스토그램 계산 모듈.

전부 순수 파이썬(pandas + ta 라이브러리) 계산이라 AI 호출이 전혀 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import ta

from app import config


@dataclass
class IndicatorSnapshot:
    disparity: float          # 이격도 (종가/이동평균*100)
    rsi: float                # RSI
    macd_hist: float          # MACD 히스토그램 (현재값)
    macd_just_turned_positive: bool  # 직전일 <=0 -> 금일 >0 으로 "방금 전환"됐는지

    @property
    def disparity_ok(self) -> bool:
        return self.disparity <= config.DISPARITY_BUY_MAX

    @property
    def rsi_ok(self) -> bool:
        return self.rsi < config.RSI_OVERSOLD

    @property
    def macd_ok(self) -> bool:
        return self.macd_hist > 0

    @property
    def all_conditions_met(self) -> bool:
        return self.disparity_ok and self.rsi_ok and self.macd_ok


def calc_disparity_series(close: pd.Series, period: int = config.DISPARITY_MA_PERIOD) -> pd.Series:
    """이격도를 날짜별로 전부 계산한 시리즈. 과거 특정일들을 한꺼번에 스캔할 때
    (예: 백테스트) 날마다 calc_disparity()를 반복 호출하는 것보다 훨씬 빠르다."""
    ma = close.rolling(window=period).mean()
    return close / ma * 100


def calc_rsi_series(close: pd.Series, period: int = config.RSI_PERIOD) -> pd.Series:
    return ta.momentum.RSIIndicator(close, window=period).rsi()


def calc_macd_hist_series(close: pd.Series) -> pd.Series:
    macd = ta.trend.MACD(
        close,
        window_fast=config.MACD_FAST,
        window_slow=config.MACD_SLOW,
        window_sign=config.MACD_SIGNAL,
    )
    return macd.macd_diff()


def calc_disparity(close: pd.Series, period: int = config.DISPARITY_MA_PERIOD) -> float:
    return float(calc_disparity_series(close, period).iloc[-1])


def calc_rsi(close: pd.Series, period: int = config.RSI_PERIOD) -> float:
    return float(calc_rsi_series(close, period).iloc[-1])


def calc_macd_hist(close: pd.Series) -> tuple[float, bool]:
    hist = calc_macd_hist_series(close)
    current = float(hist.iloc[-1])
    previous = float(hist.iloc[-2]) if len(hist) > 1 else current
    just_turned_positive = previous <= 0 and current > 0
    return current, just_turned_positive


def build_snapshot(daily_df: pd.DataFrame) -> IndicatorSnapshot:
    """일봉 DataFrame(오름차순, close 컬럼 포함)을 받아 3개 지표를 한번에 계산."""
    close = daily_df["close"]
    disparity = calc_disparity(close)
    rsi = calc_rsi(close)
    macd_hist, just_turned = calc_macd_hist(close)
    return IndicatorSnapshot(
        disparity=disparity,
        rsi=rsi,
        macd_hist=macd_hist,
        macd_just_turned_positive=just_turned,
    )


def calc_stop_loss_price(
    daily_df: pd.DataFrame, lookback_days: int = config.STOP_LOSS_LOOKBACK_DAYS
) -> float:
    """매수일 기준 최근 lookback_days 거래일 저가 중 최솟값 = "이전 저점" 손절선."""
    recent = daily_df.tail(lookback_days)
    return float(recent["low"].min())
