from __future__ import annotations

import pandas as pd
import streamlit as st

from app import config, indicators, kis_client, universe
from app.tabs.holdings_tab import add_holding_form


def _run_scan() -> None:
    stocks = universe.get_top_market_cap_universe()

    # 1단계: 전 종목 현재가만 가볍게 조회해서 급락 후보를 거른다.
    progress = st.progress(0.0, text="1단계: 급락 후보 찾는 중...")
    candidates = []
    for i, s in enumerate(stocks):
        try:
            cur = kis_client.get_current_price(s.ticker)
            if cur.change_pct <= config.DROP_THRESHOLD_PCT:
                candidates.append((s, cur))
        except Exception:
            pass  # 개별 종목 조회 실패는 건너뛴다 (전체 스캔을 막지 않음)
        kis_client.sleep_between_calls()
        progress.progress((i + 1) / len(stocks), text=f"1단계: {i + 1}/{len(stocks)} 종목 확인 중...")
    progress.empty()

    # 2단계: 후보 종목만 일봉을 조회해 지표 계산 (무거운 호출은 소수 종목만)
    results = []
    daily_cache: dict[str, pd.DataFrame] = {}
    if candidates:
        progress2 = st.progress(0.0, text="2단계: 지표 계산 중...")
        for i, (s, cur) in enumerate(candidates):
            try:
                daily_df = kis_client.get_daily_ohlcv(s.ticker)
                snap = indicators.build_snapshot(daily_df)
                daily_cache[s.ticker] = daily_df
                results.append(
                    {
                        "ticker": s.ticker,
                        "name": s.name,
                        "price": cur.price,
                        "change_pct": cur.change_pct,
                        "disparity": snap.disparity,
                        "rsi": snap.rsi,
                        "macd_hist": snap.macd_hist,
                        "macd_just_turned": snap.macd_just_turned_positive,
                        "all_ok": snap.all_conditions_met,
                    }
                )
            except Exception:
                pass
            kis_client.sleep_between_calls()
            progress2.progress(
                (i + 1) / len(candidates), text=f"2단계: {i + 1}/{len(candidates)} 종목 확인 중..."
            )
        progress2.empty()

    st.session_state["drop_results"] = results
    st.session_state["drop_daily_cache"] = daily_cache


def render() -> None:
    st.header("📉 급락 스캔")
    st.caption(
        f"우량주 중 전일 대비 {config.DROP_THRESHOLD_PCT:.0f}% 이상 급락한 종목을 찾아서, "
        "이격도·RSI·MACD 조건을 확인해드려요."
    )

    if st.button("급락 종목 찾기", type="primary"):
        with st.spinner("스캔 준비 중..."):
            _run_scan()

    results = st.session_state.get("drop_results")
    if results is None:
        st.caption("아직 스캔하지 않았어요. 위 버튼을 눌러주세요.")
        return
    if not results:
        st.info(f"오늘은 {config.DROP_THRESHOLD_PCT:.0f}% 이상 급락한 우량주가 없어요.")
        return

    df = pd.DataFrame(results).sort_values(
        ["all_ok", "change_pct"], ascending=[False, True]
    )
    display_df = df.copy()
    display_df["매수조건"] = display_df["all_ok"].map({True: "✅ 충족", False: "❌"})
    display_df["이격도"] = display_df["disparity"].map(lambda v: f"{v:.1f}")
    display_df["RSI"] = display_df["rsi"].map(lambda v: f"{v:.1f}")
    display_df["MACD히스토그램"] = display_df.apply(
        lambda r: f"{r['macd_hist']:.0f}" + (" (방금 전환)" if r["macd_just_turned"] else ""),
        axis=1,
    )
    display_df["현재가"] = display_df["price"].map(lambda v: f"{v:,.0f}원")
    display_df["등락률"] = display_df["change_pct"].map(lambda v: f"{v:+.1f}%")
    display_df["종목명"] = display_df["name"]
    display_df["종목코드"] = display_df["ticker"]

    st.dataframe(
        display_df[
            ["매수조건", "종목명", "종목코드", "현재가", "등락률", "이격도", "RSI", "MACD히스토그램"]
        ],
        hide_index=True,
        use_container_width=True,
    )

    st.divider()
    st.subheader("보유 종목으로 등록")
    ticker_options = {f"{r['name']} ({r['ticker']})": r for r in results}
    picked_label = st.selectbox("등록할 종목을 골라주세요", list(ticker_options.keys()))
    picked = ticker_options[picked_label]
    daily_cache = st.session_state.get("drop_daily_cache", {})
    daily_df = daily_cache.get(picked["ticker"])
    default_stop_loss = (
        indicators.calc_stop_loss_price(daily_df) if daily_df is not None else None
    )
    add_holding_form(
        key_prefix="drop",
        default_ticker=picked["ticker"],
        default_name=picked["name"],
        default_stop_loss=default_stop_loss,
        lock_ticker=True,
    )
