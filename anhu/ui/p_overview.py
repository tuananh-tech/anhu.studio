"""Tổng quan: chỉ những gì cần cho hôm nay và tháng này; mỗi chỉ số dẫn tới danh sách tương ứng."""
from __future__ import annotations

from datetime import timedelta

import streamlit as st

from ..db import one, scalar
from ..domain.calendar_month import month_bounds
from ..services import admin, reports, schedule
from .common import PAGES, can_write, db, money, owner, today, user, wd
from .p_schedule import _locs, _students, session_card


def _go(page, **state):
    for k, v in state.items():
        st.session_state[k] = v
    st.switch_page(PAGES[page])


def render():
    t = today()
    u = user()
    st.title(f"Chào {u['name']} 👋")
    st.caption(f"{wd(t)}, {t:%d/%m/%Y}")
    a, b = month_bounds(t.year, t.month)
    with db() as c:
        if can_write():
            schedule.ensure_planned(c, owner(), a, max(b, t + timedelta(days=7)))
        k = reports.dashboard(c, owner(), t.year, t.month, t)
        todays = schedule.planned_between(c, owner(), t, t)
        pending = scalar(c, """SELECT count(*) FROM planned_sessions WHERE owner_id = :o AND status IN ('planned','needs_review')
                               AND session_date BETWEEN :a AND :t""", o=owner(), a=(a - timedelta(days=31)).replace(day=1), t=t)
        active_students = scalar(c, "SELECT count(*) FROM students WHERE owner_id = :o AND archived_at IS NULL AND status = 'active'",
                                 o=owner())
        view, recs, pay = reports.timesheet(c, owner(), t.year, t.month, "center")
        info = admin.system_info(c, owner())
        recon = one(c, """SELECT count(*) AS n FROM reconciliation_items i JOIN reconciliation_imports m ON m.id = i.import_id
                          WHERE i.owner_id = :o AND i.resolution = 'pending'""", o=owner())["n"]

    month_ok = k["confirmed"]
    st.subheader("Hôm nay")
    live = [p for p in todays if p["status"] != "rescheduled"]
    if not live:
        st.caption("Hôm nay không có ca nào.")
    else:
        students, locs = _students(), _locs()
        for p in live:
            session_card(p, students, locs)

    pay_txt = money(pay.total) if (pay and pay.complete and pay.by_category and view.rules.confirmed) else "—"
    cells = [("Chưa xác nhận", pending, "alert" if pending else ""), (f"Ca đã dạy T{t.month}", month_ok, ""),
             (f"Giờ dạy T{t.month}", f"{k['minutes'] / 60:.1f}", ""), ("Học viên đang học", active_students, ""),
             ("Công TT (dự kiến)", pay_txt, ""), ("Đối soát chờ", recon, "alert" if recon else "")]
    st.html("<div class='anhu-kpi'>" + "".join(f"<div class='{c}'><b>{v}</b><span>{lb}</span></div>" for lb, v, c in cells) + "</div>")
    if pay_txt == "—" and month_ok:
        st.caption("Công trung tâm chỉ hiện khi quy tắc chấm công và đơn giá đã được xác nhận cho mọi ca trong tháng (không ước tính).")
    with st.container(horizontal=True, gap="small"):
        if st.button(f"Xác nhận {pending} ca" if pending else "Không có ca chờ", icon=":material/task_alt:", disabled=not pending,
                     type="primary" if pending else "secondary"):
            _go("lich_day", lich_view="Chờ xác nhận")
        if st.button("Lịch tuần", icon=":material/calendar_view_week:"):
            _go("lich_day", lich_view="Tuần")
        if st.button("Bảng công", icon=":material/table:"):
            _go("cham_cong", cc_view="Bảng công")
        if st.button(f"Đối soát ({recon})" if recon else "Đối soát", icon=":material/compare_arrows:"):
            _go("cham_cong", cc_view="Đối soát")
    if k["private_n"]:
        st.caption(f"Dạy riêng tháng này: {k['private_n']} ca · đã thu {money(k['private_paid'])} · còn phải thu {money(k['private_due'])}"
                   + (f" · {k['private_unpriced']} ca chưa có giá" if k["private_unpriced"] else ""))

    # thông báo quan trọng — gộp một khối, chỉ những việc cần làm
    notes = [n for n in info["warnings"] if not (n.startswith("Chưa có bản sao lưu") and not info["counts"]["sessions"])]
    if view.review:
        notes.insert(0, f"{len(view.review)} ca đã xác nhận nhưng **cần kiểm tra** (chưa tính công) trong tháng — xem Chấm công.")
    if notes:
        st.warning("**Cần chú ý**\n" + "\n".join(f"- {n}" for n in notes), icon=":material/info:")
