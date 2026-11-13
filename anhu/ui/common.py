"""Hạ tầng chung cho giao diện Streamlit.

- Cấu hình: biến môi trường, rồi st.secrets (Streamlit Cloud). Không có giá trị mặc định cho bí mật.
- CSDL: mỗi thao tác một transaction gắn app.user_id -> RLS trong PostgreSQL quyết định quyền (giống bản Flask).
- Rerun: Streamlit chạy lại toàn bộ script mỗi lần tương tác. Mọi thao tác GHI chỉ chạy khi người dùng bấm nút,
  qua `act()`: chặn bấm trùng (cùng thao tác + cùng dữ liệu trong 5 giây), dịch lỗi nghiệp vụ/DB sang tiếng Việt.
  Không có migration, tạo tài khoản hay import nào chạy tự động khi rerun.
"""
from __future__ import annotations

import hashlib
import html
import os
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from ..db import tx
from ..domain.recurrence import WEEKDAY_LABEL
from ..domain.timesheet import KIND_LABEL, KINDS
from ..services import BizError, ConflictError, db_error_message

VN = ZoneInfo("Asia/Ho_Chi_Minh")
PAGES: dict = {}            # tên -> st.Page (app.py điền vào), để liên kết giữa các trang


# ---------------- cấu hình ----------------
def cfg(key: str, default=None):
    v = os.environ.get(key)
    if v not in (None, ""):
        return v
    try:
        v = st.secrets.get(key)
    except Exception:  # không có secrets.toml khi chạy cục bộ
        v = None
    return default if v in (None, "") else str(v)


def database_url() -> str | None:
    return cfg("ANHU_DATABASE_URL")


def allowed_emails() -> list[str]:
    return [e.strip().lower() for e in (cfg("ANHU_ALLOWED_EMAILS", "") or "").split(",") if e.strip()]


def today() -> date:
    return datetime.now(VN).date()


# ---------------- CSDL ----------------
def user() -> dict | None:
    return st.session_state.get("user")


def uid():
    u = user()
    return u["id"] if u else None


@contextmanager
def db(reason: str | None = None):
    with tx(database_url(), uid(), reason) as c:
        yield c


def owner() -> str:
    return st.session_state.get("owner") or uid()


def owner_info() -> dict:
    return st.session_state.get("owner_info") or {"id": uid(), "name": (user() or {}).get("name", ""), "can_write": True,
                                                  "self": True}


def can_write() -> bool:
    return bool(owner_info().get("can_write"))


def flash(msg: str, kind: str = "success"):
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("_flash", []):
        {"success": st.success, "info": st.info, "warning": st.warning, "error": st.error}[kind](msg)


def _once(key: str, payload) -> bool:
    h = hashlib.sha256(repr(payload).encode("utf-8", "replace")).hexdigest()
    store = st.session_state.setdefault("_once", {})
    last = store.get(key)
    now = _time.time()
    if last and last[0] == h and now - last[1] < 5:
        return False
    store[key] = (h, now)
    return True


def act(fn, *, key: str, payload=None, reason: str | None = None, ok: str | None = None, rerun: bool = True,
        where=None):
    """Chạy một thao tác ghi. Trả kết quả hoặc None nếu lỗi. `where`: vùng hiển thị lỗi (mặc định tại chỗ)."""
    out = where or st
    if not _once(key, payload):
        out.info("Thao tác này vừa được thực hiện — đã bỏ qua lần bấm trùng.")
        return None
    try:
        with db(reason) as c:
            res = fn(c)
    except ConflictError as e:
        st.session_state["_once"].pop(key, None)
        out.warning(str(e))
        return None
    except BizError as e:
        st.session_state["_once"].pop(key, None)
        out.error(str(e))
        return None
    except (OperationalError, InterfaceError):
        st.session_state["_once"].pop(key, None)
        raise
    except DBAPIError as e:
        st.session_state["_once"].pop(key, None)
        msg = db_error_message(e)
        if not msg:
            raise
        out.error(msg)
        return None
    st.session_state.pop("_ensured", None)   # dữ liệu đã đổi (VD lịch cố định mới) -> sinh lại ca dự kiến khi xem
    if ok:
        flash(ok)
    if rerun:
        st.rerun()
    return res


def read(fn, default=None):
    """Đọc dữ liệu; lỗi nghiệp vụ -> thông báo, trả default."""
    try:
        with db() as c:
            return fn(c)
    except BizError as e:
        st.error(str(e))
        return default


# ---------------- nhãn & định dạng ----------------
def kind_label(code) -> str:
    return st.session_state.get("kind_label", KIND_LABEL).get(code, code or "")


def kinds_active() -> list[str]:
    return st.session_state.get("kinds_active") or list(KINDS)


def status_label(s) -> str:
    return st.session_state.get("status_label", {}).get(s, s)


def cancel_labels() -> dict:
    from ..services.admin import DEFAULT_CANCEL_LABELS
    return st.session_state.get("cancel_label") or DEFAULT_CANCEL_LABELS


def dmy(d) -> str:
    return d.strftime("%d/%m/%Y") if isinstance(d, (date, datetime)) else (d or "")


def dm(d) -> str:
    return d.strftime("%d/%m") if isinstance(d, date) else (d or "")


def hm(t) -> str:
    return t.strftime("%H:%M") if isinstance(t, time) else (t or "")


def money(v) -> str:
    if v is None:
        return "—"
    return f"{int(v):,}".replace(",", ".") + " ₫"


def wd(d: date) -> str:
    return WEEKDAY_LABEL[d.weekday()]


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


STATUS_COLOR = {"planned": "#8a6512", "pending": "#8a6512", "confirmed": "#1f4d3a", "cancelled": "#777",
                "rescheduled": "#4a4677", "needs_review": "#a3372b", "void": "#777"}


def badge(status: str) -> str:
    return f":{'green' if status == 'confirmed' else 'red' if status == 'needs_review' else 'gray' if status in ('cancelled', 'void', 'rescheduled') else 'orange'}-badge[{status_label(status)}]"


def seg(options: list, key: str, default):
    """Chọn chế độ con. Không truyền default khi trạng thái đã được trang khác đặt sẵn (tránh cảnh báo Streamlit)."""
    if st.session_state.get(key) not in options:
        st.session_state[key] = default
    return st.segmented_control("Chế độ", options, key=key, label_visibility="collapsed") or default


def help_box(text_md: str):
    with st.expander("❔ Hướng dẫn nhanh", expanded=False):
        st.markdown(text_md)


def month_options(center: date, back=24, ahead=2):
    out = []
    y, m = center.year, center.month
    for k in range(-back, ahead + 1):
        mm = m + k
        yy = y + (mm - 1) // 12
        mm = (mm - 1) % 12 + 1
        out.append((yy, mm))
    return out[::-1]


def month_picker(key: str, label="Tháng") -> tuple[int, int]:
    t = today()
    opts = month_options(t)
    default = st.session_state.get(key) or (t.year, t.month)
    if default not in opts:
        opts.append(default)
    return st.selectbox(label, opts, index=opts.index(default), key=key, format_func=lambda x: f"{x[1]:02d}/{x[0]}")


def page_link(name: str, label: str, icon=None, **state):
    """Liên kết sang trang khác, có thể đặt sẵn trạng thái (VD tab con)."""
    p = PAGES.get(name)
    if not p:
        return
    if st.button(label, key=f"lnk_{name}_{label}", icon=icon, type="tertiary"):
        for k, v in state.items():
            st.session_state[k] = v
        st.switch_page(p)


CSS = """
<style>
:root { --forest:#1f4d3a; --forest-2:#2c6a50; --champ:#c9a85c; --champ-soft:#f3e9cf; --cream:#faf6ec; --line:#e2dccb; --muted:#66746c; }
.block-container { padding-top: 3.6rem; padding-bottom: 3rem; max-width: 1180px; }
h1 { color: var(--forest); font-size: 1.55rem !important; }
h2, h3 { color: var(--forest); }
h2 { font-size: 1.25rem !important; }
h3 { font-size: 1.1rem !important; }
h5 { font-size: .95rem !important; color: var(--forest-2); margin: .6rem 0 .2rem !important; }
div[data-testid="stMetricValue"] { color: var(--forest); }
.anhu-brand { font-weight: 700; letter-spacing: .04em; color: var(--forest); }
.anhu-brand span { color: var(--champ); font-weight: 500; }
.anhu-muted { color: var(--muted); font-size: .88rem; }
.anhu-xl-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; background: #fff; max-height: 72vh; }
.anhu-xl { border-collapse: collapse; font-size: .85rem; min-width: 980px; width: 100%; }
.anhu-xl th, .anhu-xl td { border: 1px solid var(--line); padding: .3rem .45rem; vertical-align: top; }
.anhu-xl th { background: var(--champ-soft); position: sticky; top: 0; z-index: 2; text-align: center; color: #3a321c; }
.anhu-xl thead tr:nth-child(2) th { top: 1.9rem; }
.anhu-xl td.c { text-align: center; width: 3.2rem; }
.anhu-xl td.pre { white-space: pre-line; }
.anhu-xl td.d, .anhu-xl th.d { position: sticky; left: 0; background: #fff; z-index: 1; }
.anhu-xl th.d { z-index: 3; background: var(--champ-soft); }
.anhu-xl tr.e td { color: #b5ae9c; }
.anhu-xl tr.e td.d { color: #1d2a24; }
.anhu-xl tr.w td.d { color: var(--forest-2); font-weight: 600; }
.anhu-xl tr.t td { background: #fff7d6; font-weight: 700; }
.anhu-cal { display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 3px; font-size: .8rem; }
.anhu-cal div { background: #fff; border: 1px solid var(--line); border-radius: 6px; min-height: 3.6rem; padding: 3px 4px; overflow: hidden; }
.anhu-cal .h { background: none; border: 0; min-height: 0; text-align: center; color: var(--muted); }
.anhu-cal .o { opacity: .45; }
.anhu-cal .td { border-color: var(--champ); box-shadow: inset 0 3px 0 var(--champ); }
.anhu-cal b { display: block; color: var(--forest); }
.anhu-cal .n { color: var(--forest-2); }
.anhu-cal .p { color: #8a6512; }
.st-key-mnav { display: none; }
.anhu-kpi { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: .5rem; margin: .3rem 0 .6rem; }
.anhu-kpi div { background: #fffdf8; border: 1px solid var(--line); border-radius: 10px; padding: .55rem .7rem; }
.anhu-kpi b { display: block; font-size: 1.45rem; color: var(--forest); line-height: 1.15; }
.anhu-kpi span { font-size: .8rem; color: var(--muted); }
.anhu-kpi .alert b { color: #8a6512; }
@media (max-width: 640px) {
  .st-key-mnav { display: flex !important; flex-direction: row !important; flex-wrap: nowrap !important; overflow-x: auto;
                 gap: .1rem !important; position: sticky; top: 2.6rem; z-index: 50; background: var(--cream);
                 padding: .15rem 0 .25rem; border-bottom: 1px solid var(--line); margin-bottom: .4rem; }
  .st-key-mnav > div, .st-key-mnav [data-testid="stElementContainer"] { width: auto !important; min-width: 0 !important;
                                                                           flex: 0 0 auto !important; }
  .st-key-mnav a { padding: .3rem .3rem !important; margin: 0 !important; }
  .st-key-mnav a p { font-size: .8rem !important; white-space: nowrap; }
  .st-key-mnav a > span:first-child:not(:last-child) { display: none !important; }
  .st-key-mnav { justify-content: space-between; }
  .anhu-kpi { grid-template-columns: repeat(3, minmax(0, 1fr)); gap: .35rem; }
  .anhu-kpi b { font-size: 1.15rem; }
  .anhu-kpi span { font-size: .7rem; }
  .block-container { padding-left: .7rem; padding-right: .7rem; padding-top: 3.2rem; }
  h1 { font-size: 1.3rem !important; }
  .anhu-cal { font-size: .68rem; }
  .anhu-cal div { min-height: 2.8rem; padding: 2px; }
}
</style>
"""


def mobile_nav():
    """Thanh điều hướng 5 mục luôn thấy trên điện thoại (thanh trên cùng của Streamlit thu vào menu ẩn trên màn hình hẹp)."""
    with st.container(key="mnav", horizontal=True, gap="small"):
        for k, lbl in (("tong_quan", "Tổng quan"), ("lich_day", "Lịch"), ("hoc_vien", "Học viên"), ("cham_cong", "Chấm công"),
                       ("bao_cao", "Báo cáo")):
            if k in PAGES:
                st.page_link(PAGES[k], label=lbl, width="content")
        if "cai_dat" in PAGES:
            st.page_link(PAGES["cai_dat"], label="⚙", help="Cài đặt", width="content")


def inject_css():
    st.html(CSS)


# ---------------- ghi nhớ đăng nhập trên thiết bị (localStorage, qua st.components.v2) ----------------
_DEVICE_JS = """
export default function(component) {
  const { data, setStateValue } = component;
  const K = 'anhu.resume';
  try {
    if (data && data.clear) { window.localStorage.removeItem(K); }
    if (data && data.set) { window.localStorage.setItem(K, data.set); }
    if (data && data.read) { setStateValue('token', window.localStorage.getItem(K) || ''); }
  } catch (e) {
    if (data && data.read) { setStateValue('token', ''); }
  }
}
"""
_device = None


def device(action: str, value: str | None = None):
    """action: 'read' (trả mã đã lưu hoặc None) | 'set' | 'clear'. Chạy trong trang, không gửi mã lên URL."""
    global _device
    if _device is None:
        _device = st.components.v2.component("anhu_device", js=_DEVICE_JS)
    data = {"read": True} if action == "read" else {"set": value} if action == "set" else {"clear": True}
    res = _device(data=data, key=f"anhu_device_{action}", on_token_change=lambda: None)
    return getattr(res, "token", None) if action == "read" else None
