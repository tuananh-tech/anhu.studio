"""Học viên: mã tự sinh, nhập/xuất Excel, gắn tên cũ (import lịch sử chỉ có tên) vào hồ sơ."""
from __future__ import annotations

import io
import re
from datetime import date, datetime

from ..domain.xlsx_safe import protect_workbook
from openpyxl import Workbook, load_workbook

from ..db import one, rows, scalar
from ..domain.text import clean_ws, fold
from . import BizError
from .core import add_student

STATUS_VI = {"active": "Đang học", "paused": "Tạm nghỉ", "stopped": "Ngừng học"}
VI_STATUS = {fold(v): k for k, v in STATUS_VI.items()}


def next_code(conn, owner) -> str:
    n = scalar(conn, r"""SELECT COALESCE(max(substring(code from '^HV(\d+)$')::int), 0) FROM students
                         WHERE owner_id = :o AND code ~ '^HV\d+$'""", o=owner)
    return f"HV{(n or 0) + 1:04d}"


def add_with_code(conn, owner, display_name, **kw) -> str:
    """Thêm học viên; để trống mã -> tự sinh HV0001, HV0002…"""
    code = clean_ws(kw.pop("code", "") or "")
    if code and scalar(conn, "SELECT 1 FROM students WHERE owner_id = :o AND lower(code) = lower(:c)", o=owner, c=code):
        raise BizError(f"Mã học viên {code} đã tồn tại.")
    return str(add_student(conn, owner, display_name, code=code or next_code(conn, owner), **kw))


# ---------------- xuất / nhập Excel ----------------
HEADERS = ["Mã", "Tên học viên", "Lớp / nhóm", "Môn", "Ngày bắt đầu", "Trạng thái", "Nơi học", "Liên hệ chính", "SĐT", "Ghi chú"]


def export_xlsx(conn, owner) -> bytes:
    data = rows(conn, """SELECT s.*, (SELECT string_agg(DISTINCT l.name, ', ') FROM student_enrollments e
                                     LEFT JOIN locations l ON l.id = e.location_id WHERE e.student_id = s.id) AS places,
                                (SELECT c.name FROM contacts c WHERE c.student_id = s.id AND c.archived_at IS NULL
                                 ORDER BY c.is_primary DESC, c.created_at LIMIT 1) AS contact,
                                (SELECT c.phone FROM contacts c WHERE c.student_id = s.id AND c.archived_at IS NULL
                                 ORDER BY c.is_primary DESC, c.created_at LIMIT 1) AS phone
                         FROM students s WHERE s.owner_id = :o AND s.archived_at IS NULL ORDER BY s.code NULLS LAST, s.search_name""",
                o=owner)
    wb = Workbook()
    ws = wb.active
    ws.title = "Học viên"
    ws.append(HEADERS)
    for s in data:
        ws.append([s["code"], s["display_name"], s["class_group"], s["subject"], s["start_date"], STATUS_VI.get(s["status"]),
                   s["places"], s["contact"], s["phone"], s["notes"]])
        ws.cell(ws.max_row, 5).number_format = "dd/mm/yyyy"
    for col, w in zip("ABCDEFGHIJ", (9, 28, 16, 16, 13, 12, 24, 22, 14, 30)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    protect_workbook(wb)
    wb.save(buf)
    return buf.getvalue()


def template_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Học viên"
    ws.append(HEADERS[:6] + ["Ghi chú"])
    ws.append(["", "Nguyễn Văn A (ví dụ — xóa dòng này)", "Lớp T7", "Tiếng Anh", "01/09/2026", "Đang học", ""])
    buf = io.BytesIO()
    protect_workbook(wb)
    wb.save(buf)
    return buf.getvalue()


def _cell_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(v).strip(), f).date()
        except ValueError:
            pass
    raise ValueError(f"ngày không đọc được: {v}")


def preview_import(conn, owner, data: bytes, limit=2000) -> list[dict]:
    """Đọc file Excel học viên (hàng 1 là tiêu đề). Không ghi gì. Mỗi dòng có 'status': new | dup_code | dup_name | error."""
    if data[:2] != b"PK":
        raise BizError("File không phải .xlsx hợp lệ.")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        raise BizError("Không đọc được file Excel.")
    ws = wb.worksheets[0]
    it = ws.iter_rows(values_only=True)
    head = [fold(clean_ws(str(h or ""))) for h in next(it, [])]

    def col(*names):
        for n in names:
            if n in head:
                return head.index(n)
        return None
    ci = {"code": col("ma", "ma hoc vien"), "name": col("ten hoc vien", "ten", "ho ten"), "class": col("lop / nhom", "lop", "nhom"),
          "subject": col("mon", "mon hoc"), "start": col("ngay bat dau"), "status": col("trang thai"), "notes": col("ghi chu")}
    if ci["name"] is None:
        raise BizError("Không thấy cột “Tên học viên”. Tải file mẫu để xem đúng tiêu đề.")
    existing = rows(conn, "SELECT lower(code) AS code, search_name FROM students WHERE owner_id = :o", o=owner)
    codes = {r["code"] for r in existing if r["code"]}
    names = {r["search_name"] for r in existing}
    out, seen_codes = [], set()
    for i, r in enumerate(it, start=2):
        if i > limit + 1:
            break

        def g(k):
            j = ci[k]
            return r[j] if j is not None and j < len(r) else None
        name = clean_ws(str(g("name") or ""))
        if not name or "vi du" in fold(name):
            continue
        item = {"row": i, "name": name, "code": clean_ws(str(g("code") or "")) or None, "class_group": clean_ws(str(g("class") or "")) or None,
                "subject": clean_ws(str(g("subject") or "")) or None, "notes": clean_ws(str(g("notes") or "")) or None,
                "status": "new", "problem": ""}
        try:
            item["start_date"] = _cell_date(g("start"))
            st = fold(clean_ws(str(g("status") or "")))
            item["student_status"] = VI_STATUS.get(st, "active") if st else "active"
            if st and st not in VI_STATUS:
                item["problem"] = f"trạng thái “{g('status')}” không rõ → Đang học"
        except ValueError as e:
            item["status"], item["problem"] = "error", str(e)
            out.append(item)
            continue
        if len(name) > 120:
            item["status"], item["problem"] = "error", "tên quá dài"
        elif item["code"] and (item["code"].lower() in codes or item["code"].lower() in seen_codes):
            item["status"], item["problem"] = "dup_code", "mã đã tồn tại"
        elif fold(name) in names:
            item["status"], item["problem"] = "dup_name", "đã có học viên cùng tên (bỏ qua, trừ khi bạn chọn nhập cả tên trùng)"
        if item["code"]:
            seen_codes.add(item["code"].lower())
        out.append(item)
    return out


def apply_import(conn, owner, items: list[dict], include_dup_names=False) -> dict:
    n = skipped = 0
    for it in items:
        if it["status"] == "new" or (include_dup_names and it["status"] == "dup_name"):
            add_with_code(conn, owner, it["name"], code=it.get("code"), class_group=it.get("class_group"),
                          subject=it.get("subject"), start_date=it.get("start_date"), status=it.get("student_status", "active"),
                          notes=it.get("notes"))
            n += 1
        else:
            skipped += 1
    return {"inserted": n, "skipped": skipped}


# ---------------- gắn tên cũ vào hồ sơ ----------------
def unlinked_names(conn, owner) -> list[dict]:
    """Tên trong buổi dạy chưa gắn hồ sơ (thường từ import Excel cũ), kèm gợi ý hồ sơ trùng tên không dấu."""
    data = rows(conn, """SELECT sa.name, count(*) AS n, min(a.session_date) AS first_day, max(a.session_date) AS last_day
                         FROM session_attendees sa JOIN actual_sessions a ON a.id = sa.session_id
                         WHERE sa.owner_id = :o AND sa.student_id IS NULL AND a.status <> 'void'
                         GROUP BY sa.name ORDER BY count(*) DESC, sa.name""", o=owner)
    studs = rows(conn, "SELECT id, display_name, search_name, code FROM students WHERE owner_id = :o AND archived_at IS NULL", o=owner)
    by_fold = {}
    for s in studs:
        by_fold.setdefault(s["search_name"], []).append(s)
    for d in data:
        cands = by_fold.get(fold(d["name"]), [])
        d["suggest"] = str(cands[0]["id"]) if len(cands) == 1 else None
    return data


def link_name(conn, owner, name: str, student_id, create_new=False) -> dict:
    """Gắn mọi lần xuất hiện của tên này (chưa có hồ sơ) với một hồ sơ. Buổi thuộc kỳ ĐÃ CHỐT được giữ nguyên."""
    if create_new:
        student_id = add_with_code(conn, owner, name)
    if not student_id:
        raise BizError("Chọn hồ sơ học viên.")
    if not one(conn, "SELECT id FROM students WHERE id = :i AND owner_id = :o", i=student_id, o=owner):
        raise BizError("Không tìm thấy hồ sơ học viên.")
    r = rows(conn, """UPDATE session_attendees sa SET student_id = :s, verified = true
                      FROM actual_sessions a WHERE a.id = sa.session_id AND sa.owner_id = :o AND sa.student_id IS NULL
                      AND sa.name = :n AND NOT app.is_locked(a.owner_id, a.session_date) RETURNING sa.id""",
             s=student_id, o=owner, n=name)
    locked = scalar(conn, """SELECT count(*) FROM session_attendees sa JOIN actual_sessions a ON a.id = sa.session_id
                             WHERE sa.owner_id = :o AND sa.student_id IS NULL AND sa.name = :n""", o=owner, n=name)
    return {"linked": len(r), "locked_left": locked, "student_id": str(student_id)}


# ---------------- báo cáo theo giáo viên ----------------
def by_teacher(conn, owners: list[dict], a: date, b: date) -> list[dict]:
    """Mỗi giáo viên mà người dùng được xem (chính mình + người đã chia sẻ). RLS quyết định phạm vi."""
    out = []
    for o in owners:
        r = one(conn, """SELECT count(*) FILTER (WHERE status='confirmed') AS sessions,
                                count(*) FILTER (WHERE status='confirmed' AND teaching_type='center') AS center_n,
                                count(*) FILTER (WHERE status='confirmed' AND teaching_type='private') AS private_n,
                                COALESCE(sum(duration_min) FILTER (WHERE status='confirmed'), 0) AS minutes,
                                count(*) FILTER (WHERE status='needs_review') AS review
                         FROM actual_sessions WHERE owner_id = :o AND session_date BETWEEN :a AND :b""", o=o["id"], a=a, b=b)
        students = scalar(conn, """SELECT count(DISTINCT COALESCE(sa.student_id::text, sa.name)) FROM session_attendees sa
                                   JOIN actual_sessions x ON x.id = sa.session_id WHERE x.owner_id = :o AND x.status='confirmed'
                                   AND x.session_date BETWEEN :a AND :b""", o=o["id"], a=a, b=b)
        out.append({"teacher": o["name"], "self": o["self"], **r, "students": students})
    return out


def text_safe(s) -> str:
    return re.sub(r"[\x00-\x1f]", " ", str(s or ""))
