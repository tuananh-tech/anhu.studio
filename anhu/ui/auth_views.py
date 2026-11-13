"""Màn hình trước khi đăng nhập: khởi tạo quản trị (một lần), đăng nhập, kích hoạt bằng mã, đổi mật khẩu bắt buộc."""
from __future__ import annotations

import streamlit as st

from ..db import tx
from ..services import BizError, accounts, core
from .common import allowed_emails, cfg, database_url, flash, show_flash


def _brand(sub: str):
    st.html("<div class='anhu-brand' style='font-size:1.35rem'>ANHU <span>Teaching Studio</span></div>")
    st.caption(sub)


def _client() -> str:
    try:
        ua = st.context.headers.get("User-Agent", "")
    except Exception:
        ua = ""
    return ("Điện thoại · " if "Mobile" in ua else "Máy tính · ") + ua[:120]


def bootstrap_view():
    _brand("Thiết lập lần đầu")
    st.info("Hệ thống chưa có tài khoản nào. Bước này tạo **quản trị viên đầu tiên** và chỉ làm được **một lần**. "
            "Bạn cần *mã khởi tạo* do người cài đặt đặt trong secrets của máy chủ (ANHU_BOOTSTRAP_TOKEN).")
    with st.form("bootstrap", clear_on_submit=False):
        tok = st.text_input("Mã khởi tạo", type="password")
        email = st.text_input("Email của bạn")
        name = st.text_input("Tên hiển thị", placeholder="VD: Cô An")
        p1 = st.text_input("Mật khẩu (≥ 10 ký tự)", type="password")
        p2 = st.text_input("Nhập lại mật khẩu", type="password")
        go = st.form_submit_button("Tạo tài khoản quản trị", type="primary", width="stretch")
    if go:
        try:
            with tx(database_url(), None, "khởi tạo quản trị viên đầu tiên") as c:
                accounts.bootstrap_admin(c, given_token=tok, expected_token=cfg("ANHU_BOOTSTRAP_TOKEN"),
                                         expires=cfg("ANHU_BOOTSTRAP_EXPIRES"), email=email, name=name, password=p1, password2=p2)
        except BizError as e:
            st.error(str(e))
            return
        except Exception as e:  # noqa: BLE001
            if "BOOTSTRAP_DONE" in str(e):
                st.error("Hệ thống đã được khởi tạo — bước này đã bị khóa vĩnh viễn. Hãy đăng nhập.")
                return
            raise
        flash("Đã tạo quản trị viên. Hãy đăng nhập. Nên xóa ANHU_BOOTSTRAP_TOKEN khỏi secrets (bước này đã bị khóa vĩnh viễn).")
        st.rerun()


def login_view():
    _brand("Quản lý lịch dạy & chấm công")
    show_flash()
    if st.session_state.pop("_go_login", False):   # đặt TRƯỚC khi tạo widget
        st.session_state["auth_mode"] = "Đăng nhập"
    if st.session_state.get("auth_mode") not in ("Đăng nhập", "Tôi có mã kích hoạt"):
        st.session_state["auth_mode"] = "Đăng nhập"
    mode = st.segmented_control("Chọn", ["Đăng nhập", "Tôi có mã kích hoạt"], key="auth_mode",
                                label_visibility="collapsed")
    if mode == "Tôi có mã kích hoạt":
        return activate_view()
    with st.form("login"):
        email = st.text_input("Email", autocomplete="email")
        pw = st.text_input("Mật khẩu", type="password", autocomplete="current-password")
        remember = st.checkbox("Ghi nhớ đăng nhập trên thiết bị này (tối đa 12 giờ)", value=True,
                               help="Tải lại trang không phải đăng nhập lại. Bỏ chọn nếu dùng máy chung.")
        go = st.form_submit_button("Đăng nhập", type="primary", width="stretch")
    if go:
        try:
            with tx(database_url()) as c:
                u = accounts.login(c, email, pw, allowed_emails(), _client())
                tok = accounts.remember_device(c, u["session"]) if (u and remember) else None
        except BizError as e:
            st.error(str(e))
            return
        if not u:
            st.error("Email hoặc mật khẩu không đúng, hoặc tài khoản đã bị khóa.")
            return
        st.session_state.clear()
        st.session_state["sid"] = u["session"]
        if tok:
            st.session_state["_dev_set"] = tok
        st.rerun()
    st.caption("Quên mật khẩu? Nhờ quản trị viên tạo **mã đặt lại** (Cài đặt → Tài khoản), rồi chọn “Tôi có mã kích hoạt”.")


def activate_view():
    qp = st.query_params
    st.markdown("#### Kích hoạt tài khoản / đặt lại mật khẩu")
    st.caption("Nhập email và mã một lần mà quản trị viên gửi cho bạn. Mã có hạn và chỉ dùng được một lần.")
    with st.form("activate"):
        email = st.text_input("Email", value=qp.get("email", ""))
        code = st.text_input("Mã kích hoạt", value=qp.get("ma", ""), placeholder="XXXXX-XXXXX-XXXXX-XXXXX")
        name = st.text_input("Tên hiển thị (chỉ khi kích hoạt tài khoản mới, có thể để trống)")
        p1 = st.text_input("Mật khẩu mới (≥ 10 ký tự)", type="password", autocomplete="new-password")
        p2 = st.text_input("Nhập lại mật khẩu", type="password", autocomplete="new-password")
        go = st.form_submit_button("Kích hoạt", type="primary", width="stretch")
    if go:
        try:
            with tx(database_url()) as c:
                accounts.accept_invite(c, email=email, token=code, password=p1, password2=p2, name=name)
        except BizError as e:
            st.error(str(e))
            return
        st.query_params.clear()
        st.session_state["_go_login"] = True
        flash("Đã kích hoạt. Hãy đăng nhập bằng mật khẩu vừa đặt.")
        st.rerun()


def must_change_view(u):
    _brand("Đổi mật khẩu")
    st.warning("Tài khoản đang dùng mật khẩu tạm — hãy đặt mật khẩu mới trước khi tiếp tục.")
    with st.form("mustchange"):
        old = st.text_input("Mật khẩu hiện tại", type="password")
        p1 = st.text_input("Mật khẩu mới (≥ 10 ký tự)", type="password")
        p2 = st.text_input("Nhập lại", type="password")
        go = st.form_submit_button("Đổi mật khẩu", type="primary")
    if go:
        if p1 != p2:
            st.error("Mật khẩu nhập lại không khớp.")
            return
        try:
            with tx(database_url(), u["id"], "đổi mật khẩu") as c:
                core.change_password(c, u["id"], old, p1)
        except BizError as e:
            st.error(str(e))
            return
        flash("Đã đổi mật khẩu.")
        st.rerun()
