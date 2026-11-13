"""Khung ứng dụng Streamlit: kiểm tra cấu hình/CSDL, đăng nhập, điều hướng 5 mục + Cài đặt."""
from __future__ import annotations

from pathlib import Path

import streamlit as st
from sqlalchemy.exc import InterfaceError, OperationalError

from anhu.db import tx
from anhu.services import accounts, admin, core
from anhu.ui import common
from anhu.ui.auth_views import bootstrap_view, login_view, must_change_view

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = sorted(p.name for p in (ROOT / "db" / "migrations").glob("*.sql"))


@st.cache_data(ttl=120, show_spinner=False)
def schema_pending(url: str) -> list[str]:
    """Migration chưa áp dụng (chỉ ĐỌC; việc migrate do quản trị chạy scripts/migrate.py)."""
    with tx(url) as c:
        done = {r[0] for r in c.exec_driver_sql("SELECT name FROM schema_migrations")}
    return [m for m in MIGRATIONS if m not in done]


def fatal(title: str, body: str):
    st.title(title)
    st.write(body)
    if st.button("Thử lại", type="primary"):
        st.cache_data.clear()
        st.rerun()
    st.stop()


def load_context(u):
    """Nạp nhãn loại ca/trạng thái và danh sách giáo viên được xem cho lần chạy này."""
    with tx(common.database_url(), u["id"]) as c:
        owners = core.visible_owners(c, u["id"])
        want = st.session_state.get("owner") or u["id"]
        cur = next((o for o in owners if o["id"] == want), owners[0])
        ks = admin.kinds(c, cur["id"])
        lb = admin.labels(c, cur["id"])
    st.session_state.update(owners=owners, owner=cur["id"], owner_info=cur,
                            kind_list=ks, kind_label={k["code"]: k["label"] for k in ks},
                            kinds_active=[k["code"] for k in ks if k["active"]],
                            status_label=lb["status"], cancel_label=lb["cancel"])


def main(test_page: str | None = None):
    """test_page: chỉ dùng trong kiểm thử (AppTest) để hiển thị thẳng một trang sau bước đăng nhập."""
    common.inject_css()
    url = common.database_url()
    if not url:
        fatal("Chưa cấu hình cơ sở dữ liệu",
              "Thiếu `ANHU_DATABASE_URL`. Trên Streamlit Cloud: App settings → Secrets (xem docs/DEPLOY_STREAMLIT.md). "
              "Cục bộ: đặt biến môi trường hoặc tạo `.streamlit/secrets.toml` từ `.streamlit/secrets.toml.example`.")
    try:
        pending = schema_pending(url)
    except (OperationalError, InterfaceError):
        fatal("Không kết nối được cơ sở dữ liệu",
              "Máy chủ dữ liệu đang không phản hồi (dữ liệu không bị mất). Thử lại sau ít phút; nếu vẫn lỗi, báo người quản trị.")
    if pending:
        fatal("Cơ sở dữ liệu cần cập nhật",
              f"Phiên bản ứng dụng mới cần migration: {', '.join(pending)}. Người quản trị chạy `python scripts/migrate.py` "
              "(xem docs/DEPLOY_STREAMLIT.md, mục Cập nhật phiên bản). Ứng dụng không tự sửa cấu trúc CSDL.")

    sid = st.session_state.get("sid")
    u = None
    if sid:
        with tx(url) as c:
            u = accounts.check_session(c, sid)
        if not u:
            st.session_state.clear()
            common.flash("Phiên đăng nhập đã hết hạn hoặc đã bị thu hồi. Vui lòng đăng nhập lại.", "info")
    if not u:
        # Tải lại trang / mở lại app: thử phiên đã ghi nhớ trên thiết bị (trừ khi vừa đăng xuất trong tab này).
        if st.session_state.get("_dev_clear"):
            common.device("clear")
        else:
            tok = common.device("read")
            if tok:
                with tx(url) as c:
                    rsid = accounts.resume(c, tok)
                if rsid:
                    st.session_state["sid"] = rsid
                    st.rerun()
                st.session_state["_dev_clear"] = True
                common.device("clear")
        with tx(url) as c:
            state = accounts.bootstrap_state(c)
        if state == "open":
            bootstrap_view()
        else:
            if "ma" in st.query_params and "auth_mode" not in st.session_state:
                st.session_state["auth_mode"] = "Tôi có mã kích hoạt"
            login_view()
        st.stop()
    st.session_state["user"] = {**u, "session": sid}
    if st.session_state.get("_dev_set"):
        common.device("set", st.session_state.pop("_dev_set"))
    if u["must_change"]:
        must_change_view(u)
        st.stop()
    load_context(u)

    from anhu.ui import p_overview, p_reports, p_schedule, p_settings, p_students, p_timesheet
    pages = {
        "tong_quan": st.Page(p_overview.render, title="Tổng quan", icon=":material/home:", url_path="tong-quan", default=True),
        "lich_day": st.Page(p_schedule.render, title="Lịch dạy", icon=":material/calendar_month:", url_path="lich-day"),
        "hoc_vien": st.Page(p_students.render, title="Học viên", icon=":material/groups:", url_path="hoc-vien"),
        "cham_cong": st.Page(p_timesheet.render, title="Chấm công", icon=":material/fact_check:", url_path="cham-cong"),
        "bao_cao": st.Page(p_reports.render, title="Báo cáo", icon=":material/bar_chart:", url_path="bao-cao"),
        "cai_dat": st.Page(p_settings.render, title="Cài đặt", icon=":material/settings:", url_path="cai-dat"),
    }
    common.PAGES.update(pages)
    nav = st.navigation([pages[k] for k in ("tong_quan", "lich_day", "hoc_vien", "cham_cong", "bao_cao")] + [pages["cai_dat"]],
                        position="top")

    with st.sidebar:
        st.html("<div class='anhu-brand'>ANHU <span>Teaching Studio</span></div>")
        st.write(f"👤 **{u['name']}**" + (" · quản trị" if u["is_admin"] else ""))
        owners = st.session_state["owners"]
        if len(owners) > 1:
            labels = {o["id"]: o["name"] + ("" if o["self"] else (" (chia sẻ, chỉ xem)" if not o["can_write"] else " (chia sẻ)"))
                      for o in owners}
            pick = st.selectbox("Đang xem dữ liệu của", list(labels), format_func=labels.get,
                                index=list(labels).index(st.session_state["owner"]))
            if pick != st.session_state["owner"]:
                st.session_state["owner"] = pick
                st.rerun()
        if st.button("Đăng xuất", icon=":material/logout:", width="stretch"):
            with tx(url, u["id"]) as c:
                accounts.logout(c, sid)
            st.session_state.clear()
            st.session_state["_dev_clear"] = True
            st.rerun()
    if not common.owner_info()["self"]:
        st.warning(f"Đang xem dữ liệu được chia sẻ của **{common.owner_info()['name']}**"
                   + ("" if common.can_write() else " — chỉ xem") + ".")
    common.mobile_nav()
    common.show_flash()
    try:
        if test_page:
            {"tong_quan": p_overview.render, "lich_day": p_schedule.render, "hoc_vien": p_students.render,
             "cham_cong": p_timesheet.render, "bao_cao": p_reports.render, "cai_dat": p_settings.render}[test_page]()
        else:
            nav.run()
    except (OperationalError, InterfaceError):
        st.error("Mất kết nối cơ sở dữ liệu trong lúc thao tác. Thao tác chưa hoàn tất KHÔNG được ghi dở. Bấm Thử lại sau ít phút.")
        if st.button("Thử lại"):
            st.rerun()

