from __future__ import annotations

from datetime import date

import streamlit as st

from app import config, indicators, kis_client, storage, universe


def add_holding_form(
    *,
    key_prefix: str,
    default_ticker: str = "",
    default_name: str = "",
    default_stop_loss: float | None = None,
    lock_ticker: bool = False,
) -> None:
    """보유 종목 등록 폼. 급락 스캔 탭(사전 채움)과 보유종목 탭(직접 입력) 둘 다에서 쓴다.

    lock_ticker=True면 종목코드를 못 바꾸게 잠근다 — 급락 스캔에서 이미 계산해둔
    손절선(default_stop_loss)이 사용자가 임의로 바꾼 종목코드와 어긋나는 걸 막는다.
    """
    with st.form(key=f"{key_prefix}_add_holding_form"):
        ticker = st.text_input(
            "종목코드 (6자리)", value=default_ticker, disabled=lock_ticker
        )
        buy_price = st.number_input("매수가 (1주 기준, 원)", min_value=0.0, step=100.0)
        quantity = st.number_input(
            "수량 (선택 입력, 안 채우면 수익률만 계산돼요)", min_value=0.0, step=1.0
        )
        buy_date = st.date_input("매수일", value=date.today())
        lookback = st.number_input(
            "손절 기준: 최근 며칠 저가 중 최솟값으로 할까요?",
            min_value=1,
            value=config.STOP_LOSS_LOOKBACK_DAYS,
            step=1,
        )
        submitted = st.form_submit_button("보유 종목으로 등록")

    if not submitted:
        return
    if not ticker or buy_price <= 0:
        st.error("종목코드와 매수가는 꼭 입력해주세요.")
        return

    with st.spinner("손절선 계산 중..."):
        try:
            name = default_name or universe.get_stock_name(ticker) or ticker
            daily_df = kis_client.get_daily_ohlcv(ticker)
            stop_loss_price = (
                default_stop_loss
                if default_stop_loss is not None
                else indicators.calc_stop_loss_price(daily_df, int(lookback))
            )
        except Exception as e:  # noqa: BLE001 - 사용자에게 원인을 그대로 보여줌
            st.error(f"종목 정보를 가져오지 못했어요: {e}")
            return

    storage.add_holding(
        ticker=ticker,
        name=name,
        buy_price=buy_price,
        quantity=quantity if quantity > 0 else None,
        buy_date_str=buy_date.isoformat(),
        stop_loss_price=stop_loss_price,
    )
    st.success(f"{name}({ticker}) 보유 종목으로 등록했어요. 손절선: {stop_loss_price:,.0f}원")
    st.rerun()


def render() -> None:
    st.header("💼 보유 종목")

    holdings = storage.get_active_holdings()
    if not holdings:
        st.caption("아직 등록된 보유 종목이 없어요.")
    for h in holdings:
        with st.container(border=True):
            st.subheader(f"{h['name']} ({h['ticker']})")
            cols = st.columns(4)
            cols[0].metric("매수가", f"{float(h['buy_price']):,.0f}원")
            qty = h.get("quantity")
            cols[1].metric("수량", f"{qty}주" if qty not in ("", None) else "미입력")
            cols[2].metric("손절선", f"{float(h['stop_loss_price']):,.0f}원")
            cols[3].caption(f"매수일: {h['buy_date']}")

            btn_cols = st.columns(3)
            if btn_cols[0].button("현재가 새로고침", key=f"refresh_{h['id']}"):
                with st.spinner("조회 중..."):
                    try:
                        cur = kis_client.get_current_price(h["ticker"])
                        daily_df = kis_client.get_daily_ohlcv(h["ticker"])
                        snap = indicators.build_snapshot(daily_df)
                    except Exception as e:  # noqa: BLE001
                        st.error(f"조회 실패: {e}")
                    else:
                        pnl_pct = (cur.price / float(h["buy_price"]) - 1) * 100
                        st.write(
                            f"현재가 **{cur.price:,.0f}원** "
                            f"(매수가 대비 {pnl_pct:+.1f}%), 이격도 **{snap.disparity:.1f}**"
                        )
                        if snap.disparity >= config.DISPARITY_SELL_TARGET:
                            st.success("📈 익절 신호! 이격도가 목표치(100)에 도달했어요.")
                        if cur.price <= float(h["stop_loss_price"]):
                            st.error("📉 손절 신호! 현재가가 손절선 아래예요.")

            sell_price = btn_cols[1].number_input(
                "매도가(선택)", min_value=0.0, step=100.0, key=f"sell_price_{h['id']}",
                label_visibility="collapsed", placeholder="매도가(선택)",
            )
            if btn_cols[2].button("삭제", key=f"delete_{h['id']}"):
                storage.soft_delete_holding(
                    h["id"], sell_price=sell_price if sell_price > 0 else None
                )
                st.rerun()

    st.divider()
    st.subheader("직접 종목 추가")
    st.caption("급락 스캔을 거치지 않고 이미 매수한 종목을 등록하고 싶을 때 쓰세요.")
    add_holding_form(key_prefix="manual")
