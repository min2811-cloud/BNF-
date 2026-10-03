"""
급락 스캔 탭 — 사장님 매매 규칙 2단계.

① 오전 10시: 우량주 중 전일 대비 -5% 이하 종목을 찾아 구글시트(scans)에 저장
② 오후 2시 50분: 저장해둔 종목만 다시 조회 -> 여전히 -5% 이하면 "매수 대상"
   (수량 = 100만원 이내 최대, 1주가 넘으면 1주). 주문은 사장님이 증권사 앱에서 직접.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app import config, indicators, kis_client, storage, trading_days, universe
from app.config import now_kst, today_kst
from app.tabs.holdings_tab import add_holding_form


def _market_badge(kospi_change_pct: float | None) -> str:
    if kospi_change_pct is None:
        return "코스피 등락률 확인 불가"
    if kospi_change_pct <= config.MARKET_PANIC_KOSPI_DROP_PCT:
        return f"✅ 시장 같이 하락 (코스피 {kospi_change_pct:+.2f}%)"
    return f"⚠️ 혼자 하락 (코스피 {kospi_change_pct:+.2f}%)"


def _show_market_note(kospi_change_pct: float | None) -> None:
    badge = _market_badge(kospi_change_pct)
    note = (
        "백테스트(2~9월)에서는 코스피도 -1% 이상 같이 빠진 날 산 경우만 돈이 됐고, "
        "그 종목만 혼자 빠진 날은 손실이었어요. 참고만 하세요."
    )
    if kospi_change_pct is not None and kospi_change_pct <= config.MARKET_PANIC_KOSPI_DROP_PCT:
        st.success(f"{badge}  \n{note}")
    else:
        st.warning(f"{badge}  \n{note}")


def _indicators_for(ticker: str) -> dict:
    """BNF 원래 지표(이격도·RSI·MACD). 사장님 규칙에는 안 쓰지만 참고용으로 같이 보여준다.
    일봉 조회가 무거운 호출이라 급락 종목에만 쓴다. 실패하면 빈 값."""
    try:
        snap = indicators.build_snapshot(kis_client.get_daily_ohlcv(ticker))
    except Exception:  # noqa: BLE001
        return {}
    return {
        "disparity": round(snap.disparity, 1),
        "rsi": round(snap.rsi, 1),
        "macd_hist": round(snap.macd_hist, 1),
        "macd_just_turned": "Y" if snap.macd_just_turned_positive else "N",
        "bnf_all_ok": "Y" if snap.all_conditions_met else "N",
    }


def _indicator_columns(r: dict) -> dict:
    """표에 붙일 지표 칸. 구글시트에서 읽은 값(문자열)과 방금 계산한 값 둘 다 처리."""
    def num(v):
        return None if v in ("", None) else float(v)

    disparity, rsi, macd = num(r.get("disparity")), num(r.get("rsi")), num(r.get("macd_hist"))
    if disparity is None:
        return {"BNF조건": "-", "이격도": "-", "RSI": "-", "MACD": "-"}
    turned = " (방금 전환)" if r.get("macd_just_turned") == "Y" else ""
    return {
        "BNF조건": "✅ 충족" if r.get("bnf_all_ok") == "Y" else "❌",
        "이격도": f"{disparity:.1f}",
        "RSI": f"{rsi:.1f}",
        "MACD": f"{macd:.0f}{turned}",
    }


def _run_scan() -> None:
    stocks = universe.get_top_market_cap_universe()
    progress = st.progress(0.0, text="급락 종목 찾는 중...")
    found = []
    for i, s in enumerate(stocks):
        try:
            cur = kis_client.get_current_price(s.ticker)
            if cur.change_pct <= config.DROP_THRESHOLD_PCT:
                found.append(
                    {"ticker": s.ticker, "name": s.name, "price": cur.price, "change_pct": cur.change_pct}
                )
        except Exception:
            pass  # 개별 종목 조회 실패는 건너뛴다 (전체 스캔을 막지 않음)
        kis_client.sleep_between_calls()
        progress.progress((i + 1) / len(stocks), text=f"{i + 1}/{len(stocks)} 종목 확인 중...")
    progress.empty()

    if found:
        progress2 = st.progress(0.0, text="이격도·RSI·MACD 계산 중...")
        for i, r in enumerate(found):
            r.update(_indicators_for(r["ticker"]))
            kis_client.sleep_between_calls()
            progress2.progress((i + 1) / len(found), text=f"지표 {i + 1}/{len(found)} 계산 중...")
        progress2.empty()

    kospi = kis_client.get_kospi_index_change_pct()
    storage.save_scan(found, kospi)


def _render_scan_step() -> None:
    st.subheader(f"① {config.SCAN_HOUR_LABEL} 급락 스캔")
    st.caption(
        f"우량주 중 전일 대비 {config.DROP_THRESHOLD_PCT:.0f}% 이하로 빠진 종목을 찾아서 저장해요. "
        f"{config.BUY_CHECK_LABEL}에 이 목록을 다시 확인해요."
    )
    if st.button("급락 종목 찾기", type="primary", key="scan_btn"):
        with st.spinner("스캔 중... (1분 정도 걸려요)"):
            _run_scan()
        st.rerun()

    if not storage.has_scan_today():
        st.caption("오늘은 아직 스캔하지 않았어요.")
        return

    rows = storage.get_today_scan()
    if not rows:
        st.info(f"오늘 스캔 결과, {config.DROP_THRESHOLD_PCT:.0f}% 이상 급락한 우량주가 없었어요.")
        return

    scanned_at = rows[0].get("scanned_at", "")
    kospi_raw = rows[0].get("kospi_change_pct", "")
    kospi = float(kospi_raw) if kospi_raw not in ("", None) else None
    st.caption(f"스캔 시각: {scanned_at} · {_market_badge(kospi)}")
    df = pd.DataFrame(rows)
    df["_chg"] = pd.to_numeric(df["scan_change_pct"])
    df["등락률"] = df["_chg"].map(lambda v: f"{v:+.1f}%")
    df["가격"] = pd.to_numeric(df["scan_price"]).map(lambda v: f"{v:,.0f}원")
    ind = pd.DataFrame([_indicator_columns(r) for r in rows], index=df.index)
    df = pd.concat([df, ind], axis=1).rename(columns={"name": "종목명", "ticker": "종목코드"})
    st.dataframe(
        df.sort_values("_chg")[["종목명", "종목코드", "등락률", "가격", "BNF조건", "이격도", "RSI", "MACD"]],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        f"BNF조건 = 이격도 {config.DISPARITY_BUY_MAX} 이하 + RSI {config.RSI_OVERSOLD} 미만 + MACD 양수. "
        "사장님 규칙에는 안 쓰는 참고 정보예요."
    )


def _run_buy_check(rows: list[dict]) -> None:
    targets = []
    progress = st.progress(0.0, text="다시 확인 중...")
    for i, r in enumerate(rows):
        try:
            cur = kis_client.get_current_price(r["ticker"])
        except Exception:
            cur = None
        if cur is not None:
            still_down = cur.change_pct <= config.DROP_THRESHOLD_PCT
            targets.append(
                {
                    # 매수 대상만 지표를 지금 시점으로 다시 계산(장중에 값이 바뀌므로)
                    **(_indicators_for(r["ticker"]) if still_down else {}),
                    "ticker": r["ticker"],
                    "name": r["name"],
                    "scan_change_pct": float(r["scan_change_pct"]),
                    "price": cur.price,
                    "change_pct": cur.change_pct,
                    "still_down": still_down,
                }
            )
        kis_client.sleep_between_calls()
        progress.progress((i + 1) / len(rows), text=f"{i + 1}/{len(rows)} 종목 확인 중...")
    progress.empty()
    st.session_state["buy_check"] = {
        "date": today_kst().isoformat(),
        "checked_at": now_kst().strftime("%H:%M"),
        "kospi": kis_client.get_kospi_index_change_pct(),
        "targets": targets,
    }


def _render_buy_step() -> None:
    st.subheader(f"② {config.BUY_CHECK_LABEL} 매수 확인")
    st.caption(
        f"10시에 찾은 종목이 지금도 {config.DROP_THRESHOLD_PCT:.0f}% 이하면 매수 대상이에요. "
        f"종목당 {config.BUY_BUDGET_WON:,}원 이내로, 1주가 그보다 비싸면 1주만."
    )

    rows = storage.get_today_scan()
    if not rows:
        st.caption("오늘 10시 스캔 결과가 없어서 확인할 종목이 없어요.")
        return

    if now_kst().hour < config.BUY_CHECK_HOUR:
        st.info(f"아직 이른 시간이에요. 규칙상 확인은 {config.BUY_CHECK_LABEL}에 해요. (눌러볼 수는 있어요)")

    if st.button("지금 매수 대상 확인", type="primary", key="buy_check_btn"):
        with st.spinner("확인 중..."):
            _run_buy_check(rows)

    check = st.session_state.get("buy_check")
    if not check or check["date"] != today_kst().isoformat():
        return

    st.caption(f"확인 시각: {check['checked_at']}")
    _show_market_note(check["kospi"])

    buys = [t for t in check["targets"] if t["still_down"]]
    dropped = [t for t in check["targets"] if not t["still_down"]]
    sell_by = trading_days.sell_by_date(today_kst())

    if not buys:
        st.info("지금은 매수 대상이 없어요. (10시 종목들이 -5% 위로 회복했어요)")
    else:
        st.markdown(f"**🛒 매수 대상 {len(buys)}종목** · 매도 예정일 **{sell_by:%m/%d}({'월화수목금토일'[sell_by.weekday()]})**")
        total = 0.0
        table = []
        for t in sorted(buys, key=lambda x: x["change_pct"]):
            qty = config.buy_quantity(t["price"])
            total += qty * t["price"]
            table.append(
                {
                    "종목명": t["name"],
                    "지금 등락률": f"{t['change_pct']:+.1f}%",
                    "현재가": f"{t['price']:,.0f}원",
                    "수량": f"{qty}주",
                    "금액": f"{qty * t['price']:,.0f}원",
                    "손절가(-4%)": f"{t['price'] * (1 + config.STOP_LOSS_PCT / 100):,.0f}원",
                    "익절가(+10%)": f"{t['price'] * (1 + config.TAKE_PROFIT_PCT / 100):,.0f}원",
                    **_indicator_columns(t),
                }
            )
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
        st.caption(f"전부 사면 약 {total:,.0f}원 필요해요. 손절가·익절가는 지금 가격 기준이고, 실제 체결가로 다시 계산돼요.")

    if dropped:
        with st.expander(f"회복해서 제외된 종목 {len(dropped)}개"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {"종목명": t["name"], "10시": f"{t['scan_change_pct']:+.1f}%", "지금": f"{t['change_pct']:+.1f}%"}
                        for t in dropped
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    if buys:
        st.divider()
        st.markdown("**증권사 앱에서 샀으면 보유 종목으로 등록하세요**")
        options = {f"{t['name']} ({t['ticker']})": t for t in buys}
        picked = options[st.selectbox("등록할 종목", list(options.keys()), key="buy_pick")]
        add_holding_form(
            key_prefix=f"drop_{picked['ticker']}",
            default_ticker=picked["ticker"],
            default_name=picked["name"],
            default_price=picked["price"],
            lock_ticker=True,
        )


def render() -> None:
    st.header("📉 급락 스캔 · 매수 확인")
    _render_scan_step()
    st.divider()
    _render_buy_step()
