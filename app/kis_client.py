"""
한국투자증권(KIS) Open API 연동 전담 모듈.

- 인증: access_token 발급/캐시 (이 토큰은 24시간짜리 "인증용" 토큰이며 비용이 드는
  AI 사용량 토큰과는 전혀 다른 개념이다)
- 현재가 조회 (급락 1차 필터링용, 가벼운 호출)
- 일봉(OHLCV) 조회 (지표 계산용, 무거운 호출 — 후보 종목에만 사용)

주의: TR_ID/파라미터명은 KIS 공식 문서(https://apiportal.koreainvestment.com) 기준으로
작성했지만, 실제 App Key/Secret을 발급받기 전에는 라이브로 검증할 수 없었다.
실제 키를 받으면 가장 먼저 `python -m app.kis_client`로 스모크 테스트를 돌려보고,
에러가 나면 공식 문서의 최신 TR_ID/필드명과 대조해서 고치면 된다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import requests
import streamlit as st

from app import config
from app.secrets_util import get_secret


class KISConfigError(RuntimeError):
    """App Key/Secret이 설정되지 않았을 때 — 사용자에게 보여줄 친절한 메시지용."""


class KISAPIError(RuntimeError):
    """KIS API 호출 자체가 실패했을 때."""


@dataclass
class CurrentPrice:
    ticker: str
    price: float
    change_pct: float  # 전일 대비 등락률(%)


def _get_credentials() -> tuple[str, str]:
    app_key = get_secret("KIS_APP_KEY")
    app_secret = get_secret("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise KISConfigError(
            "한국투자증권 API 키가 설정되지 않았습니다. "
            "Streamlit Secrets(또는 로컬 .streamlit/secrets.toml)에 "
            "KIS_APP_KEY, KIS_APP_SECRET을 넣어주세요."
        )
    return app_key, app_secret


_token_cache: dict = {}


def get_access_token() -> str:
    """access_token을 발급받아 캐시해둔다. 유효기간 24시간, 캐시는 그보다 짧게 잡아
    만료 전에 자동으로 재발급되게 한다.

    주의: 원래 `@st.cache_resource`를 썼는데, `streamlit run` 없이 일반
    파이썬 스크립트(백테스트 등)로 실행하면 이 캐시가 세션 컨텍스트에
    의존해서 간헐적으로 캐시 미스가 나고, 그때마다 KIS 토큰 발급 API를
    다시 호출하다가 "1분당 1회" 제한(EGW00133)에 걸려 이후 요청이 전부
    실패하는 문제가 있었다. 그래서 순수 파이썬 전역 변수로 직접 캐시한다
    (Streamlit 앱에서 돌아도 문제없이 똑같이 동작한다).
    """
    cached_token = _token_cache.get("token")
    fetched_at = _token_cache.get("fetched_at", 0.0)
    if cached_token and (time.time() - fetched_at) < config.KIS_TOKEN_CACHE_TTL_SEC:
        return cached_token

    app_key, app_secret = _get_credentials()
    url = f"{config.KIS_BASE_URL}/oauth2/tokenP"
    resp = requests.post(
        url,
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "appsecret": app_secret,
        },
        timeout=10,
    )
    if resp.status_code != 200:
        raise KISAPIError(f"토큰 발급 실패 ({resp.status_code}): {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise KISAPIError(f"토큰 발급 응답에 access_token이 없습니다: {resp.text}")
    _token_cache["token"] = token
    _token_cache["fetched_at"] = time.time()
    return token


def _headers(tr_id: str) -> dict:
    app_key, app_secret = _get_credentials()
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": tr_id,
        "custtype": "P",  # 개인
    }


def get_current_price(ticker: str) -> CurrentPrice:
    """현재가 + 전일 대비 등락률. 급락 1차 필터링에 쓰는 가벼운 호출."""
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
    }
    resp = requests.get(url, headers=_headers("FHKST01010100"), params=params, timeout=10)
    if resp.status_code != 200:
        raise KISAPIError(f"현재가 조회 실패 ({ticker}, {resp.status_code}): {resp.text}")
    output = resp.json().get("output", {})
    if not output:
        raise KISAPIError(f"현재가 조회 응답이 비어 있습니다 ({ticker}): {resp.text}")
    # 참고: 이 API는 종목명을 안 준다(업종명만 줌). 이름이 필요하면
    # app.universe.get_stock_name() 이나, 이미 알고 있는 이름(스캔 결과 등)을 쓸 것.
    return CurrentPrice(
        ticker=ticker,
        price=float(output.get("stck_prpr", 0)),
        change_pct=float(output.get("prdy_ctrt", 0)),
    )


def get_kospi_index_change_pct() -> float | None:
    """코스피 지수(0001)의 오늘 전일 대비 등락률(%). "시장 전체가 같이 빠졌는지" 표시용.
    참고용 정보라 실패해도 앱이 멈추지 않게 None을 돌려준다."""
    url = f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price"
    params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001"}
    try:
        resp = requests.get(url, headers=_headers("FHPUP02100000"), params=params, timeout=10)
        if resp.status_code != 200:
            return None
        output = resp.json().get("output") or {}
        value = output.get("bstp_nmix_prdy_ctrt")
        return float(value) if value not in (None, "") else None
    except (requests.RequestException, ValueError):
        return None


def get_daily_ohlcv(ticker: str, count: int = config.DAILY_CANDLE_COUNT) -> pd.DataFrame:
    """최근 일봉(OHLCV)을 오래된 날짜 순으로 정렬해서 반환. 지표 계산용 (무거운 호출)."""
    url = (
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/"
        "inquire-daily-itemchartprice"
    )
    today = pd.Timestamp.today().strftime("%Y%m%d")
    # 넉넉히 과거 날짜부터 조회 (주말/공휴일 감안해 count의 2배 기간을 요청)
    start = (pd.Timestamp.today() - pd.Timedelta(days=count * 2)).strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": today,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",  # 수정주가 반영
    }
    resp = requests.get(
        url, headers=_headers("FHKST03010100"), params=params, timeout=10
    )
    if resp.status_code != 200:
        raise KISAPIError(f"일봉 조회 실패 ({ticker}, {resp.status_code}): {resp.text}")
    rows = resp.json().get("output2", [])
    if not rows:
        raise KISAPIError(f"일봉 조회 응답이 비어 있습니다 ({ticker}): {resp.text}")
    df = pd.DataFrame(rows)
    df = df.rename(
        columns={
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume",
        }
    )[["date", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.sort_values("date").reset_index(drop=True)
    return df.tail(count).reset_index(drop=True)


def get_daily_ohlcv_range(
    ticker: str,
    start_date: str,
    end_date: str,
    market_div_code: str = "J",
) -> pd.DataFrame:
    """start_date~end_date(YYYYMMDD) 구간의 일봉을 오래된 날짜 순으로 정렬해서 반환.

    백테스트처럼 과거 특정 기간 전체가 필요할 때 쓴다. 1회 호출당 최대 약 100개
    봉만 돌아오므로, 구간이 길면 뒤에서부터 ~140일씩 끊어서 여러 번 호출 후 합친다.
    market_div_code="U"면 코스피 지수 등 업종/지수 조회(벤치마크 비교용).
    """
    url = (
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/"
        "inquire-daily-itemchartprice"
    )
    end_dt = pd.Timestamp(end_date)
    start_dt = pd.Timestamp(start_date)
    chunks: list[pd.DataFrame] = []
    cursor_end = end_dt
    while cursor_end >= start_dt:
        cursor_start = max(start_dt, cursor_end - pd.Timedelta(days=140))
        params = {
            "FID_COND_MRKT_DIV_CODE": market_div_code,
            "FID_INPUT_ISCD": ticker,
            "FID_INPUT_DATE_1": cursor_start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": cursor_end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        resp = requests.get(
            url, headers=_headers("FHKST03010100"), params=params, timeout=10
        )
        if resp.status_code != 200:
            raise KISAPIError(
                f"일봉(구간) 조회 실패 ({ticker}, {resp.status_code}): {resp.text}"
            )
        rows = resp.json().get("output2", [])
        if rows:
            chunks.append(pd.DataFrame(rows))
        cursor_end = cursor_start - pd.Timedelta(days=1)
        sleep_between_calls()

    if not chunks:
        raise KISAPIError(
            f"일봉(구간) 조회 응답이 비어 있습니다 ({ticker}): {start_date}~{end_date}"
        )
    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(
        columns={
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume",
        }
    )[["date", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    return df


def get_intraday_price_at(ticker: str, date: str, time_str: str = "100000") -> float | None:
    """특정 과거 날짜의 지정 시각(기본 오전 10시 정각) 체결가를 반환.

    KIS의 "국내주식 시간별시세(일별)" API(inquire-time-dailychartprice)는
    당일 분봉만 주는 게 아니라 과거 특정 날짜도 조회가 된다(실제 키로 확인함).
    요청 시각에 정확히 체결이 없으면 그 직전 가장 가까운 체결가를 준다.
    그 날짜 데이터 자체가 없으면(상장 전, 거래정지 등) None을 반환.
    """
    url = (
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/"
        "inquire-time-dailychartprice"
    )
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": date,
        "FID_INPUT_HOUR_1": time_str,
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_FAKE_TICK_INCU_YN": "N",
    }
    resp = requests.get(
        url, headers=_headers("FHKST03010230"), params=params, timeout=10
    )
    if resp.status_code != 200:
        raise KISAPIError(
            f"시간대별 체결가 조회 실패 ({ticker}, {date} {time_str}, {resp.status_code}): {resp.text}"
        )
    rows = resp.json().get("output2", [])
    if not rows:
        return None
    return float(rows[0]["stck_prpr"])


def get_kospi_index_daily_range(start_date: str, end_date: str) -> pd.DataFrame:
    """코스피 지수(0001)의 과거 일별 종가를 오래된 날짜 순으로 반환. 벤치마크 비교용.

    지수는 개별 종목과 다른 전용 엔드포인트(inquire-daily-indexchartprice)를 쓰고
    응답 필드명도 다르다(bstp_nmix_* 접두어). 1회 호출당 최대 약 100개 봉만 오므로
    구간이 길면 나눠서 호출 후 합친다.
    """
    url = (
        f"{config.KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/"
        "inquire-daily-indexchartprice"
    )
    end_dt = pd.Timestamp(end_date)
    start_dt = pd.Timestamp(start_date)
    chunks: list[pd.DataFrame] = []
    cursor_end = end_dt
    while cursor_end >= start_dt:
        cursor_start = max(start_dt, cursor_end - pd.Timedelta(days=90))
        params = {
            "FID_COND_MRKT_DIV_CODE": "U",
            "FID_INPUT_ISCD": "0001",
            "FID_INPUT_DATE_1": cursor_start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": cursor_end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
        }
        resp = requests.get(
            url, headers=_headers("FHKUP03500100"), params=params, timeout=10
        )
        if resp.status_code != 200:
            raise KISAPIError(f"코스피 지수 조회 실패 ({resp.status_code}): {resp.text}")
        rows = resp.json().get("output2", [])
        if rows:
            chunks.append(pd.DataFrame(rows))
        cursor_end = cursor_start - pd.Timedelta(days=1)
        sleep_between_calls()

    if not chunks:
        raise KISAPIError(f"코스피 지수 조회 응답이 비어 있습니다: {start_date}~{end_date}")
    df = pd.concat(chunks, ignore_index=True)
    df = df.rename(
        columns={
            "stck_bsop_date": "date",
            "bstp_nmix_oprc": "open",
            "bstp_nmix_hgpr": "high",
            "bstp_nmix_lwpr": "low",
            "bstp_nmix_prpr": "close",
            "acml_vol": "volume",
        }
    )[["date", "open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    return df


def sleep_between_calls(seconds: float = 0.05) -> None:
    """API 호출 사이 짧은 간격 — 레이트리밋 방지용."""
    time.sleep(seconds)


if __name__ == "__main__":
    # 실제 App Key/Secret을 .streamlit/secrets.toml에 넣은 뒤
    #   python -m app.kis_client
    # 로 삼성전자(005930) 현재가 조회가 되는지 확인하는 스모크 테스트.
    price = get_current_price("005930")
    print(price)
