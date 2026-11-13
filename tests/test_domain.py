"""Kiểm thử nghiệp vụ thuần: đủ ngày, đếm HV theo ca, parse giờ, xếp cột, xuất XLSX."""
import io
from datetime import date, time

import pytest
from openpyxl import load_workbook

from anhu.domain.calendar_month import month_days
from anhu.domain.excel_export import export_timesheet_xlsx
from anhu.domain.names import count_names, split_names
from anhu.domain.pay import Rate, compute_pay
from anhu.domain.text import fold
from anhu.domain.timeparse import parse_range
from anhu.domain.timesheet import SessionRecord, TimesheetRules, build_timesheet
from decimal import Decimal


def S(day, t, names, kind="regular", status="confirmed", loc="Cơ sở A", sid=None, tt="center", notes=""):
    tr = parse_range(t)
    return SessionRecord(id=sid or f"{day}-{t}-{names}", day=day, start=tr.start if tr else None,
                         end=tr.end if tr else None, location_label=loc, kind=kind, status=status,
                         attendees=split_names(names), teaching_type=tt, notes=notes)


def row_of(view, d):
    rows = [r for r in view.rows if r.day == d]
    assert rows, d
    return rows[0].cells


# ---------- tên học viên ----------
@pytest.mark.parametrize("raw,n", [
    ("An", 1), ("An, Bình", 2), ("An, Bình, Minh", 3), ("An, Bình, Minh, Phúc", 4),
    ("Phúc, My, My, Quỳnh", 4), ("An, , Bình,", 2), ("", 0), (None, 0), ("  ,  ,", 0),
    ("Minh Anh, Bảo Ngọc", 2),           # không đếm theo số từ
    ("An, Bình, Chi , Dũng", 4), ("An\nBình", 2), ("An;Bình", 2),
])
def test_count_names(raw, n):
    assert count_names(raw) == n


def test_duplicate_names_kept():
    assert split_names("Phúc, My, My, Quỳnh") == ["Phúc", "My", "My", "Quỳnh"]


def test_fold():
    assert fold("Quỳnh  Đô") == "quynh do"


# ---------- giờ ----------
@pytest.mark.parametrize("raw,start,end,mins", [
    ("16g45-17g45", time(16, 45), time(17, 45), 60),
    ("19:45-20:45", time(19, 45), time(20, 45), 60),
    ("18h45-19g45", time(18, 45), time(19, 45), 60),
    ("19g00- 20g00", time(19), time(20), 60),
    ("16g00 - 16g45", time(16), time(16, 45), 45),
    ("9g30-10g30", time(9, 30), time(10, 30), 60),
    ("19g00-19g30", time(19), time(19, 30), 30),
    ("17g15-18g00", time(17, 15), time(18), 45),
])
def test_parse_range(raw, start, end, mins):
    tr = parse_range(raw)
    assert (tr.start, tr.end, tr.minutes) == (start, end, mins)


@pytest.mark.parametrize("raw", ["", None, "abc", "18g00-17g00", "23g30-00g30", "25g00-26g00"])
def test_parse_range_invalid(raw):
    assert parse_range(raw) is None


def test_label_keeps_minutes():
    assert parse_range("16g45-17g45").label() == "16g45-17g45"


# ---------- đủ ngày ----------
@pytest.mark.parametrize("y,m,n", [(2026, 11, 30), (2026, 10, 31), (2028, 2, 29), (2027, 2, 28), (2024, 2, 29), (2100, 2, 28)])
def test_month_days(y, m, n):
    days = month_days(y, m)
    assert len(days) == n and days == sorted(days) and days[0].day == 1


def test_acceptance_c1_nov_2026_30_days_blank_day():
    v = build_timesheet([S(date(2026, 11, 3), "16g45-17g45", "An")], 2026, 11)
    assert len(v.rows) == 30
    c = row_of(v, date(2026, 11, 2))
    assert c["A"] == date(2026, 11, 2)
    assert all(c[k] is None for k in "BCDEFGHIJKL")


def test_acceptance_c3_empty_month_all_days():
    for y, m, n in [(2028, 2, 29), (2027, 2, 28), (2026, 4, 30), (2026, 12, 31)]:
        v = build_timesheet([], y, m)
        assert [r.day for r in v.rows] == month_days(y, m) and len(v.rows) == n
        assert all(r.cells[k] is None for r in v.rows for k in "BCDEFGHIJKL")


def test_first_and_last_day_of_month():
    v = build_timesheet([S(date(2026, 10, 1), "13g00-14g00", "An"), S(date(2026, 10, 31), "23g00-23g59", "Bình"),
                         S(date(2026, 11, 1), "08g00-09g00", "Ngoài tháng")], 2026, 10)
    assert row_of(v, date(2026, 10, 1))["E"] == 1 and row_of(v, date(2026, 10, 31))["E"] == 1
    assert v.totals["E"] == 2  # ca 01/11 không lọt vào tháng 10


# ---------- đếm theo ca ----------
@pytest.mark.parametrize("names,col", [("An", "E"), ("An, Bình", "F"), ("An, Bình, Minh", "G"),
                                       ("An, Bình, Minh, Phúc", "H"), ("Phúc, My, My, Quỳnh", "H")])
def test_acceptance_c4_c5(names, col):
    d = date(2026, 11, 5)
    c = row_of(build_timesheet([S(d, "16g45-17g45", names)], 2026, 11), d)
    assert c[col] == 1
    assert all(c[k] is None for k in "EFGHI" if k != col)


def test_acceptance_c6_two_sessions_same_day():
    d = date(2026, 11, 5)
    c = row_of(build_timesheet([S(d, "16g00-17g00", "An"), S(d, "17g00-18g00", "An, Bình")], 2026, 11), d)
    assert (c["E"], c["F"], c["G"]) == (1, 1, None)
    assert c["C"] == "16g00-17g00\n17g00-18g00"
    assert c["D"] == "An\nAn, Bình"


def test_acceptance_c7_empty_items():
    d = date(2026, 11, 5)
    c = row_of(build_timesheet([S(d, "16g00-17g00", "An, , Bình,")], 2026, 11), d)
    assert c["F"] == 1 and c["G"] is None


def test_acceptance_c8_planned_not_counted():
    d = date(2026, 11, 5)
    v = build_timesheet([S(d, "16g00-17g00", "An", status="needs_review")], 2026, 11)
    c = row_of(v, d)
    assert all(c[k] is None for k in "BCDEFGHIJKL") and len(v.rows) == 30


def test_more_than_four_goes_to_supplemental():
    d = date(2026, 11, 5)
    v = build_timesheet([S(d, "16g00-17g00", "A, B, C, D, E")], 2026, 11)
    c = row_of(v, d)
    assert c["H"] is None and v.totals["H"] == 0
    assert len(v.supplemental) == 1 and v.supplemental[0]["n"] == 5
    assert c["C"] == "16g00-17g00"  # ngày vẫn thể hiện ca


def test_zero_attendees_not_counted_but_flagged():
    d = date(2026, 11, 5)
    v = build_timesheet([S(d, "16g00-17g00", "")], 2026, 11)
    assert row_of(v, d)["E"] is None and v.review and "học viên" in v.review[0]["reason"]


def test_trial_and_30min_no_double_count():
    d = date(2026, 11, 5)
    v = build_timesheet([S(d, "15g00-15g45", "Hạnh", kind="trial", notes="ca ghép"),
                         S(d, "19g00-19g30", "Dũng", kind="extra"),
                         S(d, "13g00-14g00", "An")], 2026, 11)
    c = row_of(v, d)
    assert c["E"] == 1 and c["I"] == 1 and c["J"] == "Hạnh (ca ghép)"
    assert sum(v.totals.values()) == 2  # ca dạy thử không vào E..I
    assert list(v.placements.values()).count("J") == 1


def test_private_sessions_filterable_from_center_report():
    d = date(2026, 11, 5)
    ss = [S(d, "16g00-17g00", "An", tt="center"), S(d, "18g00-19g00", "Riêng", tt="private", loc="Nhà HV")]
    center = build_timesheet([s for s in ss if s.teaching_type == "center"], 2026, 11)
    c = row_of(center, d)
    assert c["D"] == "An" and center.totals["E"] == 1


def test_edit_attendees_2_to_3_moves_count():
    d = date(2026, 11, 5)
    s = S(d, "16g00-17g00", "An, Bình", sid="x")
    before = build_timesheet([s], 2026, 11).totals
    s.attendees = split_names("An, Bình, Minh")
    after = build_timesheet([s], 2026, 11).totals
    assert (before["F"], before["G"]) == (1, 0) and (after["F"], after["G"]) == (0, 1)


def test_two_locations_same_day_two_rows():
    d = date(2026, 11, 5)
    v = build_timesheet([S(d, "16g00-17g00", "An", loc="CS1"), S(d, "18g00-19g00", "Bình", loc="CS2")], 2026, 11)
    assert [r.cells["B"] for r in v.rows if r.day == d] == ["CS1", "CS2"]
    assert len({r.day for r in v.rows}) == 30


# ---------- tiền ----------
def test_pay_only_with_confirmed_rates():
    d = date(2026, 11, 5)
    ss = [S(d, "16g00-17g00", "An", sid="a"), S(d, "17g00-18g00", "An, Bình", sid="b")]
    v = build_timesheet(ss, 2026, 11)
    unconfirmed = [Rate("hv1", Decimal(200000), date(2026, 1, 1), None, False),
                   Rate("hv2", Decimal(200000), date(2026, 1, 1), None, False)]
    r = compute_pay(v, ss, unconfirmed)
    assert r.total == 0 and not r.complete and len(r.missing) == 2
    rates = [Rate("hv1", Decimal(200000), date(2026, 1, 1), None, True)]
    r = compute_pay(v, ss, rates)
    assert r.total == 200000 and not r.complete
    rates.append(Rate("hv2", Decimal(210000), date(2026, 11, 1), None, True))
    r = compute_pay(v, ss, rates)
    assert r.total == 410000 and r.complete


def test_rate_validity_period():
    ss = [S(date(2026, 10, 31), "16g00-17g00", "An", sid="a"), S(date(2026, 11, 1), "16g00-17g00", "An", sid="b")]
    rates = [Rate("hv1", Decimal(100), date(2026, 1, 1), date(2026, 10, 31), True),
             Rate("hv1", Decimal(150), date(2026, 11, 1), None, True)]
    v10 = build_timesheet(ss, 2026, 10)
    v11 = build_timesheet(ss, 2026, 11)
    assert compute_pay(v10, ss, rates).total == 100 and compute_pay(v11, ss, rates).total == 150


# ---------- XLSX ----------
def test_export_layout_and_blank_cells():
    d = date(2026, 11, 3)
    v = build_timesheet([S(d, "16g45-17g45", "HV50"), S(d, "17g30-18g30", "Phúc, My, My, Quỳnh")], 2026, 11)
    wb = load_workbook(io.BytesIO(export_timesheet_xlsx(v)))
    ws = wb["Chấm công"]
    assert wb.sheetnames[0] == "Chấm công"
    assert ws["A1"].value == "NGÀY " and ws["B1"].value == "CƠ SỞ" and ws["E1"].value == "SỐ GIỜ DẠY"
    assert [ws[f"{c}2"].value for c in "EFGHI"] == ["1 HV", "2 HV", "3HV", "4HV", "30 PHÚT"]
    assert ws["D2"].value == "Tên Học Viên Hiện Diện" and ws["L1"].value == "TRUNG TÂM XÁC NHẬN "
    assert {"A1:A2", "B1:B2", "E1:J1", "K1:K2", "L1:L2"} <= {str(m) for m in ws.merged_cells.ranges}
    # 30 hàng ngày, đúng thứ tự
    dates = [ws[f"A{r}"].value.date() for r in range(3, 33)]
    assert dates == month_days(2026, 11)
    assert ws["A3"].number_format == "dd/mm"
    # 02/11 (hàng 4) rỗng thật
    assert all(ws[f"{c}4"].value is None for c in "BCDEFGHIJKL")
    r = 5  # 03/11
    assert ws[f"C{r}"].value == "16g45-17g45\n17g30-18g30"
    assert ws[f"E{r}"].value == 1 and ws[f"H{r}"].value == 1 and ws[f"F{r}"].value is None
    assert ws["B33"].value == "TỔNG" and ws["E33"].value == "=SUM(E3:E32)"
    assert ws["E34"].value is None  # không có tiền khi chưa có đơn giá xác nhận


def test_export_empty_month_feb_2028():
    v = build_timesheet([], 2028, 2)
    ws = load_workbook(io.BytesIO(export_timesheet_xlsx(v)))["Chấm công"]
    assert [ws[f"A{r}"].value.date() for r in range(3, 32)] == month_days(2028, 2)
    assert ws["B32"].value == "TỔNG"
    assert all(ws[f"{c}{r}"].value is None for r in range(3, 32) for c in "BCDEFGHIJKL")
