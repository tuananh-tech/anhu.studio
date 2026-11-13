"""Chức năng vận hành dài hạn: quản trị tài khoản, lưu trữ mềm, loại ca tùy chỉnh, quy tắc có hiệu lực theo ngày,
bản chốt bất biến, sao lưu / khôi phục sang CSDL mới. Postgres thật, role ứng dụng (RLS bật)."""
import io
import json
import uuid
import zipfile
from datetime import date, time

import pytest
from openpyxl import load_workbook
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from anhu.db import scalar, tx
from anhu.domain.timesheet import TimesheetRules
from anhu.services import BizError, admin, backup, core, reports, schedule

from .conftest import ALLOWED, PW


def att(*names):
    return [{"name": n} for n in names]


@pytest.fixture(scope="module")
def pg2(pg):
    """CSDL MỚI trên cùng máy chủ = mô phỏng máy chủ đích khi chuyển máy chủ / khôi phục sau sự cố."""
    from sqlalchemy import create_engine
    from scripts.migrate import migrate
    root = pg["admin"].rsplit("/", 1)[0]
    name = "anhu_t2_" + uuid.uuid4().hex[:8]
    eng = create_engine(root.replace("postgresql://", "postgresql+psycopg://") + "/postgres", isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    eng.dispose()
    adm = f"{root}/{name}"
    migrate(adm, "app-pw-test")
    return {"admin": adm, "app": f"postgresql://anhu_app:app-pw-test@{adm.split('@', 1)[1]}"}


@pytest.fixture(scope="module")
def ctx(pg, users):
    a = users["a"]
    with tx(pg["app"], a) as c:
        cid = core.add_center(c, a, "TT Dài hạn")
        lid = core.add_location(c, a, "CS Dài hạn", "center_branch", cid)
    return {"a": a, "b": users["b"], "cid": str(cid), "lid": str(lid)}


def _session(as_user, a, lid, day, names=("An",), kind="regular", start=time(9), end=time(10)):
    with as_user(a, "test") as c:
        return schedule.add_and_confirm_extra(c, a, a, day=day, start=start, end=end, location_id=lid, session_kind=kind,
                                              attendees=att(*names), override_reason="test")


# ---------------- tài khoản & quyền ----------------
def test_first_account_is_admin_and_admin_functions_are_guarded(as_user, users):
    a, b = users["a"], users["b"]
    with as_user(a) as c:
        us = admin.list_users(c)
        assert {u["email"] for u in us} == set(ALLOWED)
        assert next(u for u in us if str(u["id"]) == a)["is_admin"] is True
        # admin chỉ thấy thông tin tài khoản — KHÔNG thấy dữ liệu nghiệp vụ của B
        assert scalar(c, "SELECT count(*) FROM students WHERE owner_id = :o", o=b) == 0
    with as_user(b) as c:
        with pytest.raises(DBAPIError, match="ADMIN_ONLY"):
            admin.list_users(c)
    with as_user(b) as c:   # tự nâng quyền bằng SQL trực tiếp -> chặn
        with pytest.raises(DBAPIError, match="ADMIN_ONLY"):
            c.execute(text("UPDATE app_users SET is_admin = true WHERE id = :u"), {"u": b})
    with as_user(b) as c:
        with pytest.raises(DBAPIError, match="ADMIN_ONLY"):
            admin.reset_password(c, a)
    with as_user(a) as c:   # đã đủ 2 tài khoản -> không tạo thêm
        with pytest.raises(DBAPIError, match="MAX_USERS"):
            admin.create_account(c, "co.c@example.test", "Cô C", [])
    with as_user(a) as c:
        with pytest.raises(DBAPIError, match="ADMIN_ONLY"):
            admin.set_account_active(c, a, False)   # không tự khóa mình


def test_admin_lock_and_reset_password_then_forced_change(pg, as_user, users):
    a, b = users["a"], users["b"]
    with as_user(a, "khóa thử") as c:
        admin.set_account_active(c, b, False)
    with tx(pg["app"]) as c:
        assert core.authenticate(c, ALLOWED[1], PW, ALLOWED) is None
    with as_user(a, "mở khóa") as c:
        admin.set_account_active(c, b, True)
        temp = admin.reset_password(c, b)
    with tx(pg["app"]) as c:
        assert core.authenticate(c, ALLOWED[1], PW, ALLOWED) is None       # mật khẩu cũ hết hiệu lực
        u = core.authenticate(c, ALLOWED[1], temp, ALLOWED)
        assert u and u["must_change"] is True
    with as_user(b) as c:
        core.change_password(c, b, temp, PW)                                # trả lại mật khẩu cho các test khác
    with tx(pg["app"]) as c:
        assert core.authenticate(c, ALLOWED[1], PW, ALLOWED)["must_change"] is False
    with as_user(a) as c:
        acts = [x for x in admin.audit_search(c, b)[0]]
    with as_user(b) as c:
        acts = admin.audit_search(c, b, table="app_users")[0]
        assert any(x["reason"] == "khóa thử" for x in acts)


# ---------------- lưu trữ mềm ----------------
def test_master_data_cannot_be_hard_deleted_only_archived(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        sid = core.add_student(c, a, "Bé Lưu Trữ", code="HV-LT")
    _session(as_user, a, ctx["lid"], date(2033, 1, 5), names=("Bé Lưu Trữ",))
    for t, i in (("students", sid), ("centers", ctx["cid"]), ("locations", ctx["lid"])):
        with as_user(a) as c:
            with pytest.raises(ProgrammingError, match="permission denied"):
                c.execute(text(f"DELETE FROM {t} WHERE id = :i"), {"i": i})
    with as_user(a, "nghỉ học") as c:
        core.archive_student(c, sid, True)
        assert all(s["id"] != sid for s in core.search_students(c, a))
        assert any(s["id"] == sid for s in core.search_students(c, a, status="archived"))
        # lịch sử buổi dạy giữ nguyên
        assert scalar(c, "SELECT count(*) FROM session_attendees sa JOIN actual_sessions x ON x.id = sa.session_id "
                         "WHERE sa.name = 'Bé Lưu Trữ' AND x.status = 'confirmed'") == 1
        core.archive_student(c, sid, False)
        assert any(s["id"] == sid for s in core.search_students(c, a))


def test_location_rename_and_archive_keep_history_labels(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        lid = core.add_location(c, a, "CS Đổi Tên", "center_branch", ctx["cid"])
    aid = _session(as_user, a, str(lid), date(2033, 2, 3))
    with as_user(a, "đổi tên cơ sở") as c:
        admin.update_location(c, lid, "CS Tên Mới", "center_branch", ctx["cid"])
        admin.archive_location(c, lid, True)
        assert scalar(c, "SELECT location_label FROM actual_sessions WHERE id = :i", i=aid) == "CS Đổi Tên"
        assert all(str(l["id"]) != str(lid) for l in core.locations(c, a))
        assert any(str(l["id"]) == str(lid) for l in core.locations(c, a, include_archived=True))
        admin.archive_location(c, lid, False)


# ---------------- danh mục: môn / lớp / nhóm / liên hệ ----------------
def test_subjects_groups_members_contacts(as_user, ctx):
    a, b = ctx["a"], ctx["b"]
    with as_user(a, "danh mục") as c:
        sub = admin.save_subject(c, a, "Tiếng Anh")
        gid = admin.save_group(c, a, "Lớp Sáng T7", "class", sub, ctx["lid"])
        s1 = core.add_student(c, a, "Bé Một")
        admin.add_member(c, a, gid, s1, date(2033, 1, 1))
        with pytest.raises(BizError, match="đã ở trong"):
            admin.add_member(c, a, gid, s1)
        assert admin.groups(c, a)[0]["members"] >= 1
        cid = admin.save_contact(c, a, s1, "Mẹ Bé Một", "Mẹ", "0901 234 567", "me@example.test", is_primary=True)
        admin.save_contact(c, a, s1, "Bố Bé Một", "Bố", "0907-000-111", is_primary=True)
        cs = admin.contacts(c, s1)
        assert [x["is_primary"] for x in cs].count(True) == 1 and cs[0]["name"] == "Bố Bé Một"
        with pytest.raises(BizError, match="điện thoại"):
            admin.save_contact(c, a, s1, "X", phone="abc")
        mid = scalar(c, "SELECT id FROM class_group_members WHERE group_id = :g AND student_id = :s", g=gid, s=s1)
        admin.leave_member(c, mid, date(2033, 6, 30))
        admin.archive_contact(c, cid, True)
        assert len(admin.contacts(c, s1)) == 1
    with as_user(b) as c:   # B không gắn liên hệ/lớp vào học viên của A (bỏ qua UI)
        assert admin.contacts(c, s1) == []
        with pytest.raises(DBAPIError):
            c.execute(text("INSERT INTO contacts(owner_id, student_id, name) VALUES (:o, :s, 'x')"), {"o": b, "s": s1})


# ---------------- loại ca & quy tắc có hiệu lực ----------------
def test_custom_kind_not_counted_until_rules_include_it_with_effective_date(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        with pytest.raises(DBAPIError, match="KIND_UNKNOWN"):
            c.execute(text("""INSERT INTO actual_sessions(owner_id, session_date, start_time, end_time, teaching_type, source, session_kind)
                              VALUES (:o, '2035-01-10', '08:00', '09:00', 'center', 'manual', 'khong_co')"""), {"o": a})
    with as_user(a, "loại ca") as c:
        code = admin.add_custom_kind(c, a, "Ca ôn thi")
        assert code.startswith("c_") and code in {k["code"] for k in admin.kinds(c, a)}
        admin.save_kind(c, a, "trial", "Học thử", True, 15)
        assert next(k for k in admin.kinds(c, a) if k["code"] == "trial")["label"] == "Học thử"
        with pytest.raises(BizError):
            admin.save_kind(c, a, "regular", "Ca thường", False)
    _session(as_user, a, ctx["lid"], date(2035, 1, 10), names=("An", "Bình"), kind=code)
    _session(as_user, a, ctx["lid"], date(2035, 3, 10), names=("An", "Bình"), kind=code)
    with as_user(a) as c:
        v, _, _ = reports.timesheet(c, a, 2035, 1, "center")
        assert v.totals["F"] == 0 and any("không thuộc quy tắc" in r["reason"] for r in v.review)
    with as_user(a, "quy tắc mới") as c:
        cur = core.timesheet_rules(c, a)
        r = TimesheetRules(cur.thirty_minute_max, cur.thirty_kinds, cur.j_kinds, tuple(cur.hv_kinds) + (code,), confirmed=True)
        nv = core.save_timesheet_rules(c, a, r, date(2035, 2, 1), "thêm Ca ôn thi", a)
    with as_user(a) as c:
        v1, _, _ = reports.timesheet(c, a, 2035, 1, "center")   # tháng trước ngày hiệu lực: quy tắc cũ
        v3, _, _ = reports.timesheet(c, a, 2035, 3, "center")
        assert v1.totals["F"] == 0 and v1.rules.version < nv
        assert v3.totals["F"] == 1 and v3.rules.version == nv and v3.rules.confirmed
        assert core.rule_versions(c, a)[0]["note"] == "thêm Ca ôn thi"


def test_labels_and_export_options(as_user, ctx):
    a = ctx["a"]
    _session(as_user, a, ctx["lid"], date(2033, 4, 2))
    with as_user(a, "nhãn") as c:
        admin.save_labels(c, a, {"confirmed": "Đã học", "planned": ""}, {"student_off": "Bé nghỉ"})
        lb = admin.labels(c, a)
        assert lb["status"]["confirmed"] == "Đã học" and lb["status"]["planned"] == "Dự kiến" and lb["cancel"]["student_off"] == "Bé nghỉ"
        with pytest.raises(BizError):
            admin.save_export_options(c, a, {"sheet_name": "a/b"})
        admin.save_export_options(c, a, {"sheet_name": "Công T4", "file_prefix": "CC", "include_money_row": "1"})
        wb = load_workbook(io.BytesIO(reports.export_timesheet(c, a, 2033, 4)))
        assert wb.sheetnames == ["Công T4"]            # sheet phụ đã tắt
        ws = wb["Công T4"]
        assert ws["A1"].value == "NGÀY " and ws["E2"].value == "1 HV" and ws.max_row >= 32   # bố cục gốc giữ nguyên
        admin.save_export_options(c, a, dict(admin.DEFAULT_EXPORT))
        assert load_workbook(io.BytesIO(reports.export_timesheet(c, a, 2033, 4))).sheetnames == ["Chấm công", "Bổ sung", "Chi tiết ca", "Thông tin"]


# ---------------- kỳ chốt bất biến ----------------
def test_locked_period_is_frozen_against_later_master_data_changes(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        lid = core.add_location(c, a, "CS Chốt", "center_branch", ctx["cid"])
        rid = core.add_rate(c, a, "center", "hv1", 200000, date(2036, 1, 1), confirmed=True, center_id=ctx["cid"])
    _session(as_user, a, str(lid), date(2036, 2, 3), names=("Chi",))
    with as_user(a, "chốt") as c:
        live, _, pay = reports.timesheet(c, a, 2036, 2, "center")
        sha, _ = reports.lock_period(c, a, a, 2036, 2)
        with pytest.raises(BizError, match="đã chốt"):
            reports.lock_period(c, a, a, 2036, 2)
    with as_user(a, "thay đổi sau chốt") as c:
        admin.update_location(c, lid, "CS Chốt (đổi tên)", "center_branch", ctx["cid"])
        cur = core.timesheet_rules(c, a)
        core.save_timesheet_rules(c, a, TimesheetRules(cur.thirty_minute_max, cur.thirty_kinds, cur.j_kinds, (), confirmed=False),
                                  date(2036, 1, 1), "thử: không đếm E–H", a)
        core.archive_rate(c, rid, True)
    with as_user(a) as c:
        snap = reports.locked_snapshot(c, a, 2036, 2)
        view, spay = reports.snapshot_view(snap)
        assert view.totals == live.totals and view.totals["E"] == 1
        assert spay.complete and spay.total == 200000
        row = next(r for r in view.rows if r.day == date(2036, 2, 3))
        assert row.cells["B"] == "CS Chốt" and row.cells["A"] == date(2036, 2, 3)
        assert sum(1 for r in view.rows if not r.session_ids) >= 27      # ngày trống vẫn đủ
        assert reports.locked_export(c, a, 2036, 2)["export_sha256"] == sha
        # tính lại từ dữ liệu hiện tại thì khác (chứng minh snapshot mới là nguồn hiển thị)
        now, _, _ = reports.timesheet(c, a, 2036, 2, "center")
        assert now.totals["E"] == 0
    with as_user(a) as c:   # bản chốt đã lưu không sửa được, kể cả bằng SQL trực tiếp
        with pytest.raises(DBAPIError, match="SNAPSHOT_IMMUTABLE"):
            c.execute(text("UPDATE timesheet_period_versions SET export_sha256 = 'x' WHERE owner_id = :o"), {"o": a})
    with as_user(a, "TT yêu cầu sửa") as c:
        reports.unlock_period(c, a, 2036, 2, "TT yêu cầu sửa")
        reports.lock_period(c, a, a, 2036, 2)
        vs = admin.period_versions(c, a, 2036, 2)
        assert [v["version"] for v in vs] == [2, 1] and vs[1]["unlock_reason"] == "TT yêu cầu sửa"
        assert admin.period_version_file(c, vs[1]["id"])["export_sha256"] == sha
    with as_user(a, "dọn") as c:   # trả quy tắc về như cũ cho các test sau
        core.save_timesheet_rules(c, a, TimesheetRules(cur.thirty_minute_max, cur.thirty_kinds, cur.j_kinds, cur.hv_kinds,
                                                       confirmed=cur.confirmed), date(2036, 1, 1), "khôi phục", a)


def test_recon_mapping_profiles(as_user, ctx):
    a = ctx["a"]
    with as_user(a, "ánh xạ") as c:
        with pytest.raises(BizError, match="Ngày"):
            admin.save_mapping(c, a, "Thiếu ngày", None, {"columns": {"times": "C"}})
        with pytest.raises(BizError, match="không hợp lệ"):
            admin.save_mapping(c, a, "Sai cột", None, {"columns": {"date": "1"}})
        admin.save_mapping(c, a, "Mẫu TT", ctx["cid"], {"sheet": "Chấm công", "start_row": "3",
                                                        "columns": {"date": "a", "times": "C", "names": "D"}}, is_default=True)
        m = admin.default_mapping(c, a, ctx["cid"])
        assert m == {"sheet": "Chấm công", "start_row": 3, "columns": {"date": "A", "times": "C", "names": "D"}}


def test_audit_search_shows_readable_changes(as_user, ctx):
    a = ctx["a"]
    with as_user(a) as c:
        sid = core.add_student(c, a, "Bé Nhật Ký")
    with as_user(a, "sửa tên theo phụ huynh") as c:
        core.update_student(c, sid, display_name="Bé Nhật Ký Mới")
        items, total = admin.audit_search(c, a, table="students", q="phụ huynh")
        assert total >= 1
        ch = dict((k, (b, n)) for k, b, n in items[0]["changes"])
        assert ch["display_name"] == ("Bé Nhật Ký", "Bé Nhật Ký Mới") and "search_name" not in ch


# ---------------- sao lưu / khôi phục ----------------
def test_backup_restore_roundtrip_into_new_database(pg2, as_user, ctx):
    a = ctx["a"]
    _session(as_user, a, ctx["lid"], date(2034, 5, 6), names=("Dung", "Dung", "Em"))
    with as_user(a, "chốt 05/2034") as c:
        sha, _ = reports.lock_period(c, a, a, 2034, 5)
        months = [(2033, 1), (2033, 4), (2034, 5), (2035, 3)]
        before = {ym: reports.timesheet(c, a, *ym, "center")[0] for ym in months}
        blob, name, mf = backup.export_backup(c, a, a)
        info = admin.system_info(c, a)
    assert name.endswith(".zip") and mf["locked_exports"]["2034-05"] == sha and info["last_backup"]
    # file hỏng / bị sửa -> từ chối
    z = zipfile.ZipFile(io.BytesIO(blob))
    data = json.loads(z.read("data.json"))
    data["students"] = data["students"][:-1]
    bad = io.BytesIO()
    with zipfile.ZipFile(bad, "w") as w:
        w.writestr("manifest.json", z.read("manifest.json"))
        w.writestr("data.json", json.dumps(data))
    with as_user(a) as c:
        with pytest.raises(BizError, match="mã băm"):
            backup.read_backup(bad.getvalue())
        with pytest.raises(BizError, match="không phải"):
            backup.read_backup(b"PK not a zip")
        with pytest.raises(BizError, match="đã có dữ liệu"):     # không khôi phục đè lên tài khoản đang dùng
            backup.restore_backup(c, a, a, blob)

    # máy chủ mới: tạo tài khoản, khôi phục
    with tx(pg2["admin"]) as c:
        nu = core.create_user(c, "moi@example.test", "Cô A (máy chủ mới)", PW, [])
    with tx(pg2["app"], nu, "kiểm tra") as c:
        rep = backup.verify_backup(c, nu, nu, blob, name)
        assert rep["empty_target"] and rep["restorable"]
    with tx(pg2["app"], nu, "khôi phục") as c:
        st = backup.restore_backup(c, nu, nu, blob, name)
        assert st["restored"]["actual_sessions"] == mf["counts"]["actual_sessions"] and st["audit"] == mf["counts"]["audit_log"]
    with tx(pg2["app"], nu) as c:
        for ym in months:
            after = reports.timesheet(c, nu, *ym, "center")[0]
            assert after.totals == before[ym].totals and any(after.totals.values()), ym
            assert [r.cells for r in after.rows] == [r.cells for r in before[ym].rows], ym
        assert reports.locked_export(c, nu, 2034, 5)["export_sha256"] == sha       # file đã chốt nguyên vẹn
        v, _ = reports.snapshot_view(reports.locked_snapshot(c, nu, 2034, 5))
        assert v.totals["G"] >= 1
        for t in backup.TABLES:
            assert scalar(c, f"SELECT count(*) FROM {t} WHERE owner_id = :o", o=nu) == mf["counts"][t], t
        assert not backup.is_empty(c, nu)
        with pytest.raises(DBAPIError, match="PERIOD_LOCKED"):   # trigger khóa kỳ vẫn hoạt động sau khôi phục
            c.execute(text("UPDATE actual_sessions SET notes = 'x' WHERE owner_id = :o AND session_date = '2034-05-06'"), {"o": nu})
    with tx(pg2["app"], nu) as c:
        with pytest.raises(BizError, match="đã có dữ liệu"):     # khôi phục lần 2 bị từ chối
            backup.restore_backup(c, nu, nu, blob)
        assert [h["kind"] for h in backup.history(c, nu)][:2] == ["restore", "verify"]


def test_admin_creates_second_account_on_new_server(pg2):
    with tx(pg2["admin"]) as c:
        boss = scalar(c, "SELECT id FROM app_users WHERE email = 'moi@example.test'")
        if not boss:
            boss = core.create_user(c, "moi@example.test", "Quản trị", PW, [])
    with tx(pg2["app"], boss, "tạo tài khoản") as c:
        with pytest.raises(BizError, match="danh sách được phép"):
            admin.create_account(c, "x@example.test", "X", ["khac@example.test"])
        temp = admin.create_account(c, "gv2@example.test", "Giáo viên 2", [])
    with tx(pg2["app"]) as c:
        u = core.authenticate(c, "gv2@example.test", temp, [])
        assert u["must_change"] and not u["is_admin"]
    with tx(pg2["app"], u["id"]) as c:   # tài khoản mới có cấu hình mặc định, dữ liệu riêng trống
        assert core.timesheet_rules(c, u["id"]).version == 1
        assert scalar(c, "SELECT count(*) FROM actual_sessions") == 0


def test_full_server_dump_and_restore_keeps_data_and_rls(pg, users, tmp_path):
    """Sao lưu toàn máy chủ (pg_dump) -> khôi phục vào CSDL mới -> migrate cấp lại quyền -> dữ liệu & RLS nguyên vẹn."""
    from sqlalchemy import create_engine
    from scripts import backup as bk
    from scripts.migrate import migrate, status
    a, b = users["a"], users["b"]
    f = bk.dump(pg["admin"], tmp_path)
    assert f.stat().st_size > 0 and f.with_suffix(".sha256").exists()
    root = pg["admin"].rsplit("/", 1)[0]
    name = "anhu_rs_" + uuid.uuid4().hex[:8]
    eng = create_engine(root.replace("postgresql://", "postgresql+psycopg://") + "/postgres", isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    eng.dispose()
    target = f"{root}/{name}"
    bk.restore_dump(f, target)
    assert migrate(target, "app-pw-test") == [] and status(target)["pending"] == []   # không có migration nào bị chạy lại
    app2 = f"postgresql://anhu_app:app-pw-test@{target.split('@', 1)[1]}"
    for uid in (a, b):
        with tx(pg["app"], uid) as c1, tx(app2, uid) as c2:
            for t in ("actual_sessions", "session_attendees", "students", "timesheet_periods", "audit_log"):
                q = f"SELECT count(*) FROM {t} WHERE owner_id = :o"
                assert scalar(c1, q, o=uid) == scalar(c2, q, o=uid), t
    with tx(app2, b) as c:   # RLS vẫn chặn sau khôi phục
        assert scalar(c, "SELECT count(*) FROM actual_sessions WHERE owner_id = :o", o=a) == 0
