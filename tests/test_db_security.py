"""RLS & toàn vẹn ở mức DATABASE — truy cập trực tiếp bằng SQL (bỏ qua UI) với role ứng dụng."""
from datetime import date, time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from anhu.db import scalar
from anhu.services import BizError
from anhu.services import core, schedule

from .conftest import ALLOWED, PW


def _setup_session(as_user, uid, day=date(2026, 11, 3), names=("An",)):
    with as_user(uid) as c:
        cid = core.add_center(c, uid, f"TT-{uid[:4]}")
        lid = core.add_location(c, uid, f"CS-{uid[:4]}", "center_branch", cid)
        pid = schedule.add_one_off(c, uid, day=day, start=time(16, 45), end=time(17, 45), location_id=lid,
                                   expected=[{"name": n} for n in names], override_reason="test")
        aid = schedule.confirm(c, uid, uid, pid, [{"name": n} for n in names], override_reason="test")
    return lid, pid, aid


def test_app_role_is_not_superuser(pg, as_user, users):
    with as_user(users["a"]) as c:
        r = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
        assert c.execute(text("SELECT current_user")).scalar() == "anhu_app"
        assert r == (False, False)


def test_user_cannot_read_or_write_other_users_data(as_user, users):
    a, b = users["a"], users["b"]
    lid, pid, aid = _setup_session(as_user, a)
    with as_user(b) as c:
        for t in ("actual_sessions", "planned_sessions", "session_attendees", "locations", "centers", "app_settings"):
            assert scalar(c, f"SELECT count(*) FROM {t} WHERE owner_id = :o", o=a) == 0, t
        assert scalar(c, "SELECT count(*) FROM app_users WHERE id = :o", o=a) == 0
        # cập nhật/xóa trực tiếp: 0 dòng bị ảnh hưởng
        assert c.execute(text("UPDATE actual_sessions SET notes='hack' WHERE id = :i"), {"i": aid}).rowcount == 0
        assert c.execute(text("DELETE FROM actual_sessions WHERE id = :i"), {"i": aid}).rowcount == 0
    with as_user(b) as c:
        with pytest.raises(ProgrammingError):  # chèn dữ liệu đứng tên A -> RLS chặn
            c.execute(text("INSERT INTO centers(owner_id, name) VALUES (:o, 'x')"), {"o": a})
    with as_user(b) as c:
        with pytest.raises(DBAPIError):  # gắn học viên vào ca của A
            c.execute(text("INSERT INTO session_attendees(owner_id, session_id, position, name) VALUES (:o,:s,99,'x')"),
                      {"o": b, "s": aid})
    with as_user(a) as c:
        assert scalar(c, "SELECT notes FROM actual_sessions WHERE id = :i", i=aid) is None


def test_no_user_context_sees_nothing(as_user, users):
    _setup_session(as_user, users["a"], day=date(2026, 11, 4))
    with as_user(None) as c:
        assert scalar(c, "SELECT count(*) FROM actual_sessions") == 0
        assert scalar(c, "SELECT count(*) FROM app_users") == 0


def test_sharing_read_then_write(as_user, users):
    a, b = users["a"], users["b"]
    _, _, aid = _setup_session(as_user, a, day=date(2026, 11, 5))
    with as_user(a) as c:
        core.share_with(c, a, ALLOWED[1], False, lambda e: scalar(c, "SELECT app.user_id_by_email(:e)", e=e))
    with as_user(b) as c:
        assert scalar(c, "SELECT count(*) FROM actual_sessions WHERE id = :i", i=aid) == 1
        assert c.execute(text("UPDATE actual_sessions SET notes='x' WHERE id=:i"), {"i": aid}).rowcount == 0
        owners = core.visible_owners(c, b)
        assert [o["name"] for o in owners] == ["Cô B", "Cô A"] and owners[1]["can_write"] is False
    with as_user(b) as c:  # B không tự cấp quyền ghi cho mình
        assert c.execute(text("UPDATE data_shares SET can_write = true WHERE owner_id = :a"), {"a": a}).rowcount == 0
    with as_user(a) as c:
        core.unshare(c, a, b)
    with as_user(b) as c:
        assert scalar(c, "SELECT count(*) FROM actual_sessions WHERE id = :i", i=aid) == 0


def test_owner_cannot_be_changed(as_user, users):
    a, b = users["a"], users["b"]
    _, _, aid = _setup_session(as_user, a, day=date(2026, 11, 6))
    with as_user(a) as c:
        core.share_with(c, a, ALLOWED[1], True, lambda e: scalar(c, "SELECT app.user_id_by_email(:e)", e=e))
    with as_user(b) as c:
        with pytest.raises(DBAPIError, match="OWNER_IMMUTABLE"):
            c.execute(text("UPDATE actual_sessions SET owner_id = :b WHERE id = :i"), {"b": b, "i": aid})
    with as_user(a) as c:
        core.unshare(c, a, b)


def test_audit_log_written_by_trigger_and_not_writable(as_user, users):
    a = users["a"]
    _, _, aid = _setup_session(as_user, a, day=date(2026, 11, 9))
    with as_user(a, "sửa ghi chú") as c:
        c.execute(text("UPDATE actual_sessions SET notes='ghép lớp' WHERE id=:i"), {"i": aid})
    with as_user(a) as c:
        log = core.audit_for(c, "actual_sessions", aid)
        assert log[0]["action"] == "UPDATE" and log[0]["reason"] == "sửa ghi chú" and str(log[0]["actor_id"]) == a
        assert log[0]["after"]["notes"] == "ghép lớp" and log[0]["before"]["notes"] is None
    with as_user(a) as c:
        with pytest.raises(ProgrammingError):
            c.execute(text("DELETE FROM audit_log"))
    with as_user(users["b"]) as c:
        assert scalar(c, "SELECT count(*) FROM audit_log WHERE owner_id = :o", o=a) == 0


def test_locked_period_blocks_changes_and_unlock_needs_reason(as_user, users):
    from anhu.services import reports
    a = users["a"]
    _, pid, aid = _setup_session(as_user, a, day=date(2027, 1, 15))
    with as_user(a) as c:
        sha, _ = reports.lock_period(c, a, a, 2027, 1)
        assert len(sha) == 64
    with as_user(a) as c:
        with pytest.raises(DBAPIError, match="PERIOD_LOCKED"):
            c.execute(text("UPDATE actual_sessions SET notes='x' WHERE id=:i"), {"i": aid})
    with as_user(a) as c:
        with pytest.raises(DBAPIError, match="PERIOD_LOCKED"):
            c.execute(text("UPDATE session_attendees SET name='Khác' WHERE session_id=:i"), {"i": aid})
    with as_user(a) as c:   # chèn ca mới vào kỳ đã chốt cũng bị chặn
        with pytest.raises(DBAPIError, match="PERIOD_LOCKED"):
            c.execute(text("""INSERT INTO actual_sessions(owner_id, session_date, start_time, end_time, teaching_type, source)
                              VALUES (:o, '2027-01-20', '08:00', '09:00', 'center', 'manual')"""), {"o": a})
    with as_user(a) as c:
        with pytest.raises(BizError):
            reports.unlock_period(c, a, 2027, 1, "  ")
    with as_user(a) as c:  # bypass service: đổi trực tiếp không có lý do -> trigger chặn
        with pytest.raises(DBAPIError, match="UNLOCK_REASON_REQUIRED"):
            c.execute(text("UPDATE timesheet_periods SET status='open' WHERE owner_id=:o AND year=2027 AND month=1"), {"o": a})
    with as_user(a, "Trung tâm báo sai tên") as c:
        reports.unlock_period(c, a, 2027, 1, "Trung tâm báo sai tên")
    with as_user(a, "sửa sau mở kỳ") as c:
        c.execute(text("UPDATE actual_sessions SET notes='đã sửa' WHERE id=:i"), {"i": aid})
    with as_user(a) as c:
        log = [x for x in core.audit_for(c, "timesheet_periods", reports.period(c, a, 2027, 1)["id"])]
        assert any(x["reason"] == "Trung tâm báo sai tên" for x in log)


def test_max_two_active_users(pg, users):
    from anhu.db import tx
    from anhu.services.core import create_user
    with pytest.raises(DBAPIError, match="MAX_USERS"):
        with tx(pg["admin"]) as c:
            create_user(c, "co.c@example.test", "Cô C", PW, [])


def test_login_whitelist_and_wrong_password(pg, users):
    from anhu.db import tx
    with tx(pg["app"]) as c:
        assert core.authenticate(c, ALLOWED[0], PW, ALLOWED)["name"] == "Cô A"
        assert core.authenticate(c, ALLOWED[0].upper(), PW, ALLOWED) is not None
        assert core.authenticate(c, ALLOWED[0], "sai-mat-khau", ALLOWED) is None
        assert core.authenticate(c, ALLOWED[0], PW, [ALLOWED[1]]) is None  # bị gỡ khỏi whitelist
        assert core.authenticate(c, "nobody@example.test", PW, ALLOWED) is None
    with tx(pg["admin"]) as c:
        with pytest.raises(BizError):
            core.create_user(c, "la@example.test", "Lạ", PW, ALLOWED)
