"""Luồng nghiệp vụ end-to-end ở tầng dịch vụ (Postgres thật, role ứng dụng, RLS bật)."""
import io
from datetime import date, time
from pathlib import Path

import pytest
from openpyxl import load_workbook

from anhu.db import scalar
from anhu.domain.calendar_month import month_days
from anhu.services import BizError, ConflictError
from anhu.services import core, reports, schedule

FIX = Path(__file__).parent / "fixtures" / "anon_source.xlsx"


@pytest.fixture
def ctx(as_user, users):
    """Mỗi test dùng tháng riêng để độc lập; tài khoản A."""
    a = users["a"]
    with as_user(a) as c:
        cid = core.add_center(c, a, "Trung tâm X")
        lid = core.add_location(c, a, "Cơ sở X1", "center_branch", cid)
        priv = core.add_location(c, a, "Nhà học viên", "private_place")
    return {"a": a, "cid": str(cid), "lid": str(lid), "priv": str(priv)}


def row(view, d):
    return next(r for r in view.rows if r.day == d).cells


def att(*names):
    return [{"name": n} for n in names]


def test_f1_recurring_3_per_week_generates_planned_not_pay(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        for wd in (0, 2, 4):
            schedule.create_template(c, a, weekday=wd, start=time(17), end=time(18), valid_from=date(2028, 3, 1),
                                     valid_to=date(2028, 4, 30), location_id=ctx["lid"], expected=att("An"))
        n = schedule.ensure_planned(c, a, date(2028, 4, 1), date(2028, 4, 30))
        assert schedule.ensure_planned(c, a, date(2028, 4, 1), date(2028, 4, 30)) == 0  # idempotent
        ps = schedule.planned_between(c, a, date(2028, 4, 1), date(2028, 4, 30))
        assert n == len(ps) == 12 and all(p["session_date"].weekday() in (0, 2, 4) for p in ps)
        with pytest.raises(ConflictError):  # lịch lặp trùng khung giờ bị chặn nếu không có lý do
            schedule.create_template(c, a, weekday=0, start=time(17, 30), end=time(18, 30), valid_from=date(2028, 4, 1),
                                     valid_to=date(2028, 4, 30), location_id=ctx["lid"])
        view, _, _ = reports.timesheet(c, a, 2028, 4)
        assert sum(view.totals.values()) == 0 and len(view.rows) == 30


def test_acceptance1_confirm_from_schedule_shows_in_timesheet(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        schedule.create_template(c, a, weekday=1, start=time(16, 45), end=time(17, 45), valid_from=date(2028, 5, 1),
                                 location_id=ctx["lid"], expected=att("Phúc", "My", "My", "Quỳnh"))
        schedule.ensure_planned(c, a, date(2028, 5, 1), date(2028, 5, 31))
        p = schedule.planned_between(c, a, date(2028, 5, 2), date(2028, 5, 2))[0]
        schedule.confirm(c, a, a, p["id"], p["expected_students"])
        view, _, _ = reports.timesheet(c, a, 2028, 5)
        r = row(view, date(2028, 5, 2))
        assert (r["B"], r["C"], r["D"], r["H"]) == ("Cơ sở X1", "16g45-17g45", "Phúc, My, My, Quỳnh", 1)
        assert row(view, date(2028, 5, 9))["C"] is None  # tuần sau chưa xác nhận -> chưa có công


def test_f2_cancel_keeps_day_blank_and_f5_makeup_no_double(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        p1 = schedule.add_one_off(c, a, day=date(2028, 6, 5), start=time(17), end=time(18), location_id=ctx["lid"], expected=att("An"))
        schedule.cancel(c, p1, "student_off", "ốm")
        with pytest.raises(BizError):
            schedule.confirm(c, a, a, p1, att("An"))
        mk = schedule.makeup(c, a, p1, day=date(2028, 6, 7), start=time(9), end=time(10))
        with pytest.raises(BizError):
            schedule.makeup(c, a, p1, day=date(2028, 6, 8), start=time(9), end=time(10))
        with pytest.raises(BizError):
            schedule.restore(c, p1)
        schedule.confirm(c, a, a, mk, att("An"))
        view, _, _ = reports.timesheet(c, a, 2028, 6)
        assert all(row(view, date(2028, 6, 5))[k] is None for k in "BCDEFGHIJKL")
        assert row(view, date(2028, 6, 7))["E"] == 1 and view.totals["E"] == 1
        data = reports.export_timesheet(c, a, 2028, 6)
    ws = load_workbook(io.BytesIO(data))["Chấm công"]
    assert ws["A7"].value.date() == date(2028, 6, 5) and all(ws[f"{k}7"].value is None for k in "BCDEFGHIJKL")


def test_f3_edit_single_vs_series(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        t = schedule.create_template(c, a, weekday=0, start=time(17), end=time(18), valid_from=date(2028, 7, 3), location_id=ctx["lid"])
        schedule.ensure_planned(c, a, date(2028, 7, 1), date(2028, 8, 31))
        ps = {p["session_date"]: p for p in schedule.planned_between(c, a, date(2028, 7, 1), date(2028, 8, 31))}
        schedule.edit_single(c, a, ps[date(2028, 7, 10)]["id"], start=time(18), end=time(19))
        after = {p["session_date"]: p["start_time"] for p in schedule.planned_between(c, a, date(2028, 7, 1), date(2028, 8, 31))}
        assert after[date(2028, 7, 3)] == time(17) and after[date(2028, 7, 10)] == time(18) and after[date(2028, 7, 17)] == time(17)
        # confirm tuần 17/07, rồi đổi cả chuỗi từ 31/07
        schedule.confirm(c, a, a, ps[date(2028, 7, 17)]["id"], att("An"))
        schedule.change_series(c, a, t, date(2028, 7, 31), start=time(15), end=time(16))
        schedule.ensure_planned(c, a, date(2028, 7, 1), date(2028, 8, 31))
        after = {p["session_date"]: p["start_time"] for p in schedule.planned_between(c, a, date(2028, 7, 1), date(2028, 8, 31))}
        assert after[date(2028, 7, 3)] == time(17) and after[date(2028, 7, 10)] == time(18)
        assert after[date(2028, 7, 24)] == time(17) and after[date(2028, 7, 31)] == time(15) and after[date(2028, 8, 28)] == time(15)
        series = next(x["series_id"] for x in schedule.templates(c, a) if str(x["id"]) == str(t))
        versions = [x for x in schedule.templates(c, a) if x["series_id"] == series]
        assert len(versions) == 2 and any(v["valid_to"] == date(2028, 7, 30) for v in versions)  # giữ lịch sử


def test_f4_extra_session_needs_confirmation_and_template_untouched(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        before = len(schedule.templates(c, a))
        pid = schedule.add_one_off(c, a, day=date(2028, 8, 15), start=time(20), end=time(21), location_id=ctx["lid"], expected=att("Bình"))
        v, _, _ = reports.timesheet(c, a, 2028, 8)
        assert row(v, date(2028, 8, 15))["E"] is None
        schedule.confirm(c, a, a, pid, att("Bình"))
        v, _, _ = reports.timesheet(c, a, 2028, 8)
        assert row(v, date(2028, 8, 15))["E"] == 1 and len(schedule.templates(c, a)) == before


def test_f6_center_and_private_same_day(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        p1 = schedule.add_one_off(c, a, day=date(2028, 9, 4), start=time(9), end=time(10), location_id=ctx["lid"])
        p2 = schedule.add_one_off(c, a, day=date(2028, 9, 4), start=time(19), end=time(20), location_id=ctx["priv"])
        schedule.confirm(c, a, a, p1, att("An"))
        schedule.confirm(c, a, a, p2, att("Riêng 1", "Riêng 2"))
        v, _, _ = reports.timesheet(c, a, 2028, 9, "center")
        r = row(v, date(2028, 9, 4))
        assert r["C"] == "9g00-10g00" and r["E"] == 1 and r["F"] is None
        vp, _, _ = reports.timesheet(c, a, 2028, 9, "private")
        assert row(vp, date(2028, 9, 4))["F"] == 1
        d = reports.dashboard(c, a, 2028, 9, date(2028, 9, 30))
        assert (d["center_n"], d["private_n"]) == (1, 1)
        assert d["private_unpriced"] == 1  # không tự suy ra giá dạy riêng


def test_f7_edit_2_to_3_students(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        pid = schedule.add_one_off(c, a, day=date(2028, 10, 2), start=time(9), end=time(10), location_id=ctx["lid"])
        aid = schedule.confirm(c, a, a, pid, att("An", "Bình"))
        v, _, _ = reports.timesheet(c, a, 2028, 10)
        assert (v.totals["F"], v.totals["G"]) == (1, 0)
    with as_user(a, "thêm HV đến muộn") as c:
        schedule.update_actual(c, a, aid, attendees=att("An", "Bình", "Minh"), start=time(9), end=time(10),
                               session_kind="regular", notes=None)
        v, _, _ = reports.timesheet(c, a, 2028, 10)
        assert (v.totals["F"], v.totals["G"]) == (0, 1)
        log = core.audit_for(c, "session_attendees", scalar(c, "SELECT id FROM session_attendees WHERE session_id=:s AND position=3", s=aid))
        assert log and log[0]["reason"] == "thêm HV đến muộn"


def test_f8_same_name_two_students_by_id(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        s1 = core.add_student(c, a, "My")
        s2 = core.add_student(c, a, "My")
        exp = schedule.expected_from_form(c, a, [str(s1), str(s2)], "")
        assert len(exp) == 2 and exp[0]["student_id"] != exp[1]["student_id"]
        pid = schedule.add_one_off(c, a, day=date(2028, 11, 6), start=time(9), end=time(10), location_id=ctx["lid"], expected=exp)
        schedule.confirm(c, a, a, pid, exp)
        v, _, _ = reports.timesheet(c, a, 2028, 11)
        assert row(v, date(2028, 11, 6))["F"] == 1 and row(v, date(2028, 11, 6))["D"] == "My, My"
        assert core.student_stats(c, s1)["total_sessions"] == 1 and core.student_stats(c, s2)["total_sessions"] == 1


def test_conflict_warning_and_override(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        schedule.add_one_off(c, a, day=date(2028, 12, 4), start=time(9), end=time(10), location_id=ctx["lid"])
        with pytest.raises(ConflictError):
            schedule.add_one_off(c, a, day=date(2028, 12, 4), start=time(9, 30), end=time(10, 30), location_id=ctx["lid"])
        pid = schedule.add_one_off(c, a, day=date(2028, 12, 4), start=time(9, 30), end=time(10, 30), location_id=ctx["lid"],
                                   override_reason="hai nhóm ghép phòng")
        assert schedule.planned(c, pid)["conflict_override_reason"] == "hai nhóm ghép phòng"
        # sát nửa đêm: ca 23:00-23:59 hợp lệ, ca qua nửa đêm bị từ chối
        schedule.add_one_off(c, a, day=date(2028, 12, 31), start=time(23), end=time(23, 59), location_id=ctx["lid"])
        with pytest.raises(BizError):
            schedule.add_one_off(c, a, day=date(2028, 12, 31), start=time(23, 30), end=time(0, 30), location_id=ctx["lid"])


def test_zero_attendees_goes_to_review_not_pay(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        pid = schedule.add_one_off(c, a, day=date(2029, 1, 8), start=time(9), end=time(10), location_id=ctx["lid"])
        aid = schedule.confirm(c, a, a, pid, [])
        assert schedule.actual(c, aid)["status"] == "needs_review"
        v, _, _ = reports.timesheet(c, a, 2029, 1)
        assert sum(v.totals.values()) == 0 and v.review


def test_acceptance5_legacy_import_twice_no_double(as_user, ctx):
    a = ctx["a"]
    data = FIX.read_bytes()
    with as_user(a) as c:
        iid, plan, stats, _ = reports.legacy_preview(c, a, "anon.xlsx", data)
        assert stats["duplicates"] == 205 and stats["new"] == len(plan.sessions)
        res = reports.legacy_apply(c, a, a, iid)
        v1, _, _ = reports.timesheet(c, a, 2026, 10)
    with as_user(a) as c:
        iid2, _, stats2, _ = reports.legacy_preview(c, a, "anon.xlsx", data)
        assert stats2["new"] == 0 and stats2["already_imported"] == res["inserted"]
        res2 = reports.legacy_apply(c, a, a, iid2)
        assert res2["inserted"] == 0
        v2, _, _ = reports.timesheet(c, a, 2026, 10)
    assert v1.totals == v2.totals == {"E": 28, "F": 4, "G": 6, "H": 16, "I": 0}


def test_acceptance7_reconcile_end_to_end_and_source_preserved(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        for d, t, n in [(3, (16, 45), ("An",)), (3, (17, 45), ("Phúc", "My", "My", "Quỳnh")), (5, (9, 0), ("Bình", "Minh"))]:
            pid = schedule.add_one_off(c, a, day=date(2029, 2, d), start=time(*t), end=time(t[0] + 1, t[1]), location_id=ctx["lid"])
            schedule.confirm(c, a, a, pid, att(*n))
        data = reports.export_timesheet(c, a, 2029, 2)
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Chấm công"]
    ws["C5"] = "16g45-17g45\n18g00-19g00"   # sai giờ ca thứ 2 ngày 03/02
    for k in "BCDEFGHIJK":
        ws[f"{k}7"] = None                   # thiếu ca ngày 05/02
    buf = io.BytesIO(); wb.save(buf); center = buf.getvalue()
    with as_user(a) as c:
        before = scalar(c, "SELECT md5(string_agg(a.id::text || a.start_time::text || a.status, ',' ORDER BY a.id)) FROM actual_sessions a "
                           "WHERE owner_id=:o AND session_date BETWEEN '2029-02-01' AND '2029-02-28'", o=a)
        iid = reports.recon_upload(c, a, ctx["cid"], 2029, 2, "tt.xlsx", center)
        from anhu.domain.reconcile import preview, suggest_mapping
        items = reports.recon_run(c, a, iid, suggest_mapping(preview(center)))
        cats = {(i.day, i.category) for i in items}
        assert (date(2029, 2, 3), "time_mismatch") in cats and (date(2029, 2, 5), "missing_in_center") in cats
        after = scalar(c, "SELECT md5(string_agg(a.id::text || a.start_time::text || a.status, ',' ORDER BY a.id)) FROM actual_sessions a "
                          "WHERE owner_id=:o AND session_date BETWEEN '2029-02-01' AND '2029-02-28'", o=a)
        assert before == after                                   # không sửa dữ liệu cá nhân
        assert bytes(reports.recon_import(c, iid)["file_bytes"]) == center  # bảo toàn file gốc
        item = reports.recon_items(c, iid, category="time_mismatch")[0]
        with pytest.raises(BizError):
            reports.recon_resolve(c, a, item["id"], "accept_theirs", "")
        reports.recon_resolve(c, a, item["id"], "accept_ours", "giờ thực tế đúng theo lịch")
        rep = load_workbook(io.BytesIO(reports.recon_report_xlsx(c, iid)))["Biên bản đối soát"]
        assert any("Lệch giờ" == (rep.cell(r, 2).value) for r in range(5, rep.max_row + 1))


def test_pay_hidden_until_rates_confirmed(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        pid = schedule.add_one_off(c, a, day=date(2029, 3, 5), start=time(9), end=time(10), location_id=ctx["lid"])
        schedule.confirm(c, a, a, pid, att("An"))
        rid = core.add_rate(c, a, "center", "hv1", 200000, date(2029, 1, 1), confirmed=False, note="gợi ý từ Excel")
        _, _, pay = reports.timesheet(c, a, 2029, 3)
        assert pay.total == 0 and not pay.complete
        ws = load_workbook(io.BytesIO(reports.export_timesheet(c, a, 2029, 3)))["Chấm công"]
        assert ws["E35"].value is None and ws["K35"].value is None  # 31 ngày -> TỔNG ở hàng 34
        core.confirm_rate(c, rid, True)
        _, _, pay = reports.timesheet(c, a, 2029, 3)
        assert pay.total == 200000 and pay.complete
        ws = load_workbook(io.BytesIO(reports.export_timesheet(c, a, 2029, 3)))["Chấm công"]
        assert ws["B34"].value == "TỔNG" and ws["E35"].value == "=200000*E34"


def test_acceptance_c9_empty_months_export(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        for y, m in [(2030, 2), (2031, 2), (2030, 4), (2030, 1)]:
            ws = load_workbook(io.BytesIO(reports.export_timesheet(c, a, y, m)))["Chấm công"]
            days = month_days(y, m)
            assert [ws[f"A{3 + i}"].value.date() for i in range(len(days))] == days
            assert ws[f"B{3 + len(days)}"].value == "TỔNG"
            assert all(ws[f"{k}{r}"].value is None for r in range(3, 3 + len(days)) for k in "BCDEFGHIJKL")
