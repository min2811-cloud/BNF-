"""
아주 단순한 비밀번호 게이트. Streamlit Cloud "Who can view"(이메일 제한)와
함께 이중으로 걸어서, 폰으로 아무 링크나 눌러도 바로 못 들어오게 막는다.
"""

from __future__ import annotations

import streamlit as st

from app.secrets_util import get_secret


def require_login() -> None:
    if st.session_state.get("authenticated"):
        return

    correct_password = get_secret("APP_PASSWORD")
    if not correct_password:
        # 비밀번호가 아예 설정 안 돼있으면(로컬 테스트 등) 그냥 통과시킨다.
        st.session_state["authenticated"] = True
        return

    st.title("🔒 BNF 매매법")
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == correct_password:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 틀렸습니다.")
    st.stop()
