"""
구글 시트를 DB처럼 쓰는 저장소 모듈.

- 추천 이력(recommendations): 오늘 이미 추천을 받았는지 판단하는 용도
- 보유 종목(holdings): 매수/손절/익절 관리, "삭제"는 status를 closed로 바꾸는
  소프트 삭제 (이력을 남겨서 나중에 승률 통계 등에 쓸 수 있게)

사전 준비 (README.md에도 안내):
1. 구글 클라우드 콘솔에서 서비스 계정을 만들고 JSON 키를 발급받는다.
2. 빈 구글 스프레드시트를 하나 만들고, 그 JSON의 client_email 주소를
   "편집자"로 공유한다.
3. Streamlit Secrets에 [gcp_service_account] 섹션으로 JSON 내용을,
   GSHEET_SPREADSHEET_ID 로 그 스프레드시트의 URL 속 ID를 넣는다.

주의: "005930" 같은 종목코드는 앞자리 0이 아주 중요한데, 구글시트가 기본적으로
숫자처럼 생긴 문자열을 멋대로 숫자로 바꿔버린다 — 저장할 때(value_input_option을
USER_ENTERED로 하면 시트 자체가 숫자로 저장해버림, 그러면 "005930"이 5930이 됨)와
읽을 때(gspread의 get_all_records가 다시 한번 숫자로 변환) 두 군데 다 문제가 될 수
있어서, 쓸 때는 "RAW"로(시트가 임의로 해석 못 하게), 읽을 때는
numericise_ignore=["all"]로(gspread가 임의로 해석 못 하게) 막아뒀다. 숫자가 필요한
곳은 호출하는 쪽에서 직접 float()/int()로 변환한다.
"""

from __future__ import annotations

import uuid
from datetime import date

import gspread
import streamlit as st

from app import config
from app.secrets_util import get_secret

RECOMMENDATION_HEADERS = ["date", "ticker", "name", "market_cap_rank", "created_at"]
HOLDING_HEADERS = [
    "id",
    "ticker",
    "name",
    "buy_price",
    "quantity",
    "buy_date",
    "stop_loss_price",
    "status",
    "sell_price",
    "sell_date",
]


class StorageConfigError(RuntimeError):
    pass


@st.cache_resource
def _get_client() -> gspread.Client:
    sa_info = get_secret("gcp_service_account")
    if not sa_info:
        raise StorageConfigError(
            "구글 서비스 계정 정보가 설정되지 않았습니다. "
            "Streamlit Secrets에 [gcp_service_account] 섹션을 추가해주세요."
        )
    return gspread.service_account_from_dict(dict(sa_info))


def _get_spreadsheet():
    client = _get_client()
    sheet_id = get_secret("GSHEET_SPREADSHEET_ID")
    if sheet_id:
        return client.open_by_key(sheet_id)
    try:
        return client.open(config.SPREADSHEET_NAME)
    except gspread.SpreadsheetNotFound as e:
        raise StorageConfigError(
            f"'{config.SPREADSHEET_NAME}' 스프레드시트를 찾을 수 없습니다. "
            "직접 만들어서 서비스 계정 이메일로 공유하거나, "
            "Secrets에 GSHEET_SPREADSHEET_ID를 넣어주세요."
        ) from e


def _get_or_create_worksheet(sheet_name: str, headers: list[str]):
    spreadsheet = _get_spreadsheet()
    try:
        ws = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
        ws.append_row(headers)
        return ws
    if ws.row_values(1) != headers:
        # 헤더가 없거나 다르면 맞춰준다 (기존 데이터는 건드리지 않음)
        if not ws.row_values(1):
            ws.append_row(headers)
    return ws


def _recommendations_ws():
    return _get_or_create_worksheet(config.SHEET_RECOMMENDATIONS, RECOMMENDATION_HEADERS)


def _holdings_ws():
    return _get_or_create_worksheet(config.SHEET_HOLDINGS, HOLDING_HEADERS)


# ---------- 추천 이력 ----------

def has_recommendation_today() -> bool:
    today = date.today().isoformat()
    records = _recommendations_ws().get_all_records(numericise_ignore=["all"])
    return any(r.get("date") == today for r in records)


def get_today_recommendation() -> list[dict]:
    today = date.today().isoformat()
    records = _recommendations_ws().get_all_records(numericise_ignore=["all"])
    return [r for r in records if r.get("date") == today]


def save_recommendation(universe: list) -> None:
    """universe: app.universe.UniverseStock 리스트. 오늘 날짜로 한 번에 저장."""
    today = date.today().isoformat()
    ws = _recommendations_ws()
    rows = [
        [today, u.ticker, u.name, u.market_cap_rank, today]
        for u in universe
    ]
    ws.append_rows(rows, value_input_option="RAW")


# ---------- 보유 종목 ----------

def get_active_holdings() -> list[dict]:
    records = _holdings_ws().get_all_records(numericise_ignore=["all"])
    return [r for r in records if r.get("status") == "active"]


def add_holding(
    ticker: str,
    name: str,
    buy_price: float,
    quantity: float | None,
    buy_date_str: str,
    stop_loss_price: float,
) -> str:
    holding_id = uuid.uuid4().hex[:8]
    ws = _holdings_ws()
    ws.append_row(
        [
            holding_id,
            ticker,
            name,
            buy_price,
            quantity if quantity is not None else "",
            buy_date_str,
            stop_loss_price,
            "active",
            "",
            "",
        ],
        value_input_option="RAW",
    )
    return holding_id


def soft_delete_holding(holding_id: str, sell_price: float | None = None) -> None:
    ws = _holdings_ws()
    cell = ws.find(holding_id, in_column=1)
    if cell is None:
        raise StorageConfigError(f"보유 종목 id '{holding_id}'를 찾지 못했습니다.")
    row = cell.row
    status_col = HOLDING_HEADERS.index("status") + 1
    sell_price_col = HOLDING_HEADERS.index("sell_price") + 1
    sell_date_col = HOLDING_HEADERS.index("sell_date") + 1
    ws.update_cell(row, status_col, "closed")
    if sell_price is not None:
        ws.update_cell(row, sell_price_col, sell_price)
    ws.update_cell(row, sell_date_col, date.today().isoformat())
