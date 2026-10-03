from __future__ import annotations

from datetime import date

import streamlit as st

from app import config, kis_client, storage, trading_days, universe
from app.config import today_kst

WEEKDAYS = "월화수목금토일"


def _stop_price(buy_price: float) -> float:
    return buy_price * (1 + config.STOP_LOSS_PCT / 100)


def _take_price(buy_price: float) -> float:
    return buy_price * (1 + config.TAKE_PROFIT_PCT / 100)


def add_holding_form(
    *,
    key_prefix: str,
    default_ticker: str = "",
    default_name: str = "",
    default_price: float | None = None,
    lock_ticker: bool = False,
) -> None:
    """보유 종목 등록 폼. 급락 스캔 탭(사전 채움)과 보유종목 탭(직접 입력) 둘 다에서 쓴다.
    손절가는 규칙대로 매수가 x (1 + STOP_LOSS_PCT%)로 자동 계산한다."""
    default_qty = config.buy_quantity(default_price) if default_price else 0
    with st.form(key=f"{key_prefix}_add_holding_form"):
        ticker = st.text_input(
            "종목코드 (6자리)", value=default_ticker, disabled=lock_ticker
        )
        buy_price = st.number_input(
            "실제 매수가 (1주, 원)", min_value=0.0, step=100.0,
            value=float(default_price or 0.0),
        )
        quantity = st.number_input(
            "수량 (주)", min_value=0, step=1, value=int(default_qty),
        )
        buy_date = st.date_input("매수일", value=today_kst())
        submitted = st.form_submit_button("보유 종목으로 등록")

    if not submitted:
        return
    if not ticker or buy_price <= 0:
        st.error("종목코드와 매수가는 꼭 입력해주세요.")
        return

    name = default_name or universe.get_stock_name(ticker) or ticker
    stop = _stop_price(buy_price)
    storage.add_holding(
        ticker=ticker,
        name=name,
        buy_price=buy_price,
        quantity=quantity if quantity > 0 else None,
        buy_date_str=buy_date.isoformat(),
        stop_loss_price=round(stop),
    )
    sell_by = trading_days.sell_by_date(buy_date)
    st.success(
        f"{name}({ticker}) 등록했어요. 손절가 {stop:,.0f}원 · 익절가 {_take_price(buy_price):,.0f}원 · "
        f"매도 예정일 {sell_by:%m/%d}({WEEKDAYS[sell_by.weekday()]})"
    )
    st.rerun()


def _status(pnl_pct: float | None, days_left: int) -> tuple[str, str]:
    """(종류, 문구). 종류는 st.error/success/warning/info 중 어느 걸로 보여줄지."""
    if pnl_pct is not None and pnl_pct <= config.STOP_LOSS_PCT:
        return "error", f"🔴 손절하세요! 매수가 대비 {pnl_pct:+.1f}% (기준 {config.STOP_LOSS_PCT:.0f}%)"
    if pnl_pct is not None and pnl_pct >= config.TAKE_PROFIT_PCT:
        return "success", f"🟢 익절하세요! 매수가 대비 {pnl_pct:+.1f}% (기준 +{config.TAKE_PROFIT_PCT:.0f}%)"
    if days_left < 0:
        return "error", f"🟠 매도 예정일이 {-days_left}거래일 지났어요. 매도하세요!"
    if days_left == 0:
        return "warning", "🟡 오늘이 매도 예정일이에요. 오늘 매도하세요."
    return "info", f"보유 중 · 매도일까지 {days_left}거래일 남음"


def render() -> None:
    st.header("💼 보유 종목")
    st.caption(
        f"규칙: {config.STOP_LOSS_PCT:.0f}% 손절 / +{config.TAKE_PROFIT_PCT:.0f}% 익절 / "
        f"아니면 {config.HOLD_TRADING_DAYS}거래일째 매도 (휴일은 건너뜀)"
    )

    holdings = storage.get_active_holdings()
    if not holdings:
        st.caption("아직 등록된 보유 종목이 없어요.")
    else:
        if st.button("🔄 전체 현재가 새로고침", type="primary"):
            prices = {}
            with st.spinner("조회 중..."):
                for h in holdings:
                    try:
                        prices[h["ticker"]] = kis_client.get_current_price(h["ticker"]).price
                    except Exception:  # noqa: BLE001 - 한 종목 실패가 전체를 막지 않게
                        pass
                    kis_client.sleep_between_calls()
            st.session_state["holding_prices"] = prices
        prices = st.session_state.get("holding_prices", {})

        today = today_kst()
        total_invested = 0.0
        total_pnl = 0.0
        for h in holdings:
            buy_price = float(h["buy_price"])
            qty = int(float(h["quantity"])) if h.get("quantity") not in ("", None) else 0
            buy_date = date.fromisoformat(h["buy_date"])
            sell_by = trading_days.sell_by_date(buy_date)
            days_left = trading_days.trading_days_left(today, sell_by)
            cur = prices.get(h["ticker"])
            pnl_pct = (cur / buy_price - 1) * 100 if cur else None
            total_invested += buy_price * qty
            if cur and qty:
                total_pnl += (cur - buy_price) * qty

            with st.container(border=True):
                st.markdown(f"**{h['name']}** ({h['ticker']})")
                kind, msg = _status(pnl_pct, days_left)
                getattr(st, kind)(msg)

                cols = st.columns(3)
                cols[0].metric("매수가", f"{buy_price:,.0f}원", f"{qty}주" if qty else "수량 미입력", delta_color="off")
                if cur:
                    pnl_won = (cur - buy_price) * qty
                    cols[1].metric("현재가", f"{cur:,.0f}원", f"{pnl_pct:+.1f}% ({pnl_won:+,.0f}원)")
                else:
                    cols[1].metric("현재가", "-", "새로고침을 눌러주세요", delta_color="off")
                cols[2].metric("매도 예정일", f"{sell_by:%m/%d}({WEEKDAYS[sell_by.weekday()]})", f"매수 {buy_date:%m/%d}", delta_color="off")
                st.caption(
                    f"손절가 {_stop_price(buy_price):,.0f}원 · 익절가 {_take_price(buy_price):,.0f}원"
                )

                with st.expander("매도 완료 처리"):
                    sell_price = st.number_input(
                        "매도가(선택)", min_value=0.0, step=100.0, key=f"sell_price_{h['id']}",
                    )
                    if st.button("매도 완료 — 목록에서 빼기", key=f"delete_{h['id']}"):
                        storage.soft_delete_holding(
                            h["id"], sell_price=sell_price if sell_price > 0 else None
                        )
                        st.rerun()

        summary = f"보유 {len(holdings)}종목 · 투입금 {total_invested:,.0f}원"
        if prices:
            summary += f" · 평가손익 {total_pnl:+,.0f}원"
        st.caption(summary)

    st.divider()
    st.subheader("직접 종목 추가")
    st.caption("급락 스캔을 거치지 않고 이미 매수한 종목을 등록하고 싶을 때 쓰세요.")
    add_holding_form(key_prefix="manual")
