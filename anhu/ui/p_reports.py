"""Báo cáo (chỉ đọc): năm, tháng, học viên, giáo viên, cơ sở, dạy riêng. Chỉ buổi ĐÃ XÁC NHẬN; kỳ chốt lấy từ bản chốt."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import streamlit as st

from ..domain.calendar_month import month_bounds
from ..services import analytics, students_io
from .common import help_box, money, month_picker, owner, read, seg, today

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def _download(df: pd.DataFrame, title: str, name: str):
    if df.empty:
        return
    data = analytics.to_xlsx(title, list(df.columns), [[_num(x) if not (isinstance(x, float) and pd.isna(x)) else None for x in r]
                                                     for r in df.itertuples(index=False)])
    st.download_button("⬇ Xuất Excel", data, name, XLSX, key=f"dl_{name}")


def _range(key):
    t = today()
    c1, c2 = st.columns(2)
    a = c1.date_input("Từ ngày", date(t.year, 1, 1), key=f"{key}_a", format="DD/MM/YYYY")
    b = c2.date_input("Đến ngày", month_bounds(t.year, t.month)[1], key=f"{key}_b", format="DD/MM/YYYY")
    return (a, b) if a <= b else (b, a)


def view_year():
    y = st.number_input("Năm", 2000, 2100, today().year, key="bc_y")
    data = read(lambda c: analytics.year_overview(c, owner(), int(y)), [])
    df = pd.DataFrame([{"Tháng": f"{r['month']:02d}/{y}", "Kỳ": "Đã chốt" if r["locked"] else "", "Ca TT": r["center_n"],
                        "Ca riêng": r["private_n"], "Giờ": round(r["minutes"] / 60, 1), "1 HV": r["totals"].get("E", 0),
                        "2 HV": r["totals"].get("F", 0), "3HV": r["totals"].get("G", 0), "4HV": r["totals"].get("H", 0),
                        "30′": r["totals"].get("I", 0), "Dạy thử": r["j"], "Cần KT": r["review"],
                        "Tiền công TT": money(r["pay_total"]) if r["pay_total"] else ("—" if r["center_n"] else ""),
                        "Riêng đã thu": _num(r["paid"]), "Riêng còn thu": _num(r["due"]), "Đối soát chờ": r["recon_pending"]}
                       for r in data])
    st.dataframe(df, hide_index=True, width="stretch")
    if not df.empty:
        st.caption(f"Cả năm: **{int(df['Ca TT'].sum())}** ca trung tâm · **{int(df['Ca riêng'].sum())}** ca dạy riêng · "
                   f"**{df['Giờ'].sum():.1f}** giờ. “—” ở cột tiền = chưa đủ đơn giá đã xác nhận (không ước tính).")
    _download(df, "Tổng quan năm", f"BaoCao_Nam_{int(y)}.xlsx")


def view_month():
    y, m = month_picker("bc_m")
    a, b = month_bounds(y, m)
    st.caption(f"Tháng {m:02d}/{y}")
    data = read(lambda c: analytics.by_location(c, owner(), a, b), [])
    df = pd.DataFrame([{"Cơ sở": r["location"], "Loại hình": "Trung tâm" if r["teaching_type"] == "center" else "Dạy riêng",
                        "Số ca": r["sessions"], "Giờ": round(r["minutes"] / 60, 1), "Lượt HV": r["attendances"]} for r in data])
    if df.empty:
        st.info("Tháng này chưa có buổi dạy đã xác nhận.")
        return
    c1, c2, c3 = st.columns(3)
    c1.metric("Số ca", int(df["Số ca"].sum()))
    c2.metric("Giờ dạy", f"{df['Giờ'].sum():.1f}")
    c3.metric("Lượt học viên", int(df["Lượt HV"].sum()))
    st.dataframe(df, hide_index=True, width="stretch")
    _download(df, "Tháng", f"BaoCao_Thang_{y}-{m:02d}.xlsx")


def view_students():
    a, b = _range("bc_hv")
    tt = st.selectbox("Loại hình", ["", "center", "private"], key="bc_hv_tt",
                      format_func={"": "Tất cả", "center": "Trung tâm", "private": "Dạy riêng"}.get)
    data = read(lambda c: analytics.by_student(c, owner(), a, b, tt or None), [])
    df = pd.DataFrame([{"Học viên": r["name"], "Mã": r["code"] or "", "Hồ sơ": "có" if r["student_id"] else "chưa gắn",
                        "Số buổi": r["sessions"], "Giờ": round(r["minutes"] / 60, 1), "Trung tâm": r["center_n"],
                        "Dạy riêng": r["private_n"], "Dạy bù": r["makeup_n"], "Buổi đầu": r["first_day"], "Buổi cuối": r["last_day"]}
                       for r in data])
    if df.empty:
        st.info("Không có buổi dạy đã xác nhận trong khoảng này.")
        return
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Buổi đầu": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                "Buổi cuối": st.column_config.DateColumn(format="DD/MM/YYYY")})
    _download(df, "Theo học viên", f"BaoCao_HocVien_{a:%Y%m%d}-{b:%Y%m%d}.xlsx")


def view_teachers():
    a, b = _range("bc_gv")
    owners = st.session_state.get("owners", [])
    data = read(lambda c: students_io.by_teacher(c, owners, a, b), [])
    df = pd.DataFrame([{"Giáo viên": r["teacher"] + ("" if r["self"] else " (chia sẻ)"), "Số ca": r["sessions"], "Trung tâm": r["center_n"],
                        "Dạy riêng": r["private_n"], "Giờ": round(r["minutes"] / 60, 1), "Học viên": r["students"],
                        "Cần kiểm tra": r["review"]} for r in data])
    st.dataframe(df, hide_index=True, width="stretch")
    st.caption("Chỉ hiện giáo viên mà bạn được xem dữ liệu (chính bạn và người đã chia sẻ cho bạn).")
    _download(df, "Theo giáo viên", f"BaoCao_GiaoVien_{a:%Y%m%d}-{b:%Y%m%d}.xlsx")


def view_locations():
    a, b = _range("bc_cs")
    data = read(lambda c: analytics.by_location(c, owner(), a, b), [])
    df = pd.DataFrame([{"Cơ sở": r["location"], "Loại hình": "Trung tâm" if r["teaching_type"] == "center" else "Dạy riêng",
                        "Số ca": r["sessions"], "Giờ": round(r["minutes"] / 60, 1), "Lượt HV": r["attendances"],
                        "Dạy thử": r["trial_n"], "Dạy bù": r["makeup_n"]} for r in data])
    if df.empty:
        st.info("Không có dữ liệu.")
        return
    st.dataframe(df, hide_index=True, width="stretch")
    _download(df, "Theo cơ sở", f"BaoCao_CoSo_{a:%Y%m%d}-{b:%Y%m%d}.xlsx")


def view_private():
    a, b = _range("bc_dr")
    data = read(lambda c: analytics.private_payments(c, owner(), a, b), [])
    lab = {"unpaid": "Chưa thu", "partial": "Thu một phần", "paid": "Đã thu", "na": "Chưa có giá"}
    df = pd.DataFrame([{"Ngày": r["session_date"], "Giờ": f"{r['start_time']:%H:%M}–{r['end_time']:%H:%M}", "Địa điểm": r["location_label"],
                        "Học viên": r["names"] or "", "Giá buổi": _num(r["price_amount"]), "Đã thu": _num(r["paid_amount"]),
                        "Trạng thái": lab.get(r["payment_status"], r["payment_status"])} for r in data])
    if df.empty:
        st.info("Không có buổi dạy riêng trong khoảng này.")
        return
    due = sum(float(r["price_amount"] or 0) - float(r["paid_amount"] or 0) for r in data if r["payment_status"] in ("unpaid", "partial"))
    st.caption(f"Đã thu **{money(sum(float(r['paid_amount'] or 0) for r in data))}** · còn phải thu **{money(due)}** "
               "(ca “Chưa có giá” không được tính vào số còn phải thu).")
    st.dataframe(df, hide_index=True, width="stretch", column_config={"Ngày": st.column_config.DateColumn(format="DD/MM/YYYY")})
    _download(df, "Dạy riêng", f"BaoCao_DayRieng_{a:%Y%m%d}-{b:%Y%m%d}.xlsx")


def render():
    st.title("Báo cáo")
    help_box("Báo cáo chỉ tính buổi dạy **đã xác nhận**; tháng đã chốt lấy số liệu từ bản chốt. Không có số ước tính: "
             "khi thiếu đơn giá đã xác nhận, cột tiền để “—”. Mỗi bảng đều xuất được Excel.")
    v = seg(["Năm", "Tháng", "Học viên", "Giáo viên", "Cơ sở", "Dạy riêng"], "bc_view", "Năm")
    {"Năm": view_year, "Tháng": view_month, "Học viên": view_students, "Giáo viên": view_teachers, "Cơ sở": view_locations,
     "Dạy riêng": view_private}[v]()
