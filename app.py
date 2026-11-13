"""ANHU Teaching Studio — entrypoint Streamlit.

Chạy cục bộ:   streamlit run app.py      (cần ANHU_DATABASE_URL, xem README)
Streamlit Community Cloud: Main file path = app.py; secrets: xem docs/DEPLOY_STREAMLIT.md

File này KHÔNG chạy migration, KHÔNG tạo tài khoản, KHÔNG import dữ liệu: mọi thao tác ghi chỉ xảy ra khi người dùng bấm nút.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

st.set_page_config(page_title="ANHU Teaching Studio", page_icon="🌿", layout="wide", initial_sidebar_state="collapsed")

from anhu.ui.main import main  # noqa: E402

main()
