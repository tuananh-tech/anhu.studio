"""Quản trị trên web: tài khoản, danh mục (trung tâm/cơ sở/môn/lớp/nhóm), người liên hệ, loại ca & nhãn trạng thái,
tùy chọn xuất Excel, hồ sơ ánh xạ đối soát, nhật ký, kỳ chấm công, thông tin hệ thống.

Nguyên tắc: không xóa cứng dữ liệu gốc (lưu trữ mềm); mọi thay đổi đi qua trigger audit trong DB.
"""
from __future__ import annotations

import json
import secrets
from datetime import date

from sqlalchemy import text

from ..db import one, rows, scalar
from ..domain.text import clean_ws, fold
from ..domain.timesheet import KIND_LABEL, KINDS, TimesheetRules
from . import BizError
from .core import hash_password

APP_VERSION = "2.0.0"


# ======================= tài khoản & quyền =======================
def me(conn, uid):
    return one(conn, """SELECT u.id, u.email, u.display_name, u.is_admin, u.must_change_password, u.last_login_at, u.created_at,
                               t.subject FROM app_users u LEFT JOIN teacher_profiles t ON t.user_id = u.id WHERE u.id = :u""", u=uid)


def update_profile(conn, uid, display_name: str, subject: str | None):
    name = clean_ws(display_name)
    if not name:
        raise BizError("Tên hiển thị không được trống.")
    conn.execute(text("UPDATE app_users SET display_name = :n WHERE id = :u"), {"n": name, "u": uid})
    conn.execute(text("UPDATE teacher_profiles SET display_name = :n, subject = :s WHERE user_id = :u"),
                 {"n": name, "s": clean_ws(subject or "") or None, "u": uid})


def list_users(conn):
    return rows(conn, "SELECT * FROM app.admin_list_users()")


def create_account(conn, email: str, display_name: str, allowed: list[str]) -> str:
    """Quản trị tạo tài khoản giáo viên thứ hai. Trả về mật khẩu tạm (hiện MỘT lần, bắt đổi khi đăng nhập)."""
    email = (email or "").strip().lower()
    if "@" not in email or len(email) < 5:
        raise BizError("Email không hợp lệ.")
    if not clean_ws(display_name):
        raise BizError("Nhập tên giáo viên.")
    if allowed and email not in allowed:
        raise BizError("Email này chưa nằm trong danh sách được phép (ANHU_ALLOWED_EMAILS) của máy chủ. "
                       "Nhờ người quản trị máy chủ bổ sung, hoặc để trống biến này để chỉ dùng giới hạn 2 tài khoản trong DB.")
    temp = secrets.token_urlsafe(9)
    conn.execute(text("SELECT app.admin_create_user(:e, :n, :h, CAST(:r AS jsonb))"),
                 {"e": email, "n": clean_ws(display_name), "h": hash_password(temp), "r": json.dumps(TimesheetRules().to_dict())})
    return temp


def set_account_active(conn, user_id, active: bool):
    conn.execute(text("SELECT app.admin_set_user(:i, :a, NULL)"), {"i": user_id, "a": active})


def reset_password(conn, user_id) -> str:
    temp = secrets.token_urlsafe(9)
    conn.execute(text("SELECT app.admin_set_user(:i, NULL, :h)"), {"i": user_id, "h": hash_password(temp)})
    return temp


# ======================= trung tâm / cơ sở =======================
def update_center(conn, cid, name, contact=None, notes=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Tên trung tâm không được trống.")
    conn.execute(text("UPDATE centers SET name = :n, contact = :c, notes = :no WHERE id = :i"),
                 {"n": name, "c": clean_ws(contact or "") or None, "no": notes or None, "i": cid})


def archive_center(conn, cid, archived: bool):
    conn.execute(text("UPDATE centers SET archived_at = CASE WHEN :a THEN now() END, active = NOT :a WHERE id = :i"),
                 {"a": archived, "i": cid})


def update_location(conn, lid, name, kind, center_id=None, address=None):
    """Đổi tên cơ sở chỉ áp dụng cho ca MỚI. Ca đã ghi giữ nguyên nhãn cơ sở (location_label) như lúc dạy."""
    name = clean_ws(name)
    if not name:
        raise BizError("Tên cơ sở không được trống.")
    if kind not in ("center_branch", "private_place"):
        raise BizError("Loại địa điểm không hợp lệ.")
    if kind == "center_branch" and not center_id:
        raise BizError("Cơ sở trung tâm phải thuộc một trung tâm.")
    conn.execute(text("UPDATE locations SET name = :n, kind = :k, center_id = :c, address = :a WHERE id = :i"),
                 {"n": name, "k": kind, "c": center_id if kind == "center_branch" else None,
                  "a": clean_ws(address or "") or None, "i": lid})


def archive_location(conn, lid, archived: bool):
    conn.execute(text("UPDATE locations SET archived_at = CASE WHEN :a THEN now() END, active = NOT :a WHERE id = :i"),
                 {"a": archived, "i": lid})


# ======================= môn học / lớp / nhóm =======================
def subjects(conn, owner, include_archived=False):
    return rows(conn, "SELECT * FROM subjects WHERE owner_id = :o" + ("" if include_archived else " AND archived_at IS NULL")
                + " ORDER BY archived_at NULLS FIRST, name", o=owner)


def save_subject(conn, owner, name, notes=None, sid=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Tên môn học không được trống.")
    if sid:
        conn.execute(text("UPDATE subjects SET name = :n, notes = :no WHERE id = :i"), {"n": name, "no": notes or None, "i": sid})
        return sid
    return scalar(conn, """INSERT INTO subjects(owner_id, name, notes) VALUES (:o,:n,:no)
                           ON CONFLICT (owner_id, name) DO UPDATE SET archived_at = NULL RETURNING id""", o=owner, n=name, no=notes or None)


def archive_subject(conn, sid, archived: bool):
    conn.execute(text("UPDATE subjects SET archived_at = CASE WHEN :a THEN now() END WHERE id = :i"), {"a": archived, "i": sid})


def groups(conn, owner, include_archived=False):
    return rows(conn, """SELECT g.*, s.name AS subject_name, l.name AS location_name,
                                (SELECT count(*) FROM class_group_members m WHERE m.group_id = g.id AND m.left_on IS NULL) AS members
                         FROM class_groups g LEFT JOIN subjects s ON s.id = g.subject_id LEFT JOIN locations l ON l.id = g.location_id
                         WHERE g.owner_id = :o""" + ("" if include_archived else " AND g.archived_at IS NULL")
                + " ORDER BY g.archived_at NULLS FIRST, g.kind, g.name", o=owner)


def group(conn, gid):
    g = one(conn, """SELECT g.*, s.name AS subject_name, l.name AS location_name FROM class_groups g
                     LEFT JOIN subjects s ON s.id = g.subject_id LEFT JOIN locations l ON l.id = g.location_id WHERE g.id = :i""", i=gid)
    if not g:
        raise BizError("Không tìm thấy lớp/nhóm.")
    g["member_list"] = rows(conn, """SELECT m.*, st.display_name, st.status, st.archived_at FROM class_group_members m
                                    JOIN students st ON st.id = m.student_id WHERE m.group_id = :i
                                    ORDER BY m.left_on NULLS FIRST, st.search_name""", i=gid)
    return g


def save_group(conn, owner, name, kind="class", subject_id=None, location_id=None, notes=None, gid=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Tên lớp/nhóm không được trống.")
    if kind not in ("class", "group"):
        raise BizError("Loại không hợp lệ.")
    p = {"o": owner, "n": name, "k": kind, "s": subject_id or None, "l": location_id or None, "no": notes or None, "i": gid}
    if gid:
        conn.execute(text("UPDATE class_groups SET name=:n, kind=:k, subject_id=:s, location_id=:l, notes=:no WHERE id=:i"), p)
        return gid
    return scalar(conn, """INSERT INTO class_groups(owner_id, name, kind, subject_id, location_id, notes) VALUES (:o,:n,:k,:s,:l,:no)
                           ON CONFLICT (owner_id, name) DO UPDATE SET archived_at = NULL RETURNING id""", **p)


def archive_group(conn, gid, archived: bool):
    conn.execute(text("UPDATE class_groups SET archived_at = CASE WHEN :a THEN now() END WHERE id = :i"), {"a": archived, "i": gid})


def add_member(conn, owner, gid, student_id, joined_on=None):
    if scalar(conn, "SELECT 1 FROM class_group_members WHERE group_id = :g AND student_id = :s AND left_on IS NULL",
              g=gid, s=student_id):
        raise BizError("Học viên đã ở trong lớp/nhóm này.")
    conn.execute(text("INSERT INTO class_group_members(owner_id, group_id, student_id, joined_on) VALUES (:o,:g,:s,:j)"),
                 {"o": owner, "g": gid, "s": student_id, "j": joined_on or date.today()})


def leave_member(conn, member_id, left_on=None):
    conn.execute(text("UPDATE class_group_members SET left_on = :d WHERE id = :i AND left_on IS NULL"),
                 {"d": left_on or date.today(), "i": member_id})


def student_groups(conn, sid):
    return rows(conn, """SELECT m.*, g.name, g.kind FROM class_group_members m JOIN class_groups g ON g.id = m.group_id
                         WHERE m.student_id = :s ORDER BY m.left_on NULLS FIRST, g.name""", s=sid)


# ======================= người liên hệ =======================
def contacts(conn, sid, include_archived=False):
    return rows(conn, "SELECT * FROM contacts WHERE student_id = :s" + ("" if include_archived else " AND archived_at IS NULL")
                + " ORDER BY is_primary DESC, archived_at NULLS FIRST, name", s=sid)


def save_contact(conn, owner, sid, name, relation=None, phone=None, email=None, notes=None, is_primary=False, cid=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Nhập tên người liên hệ.")
    phone = clean_ws(phone or "") or None
    if phone and not all(ch.isdigit() or ch in "+ .-()" for ch in phone):
        raise BizError("Số điện thoại chỉ gồm chữ số và các ký tự + . - ( ).")
    email = (email or "").strip().lower() or None
    if email and "@" not in email:
        raise BizError("Email người liên hệ không hợp lệ.")
    if is_primary:
        conn.execute(text("UPDATE contacts SET is_primary = false WHERE student_id = :s AND is_primary"), {"s": sid})
    p = {"o": owner, "s": sid, "n": name, "r": clean_ws(relation or "") or None, "ph": phone, "e": email,
         "no": notes or None, "pr": bool(is_primary), "i": cid}
    if cid:
        conn.execute(text("UPDATE contacts SET name=:n, relation=:r, phone=:ph, email=:e, notes=:no, is_primary=:pr WHERE id=:i"), p)
        return cid
    return scalar(conn, """INSERT INTO contacts(owner_id, student_id, name, relation, phone, email, notes, is_primary)
                           VALUES (:o,:s,:n,:r,:ph,:e,:no,:pr) RETURNING id""", **p)


def archive_contact(conn, cid, archived: bool):
    conn.execute(text("UPDATE contacts SET archived_at = CASE WHEN :a THEN now() END, is_primary = is_primary AND NOT :a WHERE id = :i"),
                 {"a": archived, "i": cid})


# ======================= loại ca & nhãn trạng thái =======================
def kinds(conn, owner) -> list[dict]:
    """4 loại hệ thống + loại tự thêm. Tên hiển thị/ẩn hiện lấy từ bảng session_kinds nếu có."""
    over = {r["code"]: r for r in rows(conn, "SELECT * FROM session_kinds WHERE owner_id = :o", o=owner)}
    out = []
    for i, k in enumerate(KINDS):
        r = over.get(k, {})
        out.append({"code": k, "label": r.get("label") or KIND_LABEL[k], "active": r.get("active", True),
                    "sort": r.get("sort", i * 10), "builtin": True, "default_label": KIND_LABEL[k]})
    for k, r in over.items():
        if k not in KINDS:
            out.append({"code": k, "label": r["label"], "active": r["active"], "sort": r["sort"], "builtin": False,
                        "default_label": None})
    return sorted(out, key=lambda x: (x["sort"], x["code"]))


def save_kind(conn, owner, code, label, active=True, sort=50):
    label = clean_ws(label)
    if not label:
        raise BizError("Tên loại ca không được trống.")
    if code == "regular" and not active:
        raise BizError("Không thể ẩn loại 'Ca thường'.")
    conn.execute(text("""INSERT INTO session_kinds(owner_id, code, label, active, sort, builtin) VALUES (:o,:c,:l,:a,:s,:b)
                         ON CONFLICT (owner_id, code) DO UPDATE SET label = EXCLUDED.label, active = EXCLUDED.active, sort = EXCLUDED.sort"""),
                 {"o": owner, "c": code, "l": label, "a": active, "s": int(sort), "b": code in KINDS})


def add_custom_kind(conn, owner, label) -> str:
    label = clean_ws(label)
    if not label:
        raise BizError("Nhập tên loại ca.")
    base = "".join(ch if ch.isalnum() else "_" for ch in fold(label)).strip("_")[:20] or "loai"
    if not base[0].isalpha():
        base = "k_" + base
    code = "c_" + base
    n = 1
    existing = {k["code"] for k in kinds(conn, owner)}
    while code in existing:
        n += 1
        code = f"c_{base}_{n}"
    save_kind(conn, owner, code, label, True, 60)
    return code


DEFAULT_STATUS_LABELS = {"planned": "Dự kiến", "pending": "Chờ xác nhận", "confirmed": "Đã dạy", "cancelled": "Nghỉ / hủy",
                         "rescheduled": "Dời lịch", "needs_review": "Cần kiểm tra", "makeup": "Dạy bù", "void": "Đã hủy xác nhận"}
DEFAULT_CANCEL_LABELS = {"teacher_off": "Giáo viên nghỉ", "student_off": "Học viên nghỉ", "center_cancel": "Trung tâm hủy", "other": "Khác"}


def labels(conn, owner) -> dict:
    st = one(conn, "SELECT status_labels FROM app_settings WHERE owner_id = :o", o=owner)
    raw = (st or {}).get("status_labels") or {}
    return {"status": {**DEFAULT_STATUS_LABELS, **(raw.get("status") or {})},
            "cancel": {**DEFAULT_CANCEL_LABELS, **(raw.get("cancel") or {})}}


def save_labels(conn, owner, status: dict, cancel: dict):
    clean = {"status": {k: clean_ws(v) for k, v in status.items() if k in DEFAULT_STATUS_LABELS and clean_ws(v)},
             "cancel": {k: clean_ws(v) for k, v in cancel.items() if k in DEFAULT_CANCEL_LABELS and clean_ws(v)}}
    conn.execute(text("UPDATE app_settings SET status_labels = CAST(:j AS jsonb), updated_at = now() WHERE owner_id = :o"),
                 {"j": json.dumps(clean, ensure_ascii=False), "o": owner})


# ======================= mẫu Excel =======================
DEFAULT_EXPORT = {"sheet_name": "Chấm công", "include_money_row": True, "include_supplement": True, "include_detail": True,
                  "include_info": True, "file_prefix": "ChamCong"}


def export_options(conn, owner) -> dict:
    r = one(conn, "SELECT export_options FROM app_settings WHERE owner_id = :o", o=owner)
    return {**DEFAULT_EXPORT, **((r or {}).get("export_options") or {})}


def save_export_options(conn, owner, opts: dict):
    name = clean_ws(opts.get("sheet_name") or "") or DEFAULT_EXPORT["sheet_name"]
    if len(name) > 31 or any(ch in name for ch in "[]:*?/\\"):
        raise BizError("Tên sheet tối đa 31 ký tự và không chứa [ ] : * ? / \\")
    prefix = "".join(ch for ch in (opts.get("file_prefix") or "ChamCong") if ch.isalnum() or ch in "-_")[:40] or "ChamCong"
    data = {"sheet_name": name, "file_prefix": prefix,
            **{k: bool(opts.get(k)) for k in ("include_money_row", "include_supplement", "include_detail", "include_info")}}
    conn.execute(text("UPDATE app_settings SET export_options = CAST(:j AS jsonb), updated_at = now() WHERE owner_id = :o"),
                 {"j": json.dumps(data, ensure_ascii=False), "o": owner})


# ======================= hồ sơ ánh xạ đối soát =======================
def mappings(conn, owner, include_archived=False):
    return rows(conn, """SELECT m.*, c.name AS center_name FROM recon_mappings m LEFT JOIN centers c ON c.id = m.center_id
                         WHERE m.owner_id = :o""" + ("" if include_archived else " AND m.archived_at IS NULL")
                + " ORDER BY m.archived_at NULLS FIRST, c.name NULLS FIRST, m.name", o=owner)


def validate_mapping(mapping: dict) -> dict:
    from ..domain.reconcile import FIELDS
    cols = {k: v.strip().upper() for k, v in (mapping.get("columns") or {}).items() if k in FIELDS and v and v.strip()}
    for k, v in cols.items():
        if not v.isalpha() or len(v) > 3:
            raise BizError(f"Cột '{v}' của trường {FIELDS[k]} không hợp lệ (dùng chữ cái như A, B, AA).")
    if "date" not in cols:
        raise BizError("Ánh xạ phải có cột Ngày.")
    try:
        start = int(mapping.get("start_row") or 2)
    except ValueError:
        raise BizError("Hàng bắt đầu phải là số.")
    if start < 1:
        raise BizError("Hàng bắt đầu phải ≥ 1.")
    return {"sheet": clean_ws(mapping.get("sheet") or "") or None, "start_row": start, "columns": cols}


def save_mapping(conn, owner, name, center_id, mapping: dict, is_default=False, mid=None):
    name = clean_ws(name)
    if not name:
        raise BizError("Đặt tên cho hồ sơ ánh xạ.")
    m = validate_mapping(mapping)
    if is_default:
        conn.execute(text("UPDATE recon_mappings SET is_default = false WHERE owner_id = :o AND center_id IS NOT DISTINCT FROM CAST(:c AS uuid)"),
                     {"o": owner, "c": center_id or None})
    p = {"o": owner, "n": name, "c": center_id or None, "m": json.dumps(m), "d": bool(is_default), "i": mid}
    if mid:
        conn.execute(text("UPDATE recon_mappings SET name=:n, center_id=:c, mapping=CAST(:m AS jsonb), is_default=:d, updated_at=now() WHERE id=:i"), p)
        return mid
    return scalar(conn, """INSERT INTO recon_mappings(owner_id, name, center_id, mapping, is_default) VALUES (:o,:n,:c,CAST(:m AS jsonb),:d)
                           ON CONFLICT (owner_id, name) DO UPDATE SET center_id = EXCLUDED.center_id, mapping = EXCLUDED.mapping,
                           is_default = EXCLUDED.is_default, archived_at = NULL, updated_at = now() RETURNING id""", **p)


def archive_mapping(conn, mid, archived: bool):
    conn.execute(text("UPDATE recon_mappings SET archived_at = CASE WHEN :a THEN now() END, is_default = is_default AND NOT :a WHERE id = :i"),
                 {"a": archived, "i": mid})


def default_mapping(conn, owner, center_id):
    r = one(conn, """SELECT mapping FROM recon_mappings WHERE owner_id = :o AND archived_at IS NULL
                     AND (center_id IS NOT DISTINCT FROM CAST(:c AS uuid) OR center_id IS NULL)
                     ORDER BY (center_id IS NULL), is_default DESC, updated_at DESC LIMIT 1""",
            o=owner, c=str(center_id) if center_id else None)
    return r["mapping"] if r else None


# ======================= nhật ký =======================
TABLE_LABEL = {"actual_sessions": "Buổi dạy", "session_attendees": "Học viên trong buổi", "planned_sessions": "Ca dự kiến",
               "schedule_templates": "Lịch cố định", "students": "Học viên", "contacts": "Người liên hệ", "centers": "Trung tâm",
               "locations": "Cơ sở / địa điểm", "subjects": "Môn học", "class_groups": "Lớp / nhóm", "class_group_members": "Thành viên lớp",
               "rate_rules": "Đơn giá", "timesheet_rule_versions": "Quy tắc chấm công", "app_settings": "Cài đặt",
               "timesheet_periods": "Kỳ chấm công", "timesheet_period_versions": "Bản chốt", "legacy_imports": "Import lịch sử",
               "reconciliation_imports": "Đối soát", "reconciliation_items": "Mục đối soát", "recon_mappings": "Ánh xạ đối soát",
               "data_shares": "Chia sẻ", "session_kinds": "Loại ca", "backup_runs": "Sao lưu", "app_users": "Tài khoản",
               "teacher_profiles": "Hồ sơ giáo viên", "student_enrollments": "Nơi học"}
ACTION_LABEL = {"INSERT": "Thêm", "UPDATE": "Sửa", "DELETE": "Xóa"}


def audit_search(conn, owner, table=None, action=None, a=None, b=None, q=None, page=1, per=50):
    sql = """SELECT l.id, l.table_name, l.row_id, l.action, l.before, l.after, l.reason, l.at, u.display_name AS actor
             FROM audit_log l LEFT JOIN app_users u ON u.id = l.actor_id WHERE l.owner_id = :o"""
    p = {"o": owner}
    if table:
        sql += " AND l.table_name = :t"
        p["t"] = table
    if action:
        sql += " AND l.action = :ac"
        p["ac"] = action
    if a:
        sql += " AND l.at >= CAST(:a AS date)"
        p["a"] = a
    if b:
        sql += " AND l.at < CAST(:b AS date) + 1"
        p["b"] = b
    if q:
        sql += " AND (l.reason ILIKE :q OR l.after::text ILIKE :q OR l.before::text ILIKE :q)"
        p["q"] = f"%{q}%"
    total = scalar(conn, f"SELECT count(*) FROM ({sql}) x", **p)
    items = rows(conn, sql + " ORDER BY l.id DESC LIMIT :lim OFFSET :off", **p, lim=per, off=(max(page, 1) - 1) * per)
    for it in items:
        it["changes"] = diff(it["before"], it["after"])
    return items, total


_HIDE = {"id", "owner_id", "created_at", "updated_at", "search_name", "snapshot", "file_bytes", "export_bytes"}


def diff(before, after) -> list[tuple]:
    """Các trường thay đổi (trường, trước, sau) — để hiển thị dễ đọc."""
    b, a = before or {}, after or {}
    keys = [k for k in dict.fromkeys(list(a) + list(b)) if k not in _HIDE]
    out = []
    for k in keys:
        if b.get(k) != a.get(k):
            out.append((k, b.get(k), a.get(k)))
    return out[:12]


# ======================= kỳ chấm công =======================
def periods(conn, owner):
    return rows(conn, """SELECT p.id, p.year, p.month, p.status, p.locked_at, p.export_sha256, p.version, p.last_unlock_reason,
                                u.display_name AS locker,
                                (SELECT count(*) FROM timesheet_period_versions v WHERE v.period_id = p.id) AS versions
                         FROM timesheet_periods p LEFT JOIN app_users u ON u.id = p.locked_by
                         WHERE p.owner_id = :o ORDER BY p.year DESC, p.month DESC""", o=owner)


def period_versions(conn, owner, year, month):
    return rows(conn, """SELECT v.id, v.version, v.locked_at, v.export_sha256, v.unlocked_at, v.unlock_reason, u.display_name AS locker,
                                v.snapshot -> 'totals' AS totals, v.snapshot ->> 'pay_total' AS pay_total
                         FROM timesheet_period_versions v JOIN timesheet_periods p ON p.id = v.period_id
                         LEFT JOIN app_users u ON u.id = v.locked_by
                         WHERE p.owner_id = :o AND p.year = :y AND p.month = :m ORDER BY v.version DESC""", o=owner, y=year, m=month)


def period_version_file(conn, vid):
    return one(conn, "SELECT v.version, v.export_bytes, v.export_sha256, p.year, p.month FROM timesheet_period_versions v "
                     "JOIN timesheet_periods p ON p.id = v.period_id WHERE v.id = :i", i=vid)


# ======================= hệ thống =======================
def system_info(conn, owner):
    mig = rows(conn, "SELECT name, applied_at FROM schema_migrations ORDER BY name")
    counts = one(conn, """SELECT (SELECT count(*) FROM students WHERE owner_id = :o AND archived_at IS NULL) AS students,
                                 (SELECT count(*) FROM actual_sessions WHERE owner_id = :o AND status = 'confirmed') AS sessions,
                                 (SELECT count(*) FROM schedule_templates WHERE owner_id = :o
                                    AND (valid_to IS NULL OR valid_to >= CURRENT_DATE)) AS templates,
                                 (SELECT count(*) FROM timesheet_periods WHERE owner_id = :o AND status = 'locked') AS locked,
                                 (SELECT count(*) FROM audit_log WHERE owner_id = :o) AS audit""", o=owner)
    last_backup = one(conn, "SELECT created_at, filename FROM backup_runs WHERE owner_id = :o AND kind = 'export' AND ok "
                            "ORDER BY created_at DESC LIMIT 1", o=owner)
    pg = scalar(conn, "SHOW server_version")
    warnings = []
    if not last_backup:
        warnings.append("Chưa có bản sao lưu nào được tải xuống từ website.")
    elif (date.today() - last_backup["created_at"].date()).days > 30:
        warnings.append("Bản sao lưu gần nhất đã hơn 30 ngày.")
    if not scalar(conn, "SELECT bool_or(confirmed) FROM timesheet_rule_versions WHERE owner_id = :o", o=owner):
        warnings.append("Quy tắc chấm công chưa được xác nhận.")
    if not scalar(conn, "SELECT count(*) FROM rate_rules WHERE owner_id = :o AND confirmed AND archived_at IS NULL", o=owner):
        warnings.append("Chưa có đơn giá nào được xác nhận — tiền công sẽ không hiển thị.")
    return {"version": APP_VERSION, "migrations": mig, "counts": counts, "last_backup": last_backup, "pg": pg, "warnings": warnings}
