"""Kịch bản dùng cho streamlit.testing (AppTest): chạy đúng khung ứng dụng, có thể ép hiển thị một trang."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from anhu.ui.main import main  # noqa: E402

main(test_page=st.session_state.get("_test_page"))
