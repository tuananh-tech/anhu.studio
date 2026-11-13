"""Báo cáo: tổng quan năm, theo học viên, theo cơ sở, dạy riêng & thu tiền. Có xuất Excel."""
from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from flask import render_template, request, send_file

from ..domain.calendar_month import month_bounds
from ..services import analytics
from . import db, login_required, owner_ctx, parse_date, today

TABS = [("nam", "Tổng quan năm"), ("hoc-vien", "Theo học viên"), ("co-so", "Theo cơ sở"), ("day-rieng", "Dạy riêng & thu tiền")]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _num(v):
    return float(v) if isinstance(v, Decimal) else v


def register(app):
    @app.get("/bao-cao")
    @login_required
    def reports_page():
        a = request.args
        tab = a.get("tab") if a.get("tab") in dict(TABS) else "nam"
        t = today()
        try:
            year = int(a.get("nam") or t.year)
        except ValueError:
            year = t.year
        fa, fb = month_bounds(t.year, t.month)
        d1, d2 = parse_date(a.get("tu"), date(t.year, 1, 1)), parse_date(a.get("den"), fb)
        if d2 < d1:
            d1, d2 = d2, d1
        tt = a.get("loai") or None
        with db() as c:
            owner = owner_ctx(c)
            if tab == "nam":
                data = analytics.year_overview(c, owner, year)
            elif tab == "hoc-vien":
                data = analytics.by_student(c, owner, d1, d2, tt)
            elif tab == "co-so":
                data = analytics.by_location(c, owner, d1, d2)
            else:
                data = analytics.private_payments(c, owner, d1, d2)
        if a.get("xuat") == "xlsx":
            return _export(tab, data, year, d1, d2)
        return render_template("reports.html", tab=tab, TABS=TABS, data=data, year=year, d1=d1, d2=d2, tt=tt, args=a)


def _export(tab, data, year, d1, d2):
    if tab == "nam":
        head = ["Tháng", "Kỳ", "Ca trung tâm", "Ca dạy riêng", "Giờ dạy", "1 HV", "2 HV", "3HV", "4HV", "30 PHÚT", "Dạy thử (J)",
                "Cần kiểm tra", "Tiền công TT (đơn giá đã xác nhận)", "Dạy riêng đã thu", "Dạy riêng còn phải thu", "Chênh lệch đối soát chờ"]
        body = [[f"{r['month']:02d}/{year}", "Đã chốt" if r["locked"] else "Mở", r["center_n"], r["private_n"],
                 round(r["minutes"] / 60, 2), *[r["totals"].get(k, 0) for k in "EFGHI"], r["j"], r["review"],
                 float(r["pay_total"]) if r["pay_total"] else None, _num(r["paid"]), _num(r["due"]), r["recon_pending"]]
                for r in data]
        name = f"BaoCao_Nam_{year}.xlsx"
    elif tab == "hoc-vien":
        head = ["Học viên", "Mã", "Có hồ sơ", "Số buổi", "Giờ", "Trung tâm", "Dạy riêng", "Dạy bù", "Buổi đầu", "Buổi cuối"]
        body = [[r["name"], r["code"], "có" if r["student_id"] else "chưa gắn", r["sessions"], round(r["minutes"] / 60, 2),
                 r["center_n"], r["private_n"], r["makeup_n"], r["first_day"], r["last_day"]] for r in data]
        name = f"BaoCao_HocVien_{d1:%Y%m%d}-{d2:%Y%m%d}.xlsx"
    elif tab == "co-so":
        head = ["Cơ sở", "Loại hình", "Số ca", "Giờ", "Lượt học viên", "Dạy thử", "Dạy bù"]
        body = [[r["location"], "Trung tâm" if r["teaching_type"] == "center" else "Dạy riêng", r["sessions"],
                 round(r["minutes"] / 60, 2), r["attendances"], r["trial_n"], r["makeup_n"]] for r in data]
        name = f"BaoCao_CoSo_{d1:%Y%m%d}-{d2:%Y%m%d}.xlsx"
    else:
        head = ["Ngày", "Giờ", "Địa điểm", "Học viên", "Giá buổi", "Đã thu", "Trạng thái"]
        lab = {"unpaid": "Chưa thu", "partial": "Thu một phần", "paid": "Đã thu", "na": "Chưa có giá"}
        body = [[r["session_date"], f"{r['start_time']:%H:%M}-{r['end_time']:%H:%M}", r["location_label"], r["names"],
                 _num(r["price_amount"]), _num(r["paid_amount"]), lab.get(r["payment_status"], r["payment_status"])] for r in data]
        name = f"BaoCao_DayRieng_{d1:%Y%m%d}-{d2:%Y%m%d}.xlsx"
    return send_file(io.BytesIO(analytics.to_xlsx(name.split("_")[1], head, body)), mimetype=XLSX, as_attachment=True,
                     download_name=name)
