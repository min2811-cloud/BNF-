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


@st.cache_resource(ttl=config.KIS_TOKEN_CACHE_TTL_SEC)
def get_access_token() -> str:
    """access_token을 발급받아 캐시해둔다. 유효기간 24시간, 캐시는 그보다 짧게 잡아
    만료 전에 자동으로 재발급되게 한다."""
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


def sleep_between_calls(seconds: float = 0.05) -> None:
    """API 호출 사이 짧은 간격 — 레이트리밋 방지용."""
    time.sleep(seconds)


if __name__ == "__main__":
    # 실제 App Key/Secret을 .streamlit/secrets.toml에 넣은 뒤
    #   python -m app.kis_client
    # 로 삼성전자(005930) 현재가 조회가 되는지 확인하는 스모크 테스트.
    price = get_current_price("005930")
    print(price)
