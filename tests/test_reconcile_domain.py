import io
from datetime import date

from openpyxl import load_workbook

from anhu.domain.excel_export import export_timesheet_xlsx
from anhu.domain.names import split_names
from anhu.domain.reconcile import compare, parse_center_file, preview, suggest_mapping
from anhu.domain.recurrence import TemplateVersion, occurrences
from anhu.domain.timeparse import parse_range
from anhu.domain.timesheet import SessionRecord, build_timesheet
from datetime import time


def S(day, t, names, sid, kind="regular"):
    tr = parse_range(t)
    return SessionRecord(id=sid, day=day, start=tr.start, end=tr.end, location_label="Cơ sở A",
                         attendees=split_names(names), kind=kind)


def _ours():
    d1, d2 = date(2026, 11, 3), date(2026, 11, 5)
    return [S(d1, "16g45-17g45", "An", "s1"), S(d1, "17g30-18g30", "Phúc, My, My, Quỳnh", "s2"),
            S(d2, "16g00-17g00", "Bình, Minh", "s3")]


def _center_file(mutate):
    ss = _ours()
    v = build_timesheet(ss, 2026, 11)
    wb = load_workbook(io.BytesIO(export_timesheet_xlsx(v)))
    mutate(wb["Chấm công"])
    buf = io.BytesIO()
    wb.save(buf)
    return ss, v, buf.getvalue()


def _run(data, ss, v, rates=None):
    m = suggest_mapping(preview(data))
    assert m["sheet"] == "Chấm công" and m["columns"]["date"] == "A"
    rows, problems = parse_center_file(data, m, 2026, 11)
    return compare(v, ss, rows, rates, problems)


def test_identical_file_all_match():
    ss, v, data = _center_file(lambda ws: None)
    items = _run(data, ss, v)
    assert {i.category for i in items} == {"match"} and len(items) == 3


def test_acceptance_7_wrong_and_missing_session_found_source_untouched():
    def mutate(ws):
        # 03/11 = hàng 5: trung tâm ghi sai giờ ca 2; 05/11 = hàng 7: trung tâm bỏ sót ca
        ws["C5"] = "16g45-17g45\n17g45-18g45"
        for c in "BCDEFGHIJK":
            ws[f"{c}7"] = None
    ss, v, data = _center_file(mutate)
    before = [(s.id, s.start, s.end, list(s.attendees)) for s in ss]
    items = _run(data, ss, v)
    cats = [(i.day, i.category) for i in items if i.category != "match"]
    assert (date(2026, 11, 3), "time_mismatch") in cats
    assert (date(2026, 11, 5), "missing_in_center") in cats
    assert (date(2026, 11, 5), "count_mismatch") in cats
    tm = next(i for i in items if i.category == "time_mismatch")
    assert tm.their["row"] == 5 and tm.our["id"] == "s2"   # chứng cứ: hàng gốc + ca cá nhân
    assert [(s.id, s.start, s.end, list(s.attendees)) for s in ss] == before  # không sửa dữ liệu


def test_attendee_and_duplicate_and_money():
    def mutate(ws):
        ws["D5"] = "An\nPhúc, My, Quỳnh"          # trung tâm 'gộp' 2 tên My -> 3 HV
        ws["G5"] = 1; ws["H5"] = None
        ws["C7"] = "16g00-17g00\n16g00-17g00"     # ca trùng
        ws["D7"] = "Bình, Minh\nBình, Minh"; ws["F7"] = 2
    ss, v, data = _center_file(mutate)
    items = _run(data, ss, v, rates={"F": 200000, "G": 250000, "H": 250000, "E": 200000})
    cats = {i.category for i in items}
    assert {"attendee_mismatch", "extra_in_center", "count_mismatch", "money_mismatch"} <= cats
    dup = next(i for i in items if i.category == "extra_in_center")
    assert "lặp" in dup.detail
    money = {i.day: i.amount_diff for i in items if i.category == "money_mismatch"}
    assert money[date(2026, 11, 5)] == -200000   # trung tâm thừa 1 ca 2HV


def test_recurrence_three_days_a_week_next_month():
    tpls = [TemplateVersion(i, i, wd, time(17), time(18), date(2026, 11, 1), None) for i, wd in enumerate([0, 2, 4])]
    days = sorted(d for t in tpls for d in occurrences(t, date(2026, 12, 1), date(2026, 12, 31)))
    # 12/2026: thứ Hai 7,14,21,28; thứ Tư 2,9,16,23,30; thứ Sáu 4,11,18,25 -> 13 ca
    assert len(days) == 13 and all(d.weekday() in (0, 2, 4) for d in days)
    assert days[0] == date(2026, 12, 2) and days[-1] == date(2026, 12, 30)


def test_recurrence_respects_validity():
    t = TemplateVersion(1, 1, 0, time(17), time(18), date(2026, 11, 9), date(2026, 11, 23))
    assert list(occurrences(t, date(2026, 11, 1), date(2026, 11, 30))) == [date(2026, 11, 9), date(2026, 11, 16), date(2026, 11, 23)]
