"""Lịch lặp -> ca dự kiến -> thay đổi -> xác nhận thực tế.

Ca dự kiến (planned_sessions) KHÔNG bao giờ là công. Chỉ actual_sessions status='confirmed' mới vào bảng công.
"""
from __future__ import annotations

import json
from datetime import date, time, timedelta

from sqlalchemy import text

from ..db import one, rows, scalar
from ..domain.names import split_names
from ..domain.recurrence import TemplateVersion, occurrences
from . import BizError, ConflictError
from .core import location

CANCEL_REASONS = {"teacher_off": "Giáo viên nghỉ", "student_off": "Học viên nghỉ", "center_cancel": "Trung tâm hủy", "other": "Khác"}
STATUS_LABEL = {"planned": "Dự kiến", "pending": "Chờ xác nhận", "confirmed": "Đã dạy", "cancelled": "Nghỉ / hủy",
                "rescheduled": "Dời lịch", "needs_review": "Cần kiểm tra", "makeup": "Dạy bù", "void": "Đã hủy xác nhận"}


def display_status(p: dict, today: date) -> str:
    """'Chờ xác nhận' = ca dự kiến đã tới ngày mà chưa xác nhận (không tự đánh dấu đã dạy)."""
    if p["status"] == "planned" and p["session_date"] <= today:
        return "pending"
    return p["status"]


def _students_json(items) -> str:
    return json.dumps([{"student_id": str(i["student_id"]) if i.get("student_id") else None, "name": i["name"]} for i in items],
                      ensure_ascii=False)


def expected_from_form(conn, owner, student_ids: list, extra_names: str) -> list[dict]:
    out = []
    if student_ids:
        found = {str(r["id"]): r for r in rows(conn, "SELECT id, display_name FROM students WHERE owner_id = :o AND id = ANY(CAST(:ids AS uuid[]))",
                                              o=owner, ids=[str(s) for s in student_ids])}
        for sid in student_ids:  # giữ thứ tự & trùng
            if str(sid) in found:
                out.append({"student_id": str(sid), "name": found[str(sid)]["display_name"]})
    out += [{"student_id": None, "name": n} for n in split_names(extra_names)]
    return out


# ---------------- trùng giờ ----------------
def find_conflicts(conn, owner, d: date, start: time, end: time, exclude_planned=None, exclude_actual=None) -> list[str]:
    out = []
    for r in rows(conn, """SELECT id, start_time, end_time, location_id FROM planned_sessions
                           WHERE owner_id = :o AND session_date = :d AND status IN ('planned','needs_review')
                           AND start_time < :e AND :s < end_time AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))""",
                  o=owner, d=d, s=start, e=end, x=str(exclude_planned) if exclude_planned else None):
        out.append(f"ca dự kiến {r['start_time']:%H:%M}-{r['end_time']:%H:%M}")
    for r in rows(conn, """SELECT id, start_time, end_time, planned_session_id FROM actual_sessions
                           WHERE owner_id = :o AND session_date = :d AND status = 'confirmed'
                           AND start_time < :e AND :s < end_time AND (CAST(:x AS uuid) IS NULL OR id <> CAST(:x AS uuid))
                           AND (CAST(:p AS uuid) IS NULL OR planned_session_id IS DISTINCT FROM CAST(:p AS uuid))""",
                  o=owner, d=d, s=start, e=end, x=str(exclude_actual) if exclude_actual else None,
                  p=str(exclude_planned) if exclude_planned else None):
        out.append(f"ca đã dạy {r['start_time']:%H:%M}-{r['end_time']:%H:%M}")
    return out


def _check_conflict(conn, owner, d, s, e, override_reason, **excl):
    c = find_conflicts(conn, owner, d, s, e, **excl)
    if c and not (override_reason or "").strip():
        raise ConflictError(c)
    return (override_reason or "").strip() or None if c else None


def _valid_times(s: time, e: time):
    if s is None or e is None or e <= s:
        raise BizError("Giờ kết thúc phải sau giờ bắt đầu (ca qua nửa đêm chưa được hỗ trợ — tách thành 2 ca).")


# ---------------- lịch lặp ----------------
def template_conflicts(conn, owner, weekday, start, end, valid_from, valid_to, exclude_series=None) -> list[str]:
    return [f"lịch lặp {r['start_time']:%H:%M}-{r['end_time']:%H:%M} từ {r['valid_from']:%d/%m/%Y}"
            for r in rows(conn, """SELECT * FROM schedule_templates WHERE owner_id = :o AND weekday = :w
                                   AND start_time < :e AND :s < end_time
                                   AND (valid_to IS NULL OR valid_to >= :vf) AND (CAST(:vt AS date) IS NULL OR valid_from <= :vt)
                                   AND (CAST(:x AS uuid) IS NULL OR series_id <> CAST(:x AS uuid))""",
                          o=owner, w=weekday, s=start, e=end, vf=valid_from, vt=valid_to,
                          x=str(exclude_series) if exclude_series else None)]


def create_template(conn, owner, *, weekday, start, end, valid_from, valid_to=None, location_id, class_group=None,
                    session_kind="regular", expected=(), notes=None, series_id=None, override_reason=None):
    _valid_times(start, end)
    c = template_conflicts(conn, owner, weekday, start, end, valid_from, valid_to, exclude_series=series_id)
    if c and not (override_reason or "").strip():
        raise ConflictError(c)
    loc = location(conn, location_id)
    return scalar(conn, """INSERT INTO schedule_templates(owner_id, series_id, weekday, start_time, end_time, valid_from, valid_to,
                           teaching_type, center_id, location_id, class_group, session_kind, expected_students, notes)
                           VALUES (:o, COALESCE(CAST(:sid AS uuid), gen_random_uuid()), :w, :s, :e, :vf, :vt, :tt, :c, :l, :cg, :k,
                                   CAST(:ex AS jsonb), :n) RETURNING id""",
                  o=owner, sid=str(series_id) if series_id else None, w=weekday, s=start, e=end, vf=valid_from, vt=valid_to,
                  tt=loc["teaching_type"], c=loc["center_id"], l=location_id, cg=class_group, k=session_kind,
                  ex=_students_json(expected), n=notes)


def templates(conn, owner, on_date: date | None = None):
    sql = """SELECT t.*, l.name AS location_name FROM schedule_templates t LEFT JOIN locations l ON l.id = t.location_id
             WHERE t.owner_id = :o"""
    if on_date:
        sql += " AND t.valid_from <= :d AND (t.valid_to IS NULL OR t.valid_to >= :d)"
    return rows(conn, sql + " ORDER BY t.weekday, t.start_time, t.valid_from", o=owner, d=on_date)


def _clear_future_generated(conn, series_id, from_date):
    """Xóa ca dự kiến CHƯA đụng tới (không sửa riêng, chưa xác nhận) từ ngày hiệu lực để sinh lại."""
    conn.execute(text("""DELETE FROM planned_sessions p WHERE p.series_id = :s AND p.occurrence_date >= :d
                         AND p.status = 'planned' AND NOT p.is_exception
                         AND NOT EXISTS (SELECT 1 FROM actual_sessions a WHERE a.planned_session_id = p.id)
                         AND NOT EXISTS (SELECT 1 FROM planned_sessions q WHERE q.makeup_for_id = p.id OR q.rescheduled_from_id = p.id)"""),
                 {"s": series_id, "d": from_date})


def change_series(conn, owner, template_id, effective_from: date, **fields):
    """Đổi cả chuỗi từ ngày hiệu lực trở đi; quá khứ giữ nguyên (phiên bản cũ đóng lại)."""
    old = one(conn, "SELECT * FROM schedule_templates WHERE id = :i", i=template_id)
    if not old:
        raise BizError("Không tìm thấy lịch lặp.")
    if old["valid_to"] and effective_from > old["valid_to"]:
        raise BizError("Ngày hiệu lực nằm ngoài thời gian của phiên bản lịch này.")
    merged = {"weekday": old["weekday"], "start": old["start_time"], "end": old["end_time"], "location_id": old["location_id"],
              "class_group": old["class_group"], "session_kind": old["session_kind"], "notes": old["notes"],
              "expected": old["expected_students"], "valid_to": old["valid_to"]}
    merged.update({k: v for k, v in fields.items() if v is not None})
    _clear_future_generated(conn, old["series_id"], effective_from)
    if effective_from <= old["valid_from"]:
        # phiên bản cũ chưa có hiệu lực ngày nào trước effective_from -> thay thế tại chỗ
        loc = location(conn, merged["location_id"])
        _valid_times(merged["start"], merged["end"])
        conn.execute(text("""UPDATE schedule_templates SET weekday=:w, start_time=:s, end_time=:e, location_id=:l, teaching_type=:tt,
                             center_id=:c, class_group=:cg, session_kind=:k, notes=:n, expected_students=CAST(:ex AS jsonb),
                             valid_to=:vt WHERE id=:i"""),
                     {"w": merged["weekday"], "s": merged["start"], "e": merged["end"], "l": merged["location_id"],
                      "tt": loc["teaching_type"], "c": loc["center_id"], "cg": merged["class_group"], "k": merged["session_kind"],
                      "n": merged["notes"], "ex": _students_json(merged["expected"]), "vt": merged["valid_to"], "i": template_id})
        return template_id
    conn.execute(text("UPDATE schedule_templates SET valid_to = :d WHERE id = :i"),
                 {"d": effective_from - timedelta(days=1), "i": template_id})
    return create_template(conn, owner, weekday=merged["weekday"], start=merged["start"], end=merged["end"],
                           valid_from=effective_from, valid_to=merged["valid_to"], location_id=merged["location_id"],
                           class_group=merged["class_group"], session_kind=merged["session_kind"], expected=merged["expected"],
                           notes=merged["notes"], series_id=old["series_id"])


def end_series(conn, template_id, last_day: date):
    t = one(conn, "SELECT * FROM schedule_templates WHERE id = :i", i=template_id)
    if not t:
        raise BizError("Không tìm thấy lịch lặp.")
    if last_day < t["valid_from"]:
        raise BizError("Ngày kết thúc phải sau ngày hiệu lực.")
    conn.execute(text("UPDATE schedule_templates SET valid_to = :d WHERE id = :i"), {"d": last_day, "i": template_id})
    _clear_future_generated(conn, t["series_id"], last_day + timedelta(days=1))


def ensure_planned(conn, owner, a: date, b: date) -> int:
    """Sinh (idempotent) ca dự kiến trong [a, b] từ mọi phiên bản lịch lặp có hiệu lực."""
    n = 0
    for t in rows(conn, """SELECT * FROM schedule_templates WHERE owner_id = :o AND valid_from <= :b
                           AND (valid_to IS NULL OR valid_to >= :a)""", o=owner, a=a, b=b):
        tv = TemplateVersion(t["id"], t["series_id"], t["weekday"], t["start_time"], t["end_time"], t["valid_from"], t["valid_to"])
        for d in occurrences(tv, a, b):
            r = conn.execute(text("""INSERT INTO planned_sessions(owner_id, template_id, series_id, occurrence_date, session_date,
                                     start_time, end_time, teaching_type, center_id, location_id, class_group, session_kind,
                                     expected_students, notes)
                                     VALUES (:o,:t,:s,:d,:d,:st,:en,:tt,:c,:l,:cg,:k,CAST(:ex AS jsonb),:n)
                                     ON CONFLICT (series_id, occurrence_date) WHERE series_id IS NOT NULL DO NOTHING"""),
                             {"o": owner, "t": t["id"], "s": t["series_id"], "d": d, "st": t["start_time"], "en": t["end_time"],
                              "tt": t["teaching_type"], "c": t["center_id"], "l": t["location_id"], "cg": t["class_group"],
                              "k": t["session_kind"], "ex": json.dumps(t["expected_students"], ensure_ascii=False), "n": t["notes"]})
            n += r.rowcount
    return n


PLANNED_SELECT = """SELECT p.*, l.name AS location_name, a.id AS actual_id, a.status AS actual_status,
       (SELECT count(*) FROM session_attendees sa WHERE sa.session_id = a.id) AS actual_n
FROM planned_sessions p LEFT JOIN locations l ON l.id = p.location_id
LEFT JOIN actual_sessions a ON a.planned_session_id = p.id AND a.status <> 'void'"""


def planned_between(conn, owner, a: date, b: date, teaching_type=None):
    sql = PLANNED_SELECT + " WHERE p.owner_id = :o AND p.session_date BETWEEN :a AND :b"
    if teaching_type:
        sql += " AND p.teaching_type = :tt"
    return rows(conn, sql + " ORDER BY p.session_date, p.start_time", o=owner, a=a, b=b, tt=teaching_type)


def planned(conn, pid):
    r = one(conn, PLANNED_SELECT + " WHERE p.id = :i", i=pid)
    if not r:
        raise BizError("Không tìm thấy ca.")
    return r


# ---------------- thao tác trên ca dự kiến ----------------
def _require_open(p):
    if p["status"] not in ("planned", "needs_review"):
        raise BizError(f"Ca đang ở trạng thái '{STATUS_LABEL[p['status']]}', không thể thao tác này.")


def add_one_off(conn, owner, *, day, start, end, location_id, session_kind="regular", expected=(), notes=None,
                override_reason=None, makeup_for_id=None, rescheduled_from_id=None, class_group=None):
    """Ca ngoài lịch (không sửa lịch lặp). Vẫn phải xác nhận mới thành công."""
    _valid_times(start, end)
    loc = location(conn, location_id)
    ov = _check_conflict(conn, owner, day, start, end, override_reason,
                         exclude_planned=rescheduled_from_id)
    return scalar(conn, """INSERT INTO planned_sessions(owner_id, session_date, start_time, end_time, teaching_type, center_id,
                           location_id, class_group, session_kind, expected_students, notes, conflict_override_reason,
                           makeup_for_id, rescheduled_from_id, is_exception)
                           VALUES (:o,:d,:s,:e,:tt,:c,:l,:cg,:k,CAST(:ex AS jsonb),:n,:ov,:mk,:rs,true) RETURNING id""",
                  o=owner, d=day, s=start, e=end, tt=loc["teaching_type"], c=loc["center_id"], l=location_id, cg=class_group,
                  k=session_kind, ex=_students_json(expected), n=notes, ov=ov, mk=makeup_for_id, rs=rescheduled_from_id)


def edit_single(conn, owner, pid, *, start=None, end=None, location_id=None, expected=None, notes=None, override_reason=None,
                session_kind=None):
    """Sửa riêng một ca (không ảnh hưởng tuần trước/sau)."""
    p = planned(conn, pid)
    _require_open(p)
    s, e = start or p["start_time"], end or p["end_time"]
    _valid_times(s, e)
    ov = _check_conflict(conn, owner, p["session_date"], s, e, override_reason, exclude_planned=pid)
    loc = location(conn, location_id or p["location_id"])
    conn.execute(text("""UPDATE planned_sessions SET start_time=:s, end_time=:e, location_id=:l, teaching_type=:tt, center_id=:c,
                         expected_students=COALESCE(CAST(:ex AS jsonb), expected_students), notes=COALESCE(:n, notes),
                         session_kind=COALESCE(:k, session_kind), conflict_override_reason=COALESCE(:ov, conflict_override_reason),
                         is_exception=true WHERE id=:i"""),
                 {"s": s, "e": e, "l": loc["id"], "tt": loc["teaching_type"], "c": loc["center_id"],
                  "ex": _students_json(expected) if expected is not None else None, "n": notes, "k": session_kind, "ov": ov, "i": pid})


def cancel(conn, pid, reason: str, note: str | None = None):
    if reason not in CANCEL_REASONS:
        raise BizError("Chọn lý do nghỉ/hủy.")
    p = planned(conn, pid)
    _require_open(p)
    conn.execute(text("UPDATE planned_sessions SET status='cancelled', cancel_reason=:r, cancel_note=:n, is_exception=true WHERE id=:i"),
                 {"r": reason, "n": note, "i": pid})


def restore(conn, pid):
    p = planned(conn, pid)
    if p["status"] != "cancelled":
        raise BizError("Chỉ khôi phục được ca đã nghỉ/hủy.")
    if scalar(conn, "SELECT count(*) FROM planned_sessions WHERE makeup_for_id = :i", i=pid):
        raise BizError("Ca này đã có ca dạy bù — không thể khôi phục (tránh tính công hai lần).")
    conn.execute(text("UPDATE planned_sessions SET status='planned', cancel_reason=NULL, cancel_note=NULL WHERE id=:i"), {"i": pid})


def reschedule(conn, owner, pid, *, day, start, end, location_id=None, override_reason=None):
    """Dời lịch: ca gốc -> 'Dời lịch', tạo ca mới liên kết rescheduled_from_id."""
    p = planned(conn, pid)
    _require_open(p)
    new_id = add_one_off(conn, owner, day=day, start=start, end=end, location_id=location_id or p["location_id"],
                         session_kind=p["session_kind"], expected=p["expected_students"], notes=p["notes"],
                         override_reason=override_reason, rescheduled_from_id=pid, class_group=p["class_group"])
    conn.execute(text("UPDATE planned_sessions SET status='rescheduled', is_exception=true WHERE id=:i"), {"i": pid})
    return new_id


def makeup(conn, owner, cancelled_pid, *, day, start, end, location_id=None, override_reason=None):
    """Dạy bù cho ca đã nghỉ/hủy — tối đa một ca bù cho mỗi ca hủy (ràng buộc unique ở DB)."""
    p = planned(conn, cancelled_pid)
    if p["status"] != "cancelled":
        raise BizError("Chỉ tạo dạy bù cho ca đã nghỉ/hủy.")
    if scalar(conn, "SELECT count(*) FROM planned_sessions WHERE makeup_for_id = :i", i=cancelled_pid):
        raise BizError("Ca này đã có ca dạy bù.")
    return add_one_off(conn, owner, day=day, start=start, end=end, location_id=location_id or p["location_id"],
                       session_kind="makeup", expected=p["expected_students"], notes=f"Dạy bù cho ca {p['session_date']:%d/%m}",
                       override_reason=override_reason, makeup_for_id=cancelled_pid, class_group=p["class_group"])


def copy_week(conn, owner, week_start: date) -> int:
    """Sao chép các ca NGOÀI LỊCH LẶP của tuần sang tuần sau (ca theo lịch lặp tự sinh sẵn)."""
    n = 0
    for p in rows(conn, """SELECT * FROM planned_sessions WHERE owner_id = :o AND series_id IS NULL
                           AND session_date BETWEEN :a AND :b AND status IN ('planned','confirmed')
                           AND rescheduled_from_id IS NULL AND makeup_for_id IS NULL""",
                  o=owner, a=week_start, b=week_start + timedelta(days=6)):
        d = p["session_date"] + timedelta(days=7)
        if find_conflicts(conn, owner, d, p["start_time"], p["end_time"]):
            continue
        add_one_off(conn, owner, day=d, start=p["start_time"], end=p["end_time"], location_id=p["location_id"],
                    session_kind=p["session_kind"], expected=p["expected_students"], notes=p["notes"], class_group=p["class_group"])
        n += 1
    return n


# ---------------- xác nhận thực tế ----------------
def _attendee_rows(conn, owner, attendees: list[dict]):
    out = []
    for a in attendees:
        name = (a.get("name") or "").strip()
        if not name:
            continue
        out.append({"student_id": a.get("student_id") or None, "name": name, "verified": bool(a.get("student_id"))})
    return out


def _write_attendees(conn, owner, session_id, attendees):
    conn.execute(text("DELETE FROM session_attendees WHERE session_id = :s"), {"s": session_id})
    for i, a in enumerate(attendees, start=1):
        conn.execute(text("""INSERT INTO session_attendees(owner_id, session_id, position, student_id, name, verified)
                             VALUES (:o,:s,:p,:st,:n,:v)"""),
                     {"o": owner, "s": session_id, "p": i, "st": a["student_id"], "n": a["name"], "v": a["verified"]})


def _private_price(conn, owner, d: date, attendees) -> tuple:
    """Giá buổi dạy riêng = tổng giá/HV đã XÁC NHẬN; thiếu giá của bất kỳ HV nào -> None (không tự suy ra)."""
    total = 0
    for a in attendees:
        r = one(conn, """SELECT amount FROM rate_rules WHERE owner_id = :o AND teaching_type = 'private'
                         AND category = 'private_session' AND confirmed AND valid_from <= :d AND (valid_to IS NULL OR valid_to >= :d)
                         AND (student_id = CAST(:s AS uuid) OR student_id IS NULL)
                         ORDER BY (student_id IS NULL), valid_from DESC LIMIT 1""", o=owner, d=d, s=a["student_id"])
        if not r:
            return None, "na"
        total += r["amount"]
    return (total, "unpaid") if attendees else (None, "na")


def confirm(conn, owner, uid, pid, attendees: list[dict], *, start=None, end=None, session_kind=None, notes=None,
            override_reason=None):
    p = planned(conn, pid)
    _require_open(p)
    s, e = start or p["start_time"], end or p["end_time"]
    _valid_times(s, e)
    ov = _check_conflict(conn, owner, p["session_date"], s, e, override_reason or p["conflict_override_reason"],
                         exclude_planned=pid)
    att = _attendee_rows(conn, owner, attendees)
    status = "confirmed" if att else "needs_review"
    loc = location(conn, p["location_id"])
    price, pay = _private_price(conn, owner, p["session_date"], att) if p["teaching_type"] == "private" else (None, "na")
    aid = scalar(conn, """INSERT INTO actual_sessions(owner_id, planned_session_id, session_date, start_time, end_time, teaching_type,
                          center_id, location_id, location_label, class_group, session_kind, status, review_reason, source,
                          makeup_for_planned_id, notes, price_amount, payment_status, conflict_override_reason, confirmed_at, confirmed_by)
                          VALUES (:o,:p,:d,:s,:e,:tt,:c,:l,:ll,:cg,:k,:st,:rr,'schedule',:mk,:n,:pr,:ps,:ov,now(),:u) RETURNING id""",
                 o=owner, p=pid, d=p["session_date"], s=s, e=e, tt=p["teaching_type"], c=p["center_id"], l=p["location_id"],
                 ll=loc["name"], cg=p["class_group"], k=session_kind or p["session_kind"], st=status,
                 rr=None if att else "Chưa có học viên hiện diện — nếu cả lớp vắng hãy đánh dấu Nghỉ/hủy",
                 mk=p["makeup_for_id"], n=notes if notes is not None else p["notes"], pr=price, ps=pay, ov=ov, u=uid)
    _write_attendees(conn, owner, aid, att)
    conn.execute(text("UPDATE planned_sessions SET status = :st WHERE id = :i"),
                 {"st": "confirmed" if status == "confirmed" else "needs_review", "i": pid})
    return aid


def confirm_as_planned(conn, owner, uid, pids: list) -> list:
    """Xác nhận nhiều ca theo kế hoạch (sau khi người dùng xem trước và chọn từng ca)."""
    return [confirm(conn, owner, uid, pid, planned(conn, pid)["expected_students"]) for pid in pids]


def add_and_confirm_extra(conn, owner, uid, *, day, start, end, location_id, session_kind, attendees, notes=None, override_reason=None):
    pid = add_one_off(conn, owner, day=day, start=start, end=end, location_id=location_id, session_kind=session_kind,
                      expected=attendees, notes=notes, override_reason=override_reason)
    return confirm(conn, owner, uid, pid, attendees, override_reason=override_reason)


ACTUAL_SELECT = """SELECT a.*, l.name AS location_name, c.name AS center_name,
  COALESCE((SELECT json_agg(json_build_object('position', sa.position, 'name', sa.name, 'student_id', sa.student_id,
                     'verified', sa.verified) ORDER BY sa.position) FROM session_attendees sa WHERE sa.session_id = a.id), '[]') AS attendees
FROM actual_sessions a LEFT JOIN locations l ON l.id = a.location_id LEFT JOIN centers c ON c.id = a.center_id"""


def actual(conn, aid):
    r = one(conn, ACTUAL_SELECT + " WHERE a.id = :i", i=aid)
    if not r:
        raise BizError("Không tìm thấy buổi dạy.")
    return r


def update_actual(conn, owner, aid, *, attendees: list[dict], start, end, session_kind, notes, status=None,
                  override_reason=None, payment_status=None, paid_amount=None):
    a = actual(conn, aid)
    _valid_times(start, end)
    ov = _check_conflict(conn, owner, a["session_date"], start, end, override_reason or a["conflict_override_reason"],
                         exclude_actual=aid, exclude_planned=a["planned_session_id"])
    att = _attendee_rows(conn, owner, attendees)
    new_status = status or ("confirmed" if att else "needs_review")
    if new_status == "confirmed" and not att:
        new_status = "needs_review"
    conn.execute(text("""UPDATE actual_sessions SET start_time=:s, end_time=:e, session_kind=:k, notes=:n, status=:st,
                         review_reason = CASE WHEN :st = 'needs_review' THEN COALESCE(review_reason, 'Cần kiểm tra') ELSE NULL END,
                         conflict_override_reason=COALESCE(:ov, conflict_override_reason),
                         payment_status=COALESCE(:ps, payment_status), paid_amount=COALESCE(:pa, paid_amount) WHERE id=:i"""),
                 {"s": start, "e": end, "k": session_kind, "n": notes, "st": new_status, "ov": ov, "ps": payment_status,
                  "pa": paid_amount, "i": aid})
    _write_attendees(conn, owner, aid, att)
    if a["planned_session_id"]:
        conn.execute(text("UPDATE planned_sessions SET status=:st WHERE id=:i"),
                     {"st": "confirmed" if new_status == "confirmed" else "needs_review", "i": a["planned_session_id"]})


def unconfirm(conn, aid):
    """Hủy xác nhận: buổi dạy -> void (giữ lịch sử), ca dự kiến quay lại 'Dự kiến'."""
    a = actual(conn, aid)
    conn.execute(text("UPDATE actual_sessions SET status='void', planned_session_id=NULL, makeup_for_planned_id=NULL WHERE id=:i"),
                 {"i": aid})
    if a["planned_session_id"]:
        conn.execute(text("UPDATE planned_sessions SET status='planned' WHERE id=:i"), {"i": a["planned_session_id"]})


def search_actual(conn, owner, *, a: date, b: date, teaching_type=None, location_id=None, center_id=None, kind=None,
                  status=None, payment=None, student_id=None, q=None, class_group=None, recon=None):
    """Bộ lọc kết hợp cho danh sách buổi dạy. Chỉ là chế độ xem — không ảnh hưởng file xuất mẫu."""
    from ..domain.text import fold
    sql = ACTUAL_SELECT + " WHERE a.owner_id = :o AND a.session_date BETWEEN :a AND :b"
    p = {"o": owner, "a": a, "b": b}
    for col, key, val in (("a.teaching_type", "tt", teaching_type), ("a.location_id", "l", location_id),
                          ("a.center_id", "c", center_id), ("a.session_kind", "k", kind), ("a.status", "st", status),
                          ("a.payment_status", "ps", payment), ("a.class_group", "cg", class_group)):
        if val:
            sql += f" AND {col} = :{key}"
            p[key] = val
    if not status:
        sql += " AND a.status <> 'void'"
    if student_id:
        sql += " AND EXISTS (SELECT 1 FROM session_attendees sa WHERE sa.session_id = a.id AND sa.student_id = :sid)"
        p["sid"] = student_id
    if recon == "pending":
        sql += """ AND EXISTS (SELECT 1 FROM reconciliation_items i WHERE i.owner_id = a.owner_id AND i.day = a.session_date
                   AND i.resolution = 'pending')"""
    elif recon == "clean":
        sql += """ AND NOT EXISTS (SELECT 1 FROM reconciliation_items i WHERE i.owner_id = a.owner_id AND i.day = a.session_date
                   AND i.resolution = 'pending')"""
    out = rows(conn, sql + " ORDER BY a.session_date DESC, a.start_time DESC", **p)
    if q:
        fq = fold(q)
        out = [r for r in out if any(fq in fold(x["name"]) for x in r["attendees"]) or fq in fold(r["location_label"])
               or fq in fold(r["notes"] or "")]
    return out
