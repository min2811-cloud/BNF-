from __future__ import annotations

import os
import sys

# `streamlit run app/main.py`로 실행하면 이 파일이 있는 app/ 폴더만 sys.path에
# 잡혀서 `from app import ...`가 깨진다. 저장소 루트를 sys.path에 추가해준다.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st  # noqa: E402

from app import auth  # noqa: E402
from app.tabs import drop_tab, holdings_tab, recommend_tab  # noqa: E402

st.set_page_config(page_title="BNF 매매법", page_icon="📈", layout="wide")

auth.require_login()

st.title("📈 BNF 매매법")
st.caption("우량 대형주 저점매수 보조 도구 — 신호만 보여줘요, 주문은 직접 하세요.")

tab1, tab2, tab3 = st.tabs(["🏆 우량주 추천", "📉 급락 스캔", "💼 보유 종목"])

with tab1:
    recommend_tab.render()

with tab2:
    drop_tab.render()

with tab3:
    holdings_tab.render()
