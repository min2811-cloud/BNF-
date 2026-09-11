"""
st.secrets에 안전하게 접근하는 헬퍼.

주의: `.streamlit/secrets.toml` 파일이 아예 없으면 `st.secrets.get(key)`조차
조용히 None을 주지 않고 예외(StreamlitSecretNotFoundError)를 던진다. 로컬에서
설정 전에 앱을 처음 켜보거나, 일부 키만 아직 안 채운 상태에서도 앱이 죽지 않고
친절한 안내 메시지를 보여주려면 이 헬퍼를 통해서만 접근해야 한다.
"""

from __future__ import annotations

import streamlit as st


def get_secret(key: str, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default
