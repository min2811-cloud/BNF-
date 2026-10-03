"""
거래일(장 열리는 날) 계산. 매도 예정일 = 매수일로부터 config.HOLD_TRADING_DAYS 거래일 뒤.

예) 월 매수 -> 목 매도, 금 매수 -> 수 매도. 사이에 공휴일이 끼면 그만큼 뒤로 밀린다.
공휴일은 `holidays` 패키지의 한국 공휴일(대체공휴일 포함) + 증시만 쉬는 날
(근로자의 날 5/1, 연말 휴장 12/31)을 쓴다. 임시공휴일은 패키지 업데이트가 늦을 수
있으니, 이상하면 EXTRA_CLOSED_DAYS에 직접 날짜를 추가하면 된다.
"""

from __future__ import annotations

from datetime import date, timedelta

import holidays

from app import config

# 패키지에 없는 임시 휴장일이 생기면 여기에 date(2026, 1, 2) 식으로 추가
EXTRA_CLOSED_DAYS: set[date] = set()


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    if d in holidays.KR(years=d.year):
        return False
    if (d.month, d.day) in ((5, 1), (12, 31)):
        return False
    return d not in EXTRA_CLOSED_DAYS


def add_trading_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if is_trading_day(d):
            n -= 1
    return d


def sell_by_date(buy_date: date) -> date:
    return add_trading_days(buy_date, config.HOLD_TRADING_DAYS)


def trading_days_left(today: date, target: date) -> int:
    """오늘부터 target까지 남은 거래일 수(오늘 제외, target 포함). 지났으면 음수."""
    if target <= today:
        n, d = 0, target
        while d < today:
            d += timedelta(days=1)
            if is_trading_day(d):
                n -= 1
        return n
    n, d = 0, today
    while d < target:
        d += timedelta(days=1)
        if is_trading_day(d):
            n += 1
    return n
