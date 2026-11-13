"""Tài khoản, chia sẻ, danh mục trung tâm/cơ sở, học viên, cài đặt."""
from __future__ import annotations

import json

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import text

from ..db import one, rows, scalar
from ..domain.text import clean_ws, fold
from ..domain.timesheet import TimesheetRules
from . import BizError

_ph = PasswordHasher()
_DUMMY = _ph.hash("dummy-password-for-timing")


# ---------------- tài khoản (kết nối ADMIN) ----------------
def create_user(admin_conn, email: str, display_name: str, password: str, allowed: list[str]) -> str:
    email = email.strip().lower()
    if allowed and email not in allowed:
        raise BizError("Email không nằm trong danh sách được duyệt (ANHU_ALLOWED_EMAILS).")
    if len(password) < 10:
        raise BizError("Mật khẩu tối thiểu 10 ký tự.")
    uid = scalar(admin_conn, "INSERT INTO app_users(email, display_name, password_hash) VALUES (:e,:n,:h) RETURNING id",
                 e=email, n=display_name, h=_ph.hash(password))
    admin_conn.execute(text("INSERT INTO teacher_profiles(user_id, owner_id, display_name) VALUES (:u,:u,:n)"),
                       {"u": uid, "n": display_name})
    admin_conn.execute(text("INSERT INTO app_settings(owner_id, timesheet_rules) VALUES (:u, CAST(:r AS jsonb))"),
                       {"u": uid, "r": json.dumps(TimesheetRules().to_dict())})
    admin_conn.execute(text("""INSERT INTO timesheet_rule_versions(owner_id, version, valid_from, rules, confirmed, note)
                               VALUES (:u, 1, DATE '2000-01-01', CAST(:r AS jsonb), false, 'Quy tắc đề xuất mặc định')"""),
                       {"u": uid, "r": json.dumps(TimesheetRules().to_dict())})
    return str(uid)


def authenticate(conn, email: str, password: str, allowed: list[str]):
    email = (email or "").strip().lower()
    r = one(conn, "SELECT * FROM app.auth_lookup(:e)", e=email)
    ok = False
    try:
        ok = _ph.verify(r["password_hash"] if r else _DUMMY, password or "")
    except (VerificationError, InvalidHashError):
        ok = False
    if not r or not ok or not r["is_active"] or (allowed and email not in allowed):
        return None
    conn.execute(text("SELECT app.touch_login(:u)"), {"u": r["id"]})
    return {"id": str(r["id"]), "name": r["display_name"], "must_change": r["must_change_password"], "is_admin": r["is_admin"]}


def change_password(conn, uid, old: str, new: str):
    r = one(conn, "SELECT password_hash FROM app_users WHERE id = :u", u=uid)
    try:
        _ph.verify(r["password_hash"], old)
    except (VerificationError, InvalidHashError):
        raise BizError("Mật khẩu hiện tại không đúng.")
    if len(new) < 10:
        raise BizError("Mật khẩu mới tối thiểu 10 ký tự.")
    if new == old:
        raise BizError("Mật khẩu mới phải khác mật khẩu hiện tại.")
    conn.execute(text("UPDATE app_users SET password_hash = :h, must_change_password = false WHERE id = :u"),
                 {"h": _ph.hash(new), "u": uid})


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise BizError("Mật khẩu tối thiểu 10 ký tự.")
    return _ph.hash(pw)


def visible_owners(conn, uid) -> list[dict]:
    """Chính mình + những người đã chủ động chia sẻ cho mình."""
    me = one(conn, "SELECT id, display_name FROM app_users WHERE id = :u", u=uid)
    out = [{"id": str(me["id"]), "name": me["display_name"], "can_write": True, "self": True}]
    for r in rows(conn, """SELECT u.id, u.display_name, s.can_write FROM data_shares s JOIN app_users u ON u.id = s.owner_id
                           WHERE s.grantee_id = :u ORDER BY u.display_name""", u=uid):
        out.append({"id": str(r["id"]), "name": r["display_name"], "can_write": r["can_write"], "self": False})
    return out


def my_shares(conn, uid):
    return rows(conn, """SELECT s.grantee_id, s.can_write, u.display_name, u.email FROM data_shares s
                         JOIN app_users u ON u.id = s.grantee_id WHERE s.owner_id = :u""", u=uid)


def share_with(conn, uid, email: str, can_write: bool, admin_lookup):
    """admin_lookup(email) -> id: tìm tài khoản đích (chỉ trong whitelist)."""
    gid = admin_lookup(email)
    if not gid or str(gid) == str(uid):
        raise BizError("Không tìm thấy tài khoản được duyệt với email này.")
    conn.execute(text("""INSERT INTO data_shares(owner_id, grantee_id, can_write) VALUES (:o,:g,:w)
                         ON CONFLICT (owner_id, grantee_id) DO UPDATE SET can_write = EXCLUDED.can_write"""),
                 {"o": uid, "g": gid, "w": can_write})


def unshare(conn, uid, grantee_id):
    conn.execute(text("DELETE FROM data_shares WHERE owner_id = :o AND grantee_id = :g"), {"o": uid, "g": grantee_id})


# ---------------- trung tâm / cơ sở ----------------
def centers(conn, owner, include_archived=False):
    return rows(conn, "SELECT * FROM centers WHERE owner_id = :o" + ("" if include_archived else " AND archived_at IS NULL")
                + " ORDER BY archived_at NULLS FIRST, name", o=owner)


def locations(conn, owner, include_archived=False):
    return rows(conn, """SELECT l.*, c.name AS center_name FROM locations l LEFT JOIN centers c ON c.id = l.center_id
                         WHERE l.owner_id = :o""" + ("" if include_archived else " AND l.archived_at IS NULL")
                + " ORDER BY l.archived_at NULLS FIRST, l.kind, l.name", o=owner)


def add_center(conn, owner, name):
    name = clean_ws(name)
    if not name:
        raise BizError("Tên trung tâm không được trống.")
    return scalar(conn, """INSERT INTO centers(owner_id, name) VALUES (:o,:n)
                           ON CONFLICT (owner_id, name) DO UPDATE SET active = true, archived_at = NULL RETURNING id""", o=owner, n=name)


def add_location(conn, owner, name, kind, center_id=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Tên cơ sở/địa điểm không được trống.")
    if kind == "center_branch" and not center_id:
        raise BizError("Cơ sở trung tâm phải thuộc một trung tâm.")
    return scalar(conn, """INSERT INTO locations(owner_id, name, kind, center_id) VALUES (:o,:n,:k,:c)
                           ON CONFLICT (owner_id, name) DO UPDATE SET kind = EXCLUDED.kind, center_id = EXCLUDED.center_id,
                           active = true, archived_at = NULL RETURNING id""", o=owner, n=name, k=kind, c=center_id or None)


def location(conn, loc_id):
    r = one(conn, "SELECT * FROM locations WHERE id = :i", i=loc_id)
    if not r:
        raise BizError("Không tìm thấy cơ sở/địa điểm.")
    r["teaching_type"] = "center" if r["kind"] == "center_branch" else "private"
    return r


# ---------------- học viên ----------------
def add_student(conn, owner, display_name, **kw):
    name = clean_ws(display_name)
    if not name:
        raise BizError("Tên học viên không được trống.")
    return scalar(conn, """INSERT INTO students(owner_id, display_name, search_name, class_group, subject, start_date, status, notes, code)
                           VALUES (:o,:n,:s,:cg,:sub,:sd,:st,:no,:code) RETURNING id""",
                  o=owner, n=name, s=fold(name), cg=kw.get("class_group") or None, sub=kw.get("subject") or None,
                  sd=kw.get("start_date") or None, st=kw.get("status") or "active", no=kw.get("notes") or None,
                  code=clean_ws(kw.get("code") or "") or None)


def update_student(conn, sid, **kw):
    name = clean_ws(kw["display_name"])
    if not name:
        raise BizError("Tên học viên không được trống.")
    if kw.get("status") not in (None, "", "active", "paused", "stopped"):
        raise BizError("Trạng thái học viên không hợp lệ.")
    conn.execute(text("""UPDATE students SET display_name=:n, search_name=:s, class_group=:cg, subject=:sub,
                         start_date=:sd, status=:st, notes=:no, code=:code WHERE id=:i"""),
                 {"n": name, "s": fold(name), "cg": kw.get("class_group") or None, "sub": kw.get("subject") or None,
                  "sd": kw.get("start_date") or None, "st": kw.get("status") or "active", "no": kw.get("notes") or None,
                  "code": clean_ws(kw.get("code") or "") or None, "i": sid})


def archive_student(conn, sid, archived: bool):
    """Lưu trữ mềm: ẩn khỏi danh sách chọn, KHÔNG xóa lịch sử buổi dạy/bảng công."""
    conn.execute(text("UPDATE students SET archived_at = CASE WHEN :a THEN now() END WHERE id = :i"), {"a": archived, "i": sid})


def enroll(conn, owner, student_id, location_id, class_group=None):
    loc = location(conn, location_id)
    conn.execute(text("""INSERT INTO student_enrollments(owner_id, student_id, teaching_type, center_id, location_id, class_group)
                         VALUES (:o,:s,:t,:c,:l,:g)"""),
                 {"o": owner, "s": student_id, "t": loc["teaching_type"], "c": loc["center_id"], "l": location_id, "g": class_group})


def search_students(conn, owner, q="", status=None, location_id=None, class_group=None, teaching_type=None):
    sql = """SELECT s.*, (SELECT string_agg(DISTINCT l.name, ', ') FROM student_enrollments e
                            LEFT JOIN locations l ON l.id = e.location_id WHERE e.student_id = s.id) AS places,
                         (SELECT string_agg(DISTINCT e.teaching_type, ',') FROM student_enrollments e WHERE e.student_id = s.id) AS types
             FROM students s WHERE s.owner_id = :o"""
    p = {"o": owner}
    if status == "archived":
        sql += " AND s.archived_at IS NOT NULL"
        status = None
    elif status != "all":
        sql += " AND s.archived_at IS NULL"
    if status == "all":
        status = None
    if q:
        sql += " AND s.search_name LIKE :q"
        p["q"] = f"%{fold(q)}%"
    if status:
        sql += " AND s.status = :st"
        p["st"] = status
    if class_group:
        sql += " AND s.class_group = :cg"
        p["cg"] = class_group
    if location_id:
        sql += " AND EXISTS (SELECT 1 FROM student_enrollments e WHERE e.student_id = s.id AND e.location_id = :l)"
        p["l"] = location_id
    if teaching_type:
        sql += " AND EXISTS (SELECT 1 FROM student_enrollments e WHERE e.student_id = s.id AND e.teaching_type = :tt)"
        p["tt"] = teaching_type
    return rows(conn, sql + " ORDER BY s.search_name", **p)


def student_stats(conn, sid):
    st = one(conn, "SELECT * FROM students WHERE id = :i", i=sid)
    if not st:
        raise BizError("Không tìm thấy học viên.")
    hist = rows(conn, """SELECT a.id, a.session_date, a.start_time, a.end_time, a.duration_min, a.session_kind, a.status,
                                a.teaching_type, a.location_label, a.payment_status, a.price_amount, a.paid_amount
                         FROM session_attendees sa JOIN actual_sessions a ON a.id = sa.session_id
                         WHERE sa.student_id = :i AND a.status <> 'void' ORDER BY a.session_date DESC, a.start_time DESC""", i=sid)
    planned = rows(conn, """SELECT p.* FROM planned_sessions p
                            WHERE p.expected_students @> CAST(:j AS jsonb) AND p.session_date >= CURRENT_DATE - 60
                            ORDER BY p.session_date, p.start_time""", j=json.dumps([{"student_id": str(sid)}]))
    confirmed = [h for h in hist if h["status"] == "confirmed"]
    return {
        "student": st, "history": hist, "planned": planned,
        "total_sessions": len(confirmed), "total_minutes": sum(h["duration_min"] or 0 for h in confirmed),
        "cancelled": [p for p in planned if p["status"] == "cancelled"],
        "rescheduled": [p for p in planned if p["status"] == "rescheduled"],
        "makeup": [h for h in confirmed if h["session_kind"] == "makeup"],
        "unpaid": sum(((h["price_amount"] or 0) - (h["paid_amount"] or 0)) for h in confirmed
                      if h["teaching_type"] == "private" and h["payment_status"] in ("unpaid", "partial")),
        "enrollments": rows(conn, """SELECT e.*, l.name AS location_name FROM student_enrollments e
                                     LEFT JOIN locations l ON l.id = e.location_id WHERE e.student_id = :i""", i=sid),
    }


# ---------------- cài đặt / quy tắc ----------------
def settings(conn, owner):
    r = one(conn, "SELECT * FROM app_settings WHERE owner_id = :o", o=owner)
    if not r:
        r = {"timesheet_rules": TimesheetRules().to_dict(), "cancel_pay_rules": {}, "flag_meaning": None,
             "flag_meaning_confirmed": False}
    return r


def rule_versions(conn, owner):
    return rows(conn, """SELECT v.*, u.display_name AS creator FROM timesheet_rule_versions v
                         LEFT JOIN app_users u ON u.id = v.created_by WHERE v.owner_id = :o
                         ORDER BY v.valid_from DESC, v.version DESC""", o=owner)


def timesheet_rules(conn, owner, year: int | None = None, month: int | None = None) -> TimesheetRules:
    """Quy tắc có hiệu lực cho tháng (year, month): phiên bản mới nhất có valid_from <= ngày cuối tháng.
    Không truyền tháng -> phiên bản mới nhất."""
    if year and month:
        from ..domain.calendar_month import month_bounds
        end = month_bounds(year, month)[1]
        r = one(conn, """SELECT rules, version, confirmed FROM timesheet_rule_versions WHERE owner_id = :o AND valid_from <= :d
                         ORDER BY valid_from DESC, version DESC LIMIT 1""", o=owner, d=end)
    else:
        r = one(conn, """SELECT rules, version, confirmed FROM timesheet_rule_versions WHERE owner_id = :o
                         ORDER BY valid_from DESC, version DESC LIMIT 1""", o=owner)
    if not r:
        return TimesheetRules.from_dict(settings(conn, owner)["timesheet_rules"])
    t = TimesheetRules.from_dict(r["rules"])
    t.version, t.confirmed = r["version"], r["confirmed"]
    return t


def save_timesheet_rules(conn, owner, rules: TimesheetRules, valid_from=None, note=None, uid=None):
    """Tạo PHIÊN BẢN MỚI (không sửa phiên bản cũ). valid_from = ngày bắt đầu áp dụng (mặc định: mọi tháng chưa chốt).
    Tháng đã chốt luôn hiển thị theo bản chốt, nên đổi quy tắc không làm thay đổi bảng công lịch sử."""
    from datetime import date as _d
    vf = valid_from or _d(2000, 1, 1)
    nv = (scalar(conn, "SELECT max(version) FROM timesheet_rule_versions WHERE owner_id = :o", o=owner) or 0) + 1
    rules.version = nv
    data = json.dumps(rules.to_dict())
    conn.execute(text("""INSERT INTO timesheet_rule_versions(owner_id, version, valid_from, rules, confirmed, confirmed_at, note, created_by)
                         VALUES (:o,:v,:vf,CAST(:r AS jsonb),:c, CASE WHEN :c THEN now() END,:n,:u)"""),
                 {"o": owner, "v": nv, "vf": vf, "r": data, "c": rules.confirmed, "n": note, "u": uid})
    conn.execute(text("""INSERT INTO app_settings(owner_id, timesheet_rules) VALUES (:o, CAST(:r AS jsonb))
                         ON CONFLICT (owner_id) DO UPDATE SET timesheet_rules = EXCLUDED.timesheet_rules, updated_at = now()"""),
                 {"o": owner, "r": data})
    return nv


def confirm_rule_version(conn, owner, version_id, confirmed: bool):
    conn.execute(text("""UPDATE timesheet_rule_versions SET confirmed = :c, confirmed_at = CASE WHEN :c THEN now() END,
                         rules = jsonb_set(rules, '{confirmed}', to_jsonb(CAST(:c AS boolean))) WHERE id = :i AND owner_id = :o"""),
                 {"c": confirmed, "i": version_id, "o": owner})


def save_cancel_rules(conn, owner, rules: dict):
    conn.execute(text("UPDATE app_settings SET cancel_pay_rules = CAST(:r AS jsonb), updated_at = now() WHERE owner_id = :o"),
                 {"o": owner, "r": json.dumps(rules)})


def save_flag_meaning(conn, owner, meaning: str, confirmed: bool):
    conn.execute(text("UPDATE app_settings SET flag_meaning = :m, flag_meaning_confirmed = :c, updated_at = now() WHERE owner_id = :o"),
                 {"o": owner, "m": meaning, "c": confirmed})


def rates(conn, owner):
    return rows(conn, """SELECT r.*, c.name AS center_name, s.display_name AS student_name FROM rate_rules r
                         LEFT JOIN centers c ON c.id = r.center_id LEFT JOIN students s ON s.id = r.student_id
                         WHERE r.owner_id = :o ORDER BY r.archived_at NULLS FIRST, r.teaching_type, r.category, r.valid_from""", o=owner)


def add_rate(conn, owner, teaching_type, category, amount, valid_from, valid_to=None, center_id=None, student_id=None,
             confirmed=False, note=None):
    return scalar(conn, """INSERT INTO rate_rules(owner_id, teaching_type, center_id, student_id, category, amount, valid_from,
                           valid_to, confirmed, confirmed_at, note)
                           VALUES (:o,:t,:c,:s,:cat,:a,:vf,:vt,:cf, CASE WHEN :cf THEN now() END, :n) RETURNING id""",
                  o=owner, t=teaching_type, c=center_id or None, s=student_id or None, cat=category, a=amount,
                  vf=valid_from, vt=valid_to or None, cf=confirmed, n=note)


def confirm_rate(conn, rate_id, confirmed: bool):
    r = one(conn, "SELECT archived_at FROM rate_rules WHERE id = :i", i=rate_id)
    if r and r["archived_at"] and confirmed:
        raise BizError("Đơn giá đã lưu trữ — khôi phục trước khi xác nhận.")
    conn.execute(text("UPDATE rate_rules SET confirmed = :c, confirmed_at = CASE WHEN :c THEN now() END WHERE id = :i"),
                 {"c": confirmed, "i": rate_id})


def close_rate(conn, rate_id, valid_to):
    """Kết thúc hiệu lực một đơn giá (giữ lịch sử). Đổi giá = kết thúc giá cũ + thêm giá mới từ ngày kế tiếp."""
    r = one(conn, "SELECT valid_from FROM rate_rules WHERE id = :i", i=rate_id)
    if not r:
        raise BizError("Không tìm thấy đơn giá.")
    if valid_to is not None and valid_to < r["valid_from"]:
        raise BizError("Ngày kết thúc phải sau ngày bắt đầu hiệu lực.")
    conn.execute(text("UPDATE rate_rules SET valid_to = :v WHERE id = :i"), {"v": valid_to, "i": rate_id})


def archive_rate(conn, rate_id, archived: bool):
    """Lưu trữ đơn giá nhập nhầm: không còn được dùng để tính tiền, vẫn giữ trong nhật ký."""
    conn.execute(text("UPDATE rate_rules SET archived_at = CASE WHEN :a THEN now() END, "
                      "confirmed = CASE WHEN :a THEN false ELSE confirmed END WHERE id = :i"), {"a": archived, "i": rate_id})


def audit_for(conn, table, row_id, limit=50):
    return rows(conn, """SELECT a.*, u.display_name AS actor FROM audit_log a LEFT JOIN app_users u ON u.id = a.actor_id
                         WHERE a.table_name = :t AND a.row_id = :r ORDER BY a.id DESC LIMIT :l""", t=table, r=str(row_id), l=limit)
