"""Golden-master trên fixture ẩn danh (cùng cấu trúc file nguồn thật)."""
from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from anhu.domain.legacy import detect_groups, legacy_formula_row, plan_import
from anhu.domain.timesheet import SessionRecord, build_timesheet

FIX = Path(__file__).parent / "fixtures" / "anon_source.xlsx"


@pytest.fixture(scope="module")
def plan():
    return plan_import(FIX.read_bytes())


def _norm(v):
    return "" if v is None else v


def test_legacy_formula_emulation_matches_cached_values():
    wb = load_workbook(FIX)
    dl, cc = wb["Dữ liệu"], wb["Chấm công"]
    checked = 0
    for r in range(3, 40):
        src = {get_column_letter(c): dl.cell(r - 1, c).value for c in range(1, dl.max_column + 1)}
        got = legacy_formula_row(src)
        for col in "CDEFGHJ":
            assert _norm(got[col]) == _norm(cc[f"{col}{r}"].value), (r, col)
            checked += 1
    assert checked == 259
    assert cc["E40"].value == 29 and cc["H40"].value == 16  # tổng của sheet cũ


def test_header_detection_handles_column_shift():
    wb = load_workbook(FIX)
    g_nl, _ = detect_groups([c.value for c in wb["Nhập liệu"][1]])
    g_dl, _ = detect_groups([c.value for c in wb["Dữ liệu"][1]])
    assert len(g_nl) == len(g_dl) == 13
    # nhóm 9: Dữ liệu có cột 'Còn không' (AH), Nhập liệu thì không -> các nhóm sau lệch 1 cột
    assert g_dl[8].flag == 34 and g_nl[8].flag is None
    assert g_dl[9].time == 35 and g_nl[9].time == 34
    assert g_dl[5].type == 19 and g_dl[5].time == 20  # 'Cái này là' gắn với nhóm 6


def test_dedup_across_sheets_and_repeated_blocks(plan):
    assert len(plan.responses) == 269
    assert len(plan.unique) == 64
    assert len(plan.duplicates) == 205
    assert not plan.conflicts
    # 23 phản hồi tháng 10/2026 có ở cả Dữ liệu và Nhập liệu
    oct_dups = [r for r in plan.duplicates if r.day and (r.day.year, r.day.month) == (2026, 10)]
    assert len(oct_dups) == 23


def test_reimport_same_file_same_fingerprints(plan):
    again = plan_import(FIX.read_bytes())
    assert [p.fingerprint for p in again.sessions] == [p.fingerprint for p in plan.sessions]
    assert len({p.fingerprint for p in plan.sessions}) == len(plan.sessions)


def test_multi_session_response_split(plan):
    r = next(x for x in plan.unique if x.day == date(2026, 10, 5))
    assert [p.time_raw for p in r.sessions] == ["16g00-17g00", "17g00-18g00", "18g00-19g00", "19g00-20g00"]
    assert [len(p.attendees) for p in r.sessions] == [1, 2, 1, 1]


def test_16g45_and_duplicate_names(plan):
    r = next(x for x in plan.unique if x.day == date(2026, 10, 1))
    s1, s2 = r.sessions[0], r.sessions[1]
    assert (s1.start, s1.end) == (time(16, 45), time(17, 45))
    assert len(s2.attendees) == 4 and s2.attendees[1] == s2.attendees[2]  # 'Phúc, My, My, Quỳnh'


def test_kind_and_review_flags(plan):
    r = next(x for x in plan.unique if x.day == date(2026, 10, 19))
    extra = r.sessions[-1]
    assert extra.kind == "extra" and extra.time_raw == "19g00-19g30" and extra.status == "needs_review"
    r17 = next(x for x in plan.unique if x.day == date(2026, 10, 17))
    assert r17.sessions[-1].kind == "extra" and r17.sessions[-1].status == "confirmed"


def test_oct_2026_engine_vs_legacy_documented_diffs(plan):
    recs = [SessionRecord(id=p.fingerprint, day=p.day, start=p.start, end=p.end, location_label=p.location,
                          kind=p.kind, status=p.status, attendees=p.attendees) for p in plan.sessions]
    v = build_timesheet(recs, 2026, 10)
    assert len(v.rows) == 31
    # Sheet cũ: E=29 F=4 G=6 H=16 I=0. Khác biệt duy nhất ở E: 17/10 công thức cũ cộng đôi ca
    # 'Dạy thêm giờ' (vừa vào E vừa vào J). Engine mới: ca đó chỉ ở J.
    assert v.totals == {"E": 28, "F": 4, "G": 6, "H": 16, "I": 0}
    row17 = next(r for r in v.rows if r.day == date(2026, 10, 17)).cells
    assert row17["E"] == 1 and row17["G"] == 1 and row17["J"]
    # 19/10: ca thêm 30' không tên -> cần kiểm tra, không tự tính
    assert any(x["day"] == date(2026, 10, 19) for x in v.review)
    # ngày không có phản hồi (02/10) vẫn có hàng, B:L rỗng
    row2 = next(r for r in v.rows if r.day == date(2026, 10, 2)).cells
    assert all(row2[c] is None for c in "BCDEFGHIJKL")
