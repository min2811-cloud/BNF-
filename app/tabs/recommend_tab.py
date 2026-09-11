from __future__ import annotations

import pandas as pd
import streamlit as st

from app import config, storage, universe


def render() -> None:
    st.header("🏆 우량주 추천")
    st.caption(
        f"코스피 시가총액 상위 {config.TOP_N}위 안에서 자동으로 골라드려요. "
        "하루에 한 번만 새로 받을 수 있어요."
    )

    already = storage.has_recommendation_today()

    if st.button("대형주 우량주 추천해줘", disabled=already, type="primary"):
        with st.spinner("시가총액 상위 종목을 계산하는 중..."):
            stocks = universe.get_top_market_cap_universe()
            storage.save_recommendation(stocks)
        st.rerun()

    if already:
        st.info("오늘은 이미 추천을 받으셨어요. 아래는 오늘 추천 목록이에요. (내일 다시 눌러주세요)")

    todays = storage.get_today_recommendation()
    if todays:
        df = pd.DataFrame(todays).sort_values("market_cap_rank")
        df = df.rename(
            columns={"market_cap_rank": "시총순위", "name": "종목명", "ticker": "종목코드"}
        )
        st.dataframe(
            df[["시총순위", "종목명", "종목코드"]], hide_index=True, use_container_width=True
        )
    else:
        st.caption("아직 오늘의 추천이 없어요. 위 버튼을 눌러주세요.")
