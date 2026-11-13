"""Bảng công, tiền công, chốt kỳ, import lịch sử, đối soát trung tâm."""
from __future__ import annotations

import hashlib
import io
import json
from datetime import date, datetime, time
from decimal import Decimal

from ..domain.xlsx_safe import protect_workbook
from openpyxl import Workbook
from sqlalchemy import text

from ..db import one, rows, scalar
from ..domain.calendar_month import month_bounds
from ..domain.excel_export import export_timesheet_xlsx
from ..domain.legacy import plan_import
from ..domain.pay import CATEGORY_BY_COL, Rate, compute_pay
from ..domain.reconcile import CATEGORY_LABEL, compare, parse_center_file
from ..domain.text import clean_ws, fold
from ..domain.timesheet import SessionRecord, build_timesheet
from . import BizError
from .core import add_center, add_location, timesheet_rules


def _export_options(conn, owner):
    from .admin import export_options
    return export_options(conn, owner)


# ---------------- bảng công ----------------
def session_records(conn, owner, year, month, teaching_type=None, center_id=None, include_void=False) -> list[SessionRecord]:
    a, b = month_bounds(year, month)
    sql = """SELECT a.*, t.display_name AS teacher,
             COALESCE((SELECT array_agg(sa.name ORDER BY sa.position) FROM session_attendees sa WHERE sa.session_id = a.id), '{}') AS names
             FROM actual_sessions a LEFT JOIN teacher_profiles t ON t.user_id = a.owner_id
             WHERE a.owner_id = :o AND a.session_date BETWEEN :a AND :b"""
    if not include_void:
        sql += " AND a.status <> 'void'"
    if teaching_type:
        sql += " AND a.teaching_type = :tt"
    if center_id:
        sql += " AND a.center_id = :c"
    out = []
    for r in rows(conn, sql, o=owner, a=a, b=b, tt=teaching_type, c=center_id):
        out.append(SessionRecord(id=str(r["id"]), day=r["session_date"], start=r["start_time"], end=r["end_time"],
                                 location_label=r["location_label"], teaching_type=r["teaching_type"], kind=r["session_kind"],
                                 status=r["status"], attendees=list(r["names"]), notes=r["notes"] or "", teacher=r["teacher"] or "",
                                 center_id=str(r["center_id"]) if r["center_id"] else None, source=r["source"]))
    return out


def rate_objects(conn, owner, teaching_type="center"):
    return [Rate(r["category"], Decimal(r["amount"]), r["valid_from"], r["valid_to"], r["confirmed"],
                 str(r["center_id"]) if r["center_id"] else None)
            for r in rows(conn, "SELECT * FROM rate_rules WHERE owner_id = :o AND teaching_type = :t AND archived_at IS NULL",
                          o=owner, t=teaching_type)]


def timesheet(conn, owner, year, month, teaching_type="center", center_id=None):
    rules = timesheet_rules(conn, owner, year, month)
    recs = session_records(conn, owner, year, month, teaching_type, center_id)
    view = build_timesheet(recs, year, month, rules)
    pay = compute_pay(view, recs, rate_objects(conn, owner, "center")) if teaching_type == "center" else None
    return view, recs, pay


def money_for_export(conn, owner, view, recs, pay):
    """Chỉ ghi hàng tiền khi MỌI ca đều có đơn giá đã xác nhận, và mỗi cột dùng một đơn giá duy nhất."""
    if not pay or not pay.complete:
        return None
    out = {"complete": True}
    rates = rate_objects(conn, owner, "center")
    by_id = {s.id: s for s in recs}
    for col in "EFGHI":
        cat = CATEGORY_BY_COL[col]
        used = set()
        for sid, c in view.placements.items():
            if c == col:
                s = by_id[sid]
                cands = sorted([r for r in rates if r.category == cat and r.applies(s.day, s.center_id)],
                               key=lambda r: (r.center_id is None, -r.valid_from.toordinal()))
                used.add(cands[0].amount)
        if len(used) > 1:
            return None  # đơn giá đổi giữa kỳ -> không ghi công thức đơn giản kiểu mẫu gốc
        if used:
            out[col] = used.pop()
    return out


def export_timesheet(conn, owner, year, month, center_id=None) -> bytes:
    """File mẫu trung tâm: chỉ ca dạy trung tâm, đúng bố cục A:L, đủ ngày."""
    view, recs, pay = timesheet(conn, owner, year, month, "center", center_id)
    meta = {"Kỳ": f"{month:02d}/{year}", "Phạm vi": "Chỉ dạy trung tâm",
            "Quy tắc xếp cột": f"phiên bản {view.rules.version} — {'đã xác nhận' if view.rules.confirmed else 'CHƯA xác nhận'}",
            "Xuất lúc": datetime.now().strftime("%d/%m/%Y %H:%M")}
    return export_timesheet_xlsx(view, money_for_export(conn, owner, view, recs, pay), meta, _export_options(conn, owner))


def dashboard(conn, owner, year, month, today: date):
    a, b = month_bounds(year, month)
    p = one(conn, """SELECT count(*) FILTER (WHERE status <> 'rescheduled') AS planned_total,
                            count(*) FILTER (WHERE status IN ('planned','needs_review') AND session_date <= :t) AS unconfirmed,
                            count(*) FILTER (WHERE status = 'cancelled') AS cancelled
                     FROM planned_sessions WHERE owner_id = :o AND session_date BETWEEN :a AND :b""", o=owner, a=a, b=b, t=today)
    s = one(conn, """SELECT count(*) FILTER (WHERE status = 'confirmed') AS confirmed,
                            COALESCE(sum(duration_min) FILTER (WHERE status = 'confirmed'), 0) AS minutes,
                            count(*) FILTER (WHERE status = 'confirmed' AND (planned_session_id IS NULL OR session_kind IN ('extra','makeup','trial'))) AS extra,
                            count(*) FILTER (WHERE status = 'needs_review') AS review,
                            count(*) FILTER (WHERE status = 'confirmed' AND teaching_type = 'center') AS center_n,
                            count(*) FILTER (WHERE status = 'confirmed' AND teaching_type = 'private') AS private_n,
                            COALESCE(sum(paid_amount) FILTER (WHERE teaching_type = 'private' AND status = 'confirmed'), 0) AS private_paid,
                            COALESCE(sum(price_amount - paid_amount) FILTER (WHERE teaching_type = 'private' AND status = 'confirmed'
                                     AND payment_status IN ('unpaid','partial')), 0) AS private_due,
                            count(*) FILTER (WHERE teaching_type = 'private' AND status = 'confirmed' AND price_amount IS NULL) AS private_unpriced
                     FROM actual_sessions WHERE owner_id = :o AND session_date BETWEEN :a AND :b""", o=owner, a=a, b=b)
    r = scalar(conn, """SELECT count(*) FROM reconciliation_items i JOIN reconciliation_imports m ON m.id = i.import_id
                        WHERE i.owner_id = :o AND i.resolution = 'pending' AND m.year = :y AND m.month = :m""", o=owner, y=year, m=month)
    return {**p, **s, "recon_pending": r}


# ---------------- chốt kỳ ----------------
def period(conn, owner, year, month):
    return one(conn, "SELECT id, owner_id, year, month, status, locked_at, locked_by, export_sha256, version, last_unlock_reason "
                     "FROM timesheet_periods WHERE owner_id = :o AND year = :y AND month = :m", o=owner, y=year, m=month)


def _j(v):
    if isinstance(v, (date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def build_snapshot(view, pay) -> dict:
    """Ảnh chụp đầy đủ để hiển thị lại kỳ đã chốt mà KHÔNG tính lại từ dữ liệu hiện tại
    (đổi tên cơ sở, quy tắc, đơn giá sau này không làm thay đổi bảng công đã chốt)."""
    return {
        "rows": [{"day": r.day.isoformat(), "session_ids": list(r.session_ids), **{k: _j(v) for k, v in r.cells.items()}}
                 for r in view.rows],
        "totals": view.totals, "rules": view.rules.to_dict(),
        "needs_review_count": sum(1 for d in view.detail if d["status"] == "needs_review"),
        "detail": [{k: _j(v) for k, v in d.items()} for d in view.detail],
        "supplemental": [{k: _j(v) for k, v in s.items()} for s in view.supplemental],
        "review": [{k: _j(v) for k, v in s.items()} for s in view.review],
        "pay_total": str(pay.total) if pay and pay.complete else None,
        "pay": ({k: {"count": v["count"], "amount": str(v["amount"]), "label": v["label"]} for k, v in pay.by_category.items()}
                if pay else None),
        "pay_complete": bool(pay and pay.complete),
        "pay_missing": len(pay.missing) if pay else 0,
    }


def snapshot_view(snap: dict):
    """Dựng lại đối tượng giống TimesheetView từ snapshot để template hiển thị y như lúc chốt."""
    from types import SimpleNamespace

    from ..domain.timesheet import TimesheetRules

    def d(v):
        return date.fromisoformat(v) if isinstance(v, str) and len(v) == 10 else v

    def t(v):
        return time.fromisoformat(v) if isinstance(v, str) and v else None

    rows_ = []
    for r in snap.get("rows", []):
        cells = {k: r.get(k) for k in "ABCDEFGHIJKL"}
        cells["A"] = d(cells["A"])
        rows_.append(SimpleNamespace(day=d(r["day"]), cells=cells, session_ids=r.get("session_ids") or ([1] if cells.get("B") else [])))
    detail = [{**x, "day": d(x.get("day")), "start": t(x.get("start")), "end": t(x.get("end"))} for x in snap.get("detail", [])]
    view = SimpleNamespace(rows=rows_, totals=snap.get("totals", {}), rules=TimesheetRules.from_dict(snap.get("rules")),
                           detail=detail, supplemental=[{**x, "day": d(x.get("day"))} for x in snap.get("supplemental", [])],
                           review=[{**x, "day": d(x.get("day"))} for x in snap.get("review", [])], from_snapshot=True)
    pay = None
    if snap.get("pay") is not None:
        pay = SimpleNamespace(by_category={k: {**v, "amount": Decimal(v["amount"])} for k, v in snap["pay"].items()},
                              total=Decimal(snap["pay_total"]) if snap.get("pay_total") else Decimal(0),
                              complete=snap.get("pay_complete", False), missing=[None] * snap.get("pay_missing", 0))
    return view, pay


def locked_snapshot(conn, owner, year, month):
    r = one(conn, "SELECT snapshot, version FROM timesheet_periods WHERE owner_id=:o AND year=:y AND month=:m AND status='locked'",
            o=owner, y=year, m=month)
    return r["snapshot"] if r and r["snapshot"] and r["snapshot"].get("rows") else None


def lock_period(conn, owner, uid, year, month):
    view, recs, pay = timesheet(conn, owner, year, month, "center")
    data = export_timesheet(conn, owner, year, month)
    sha = hashlib.sha256(data).hexdigest()
    snap = build_snapshot(view, pay)
    s = json.dumps(snap, ensure_ascii=False)
    pid = scalar(conn, """INSERT INTO timesheet_periods(owner_id, year, month, status, locked_at, locked_by, snapshot, export_sha256,
                         export_bytes, version)
                         VALUES (:o,:y,:m,'locked',now(),:u,CAST(:s AS jsonb),:h,:b,1)
                         ON CONFLICT (owner_id, year, month) DO UPDATE SET status='locked', locked_at=now(), locked_by=:u,
                         snapshot=EXCLUDED.snapshot, export_sha256=EXCLUDED.export_sha256, export_bytes=EXCLUDED.export_bytes,
                         version=timesheet_periods.version + 1
                         WHERE timesheet_periods.status = 'open'
                         RETURNING id""",
                 o=owner, y=year, m=month, u=uid, s=s, h=sha, b=data)
    if pid is None:
        raise BizError(f"Kỳ {month:02d}/{year} đã chốt rồi.")
    ver = scalar(conn, "SELECT version FROM timesheet_periods WHERE id = :i", i=pid)
    conn.execute(text("""INSERT INTO timesheet_period_versions(owner_id, period_id, version, locked_by, snapshot, export_sha256, export_bytes)
                         VALUES (:o,:p,:v,:u,CAST(:s AS jsonb),:h,:b)"""),
                 {"o": owner, "p": pid, "v": ver, "u": uid, "s": s, "h": sha, "b": data})
    return sha, snap["needs_review_count"]


def unlock_period(conn, owner, year, month, reason: str):
    if not clean_ws(reason):
        raise BizError("Phải nhập lý do mở kỳ.")
    pid = scalar(conn, "UPDATE timesheet_periods SET status='open', last_unlock_reason=:r WHERE owner_id=:o AND year=:y AND month=:m "
                       "AND status='locked' RETURNING id", r=reason, o=owner, y=year, m=month)
    if pid is None:
        raise BizError("Kỳ này chưa chốt.")
    conn.execute(text("""UPDATE timesheet_period_versions SET unlocked_at = now(), unlock_reason = :r
                         WHERE period_id = :p AND unlocked_at IS NULL"""), {"r": reason, "p": pid})


def locked_export(conn, owner, year, month):
    return one(conn, "SELECT export_bytes, export_sha256 FROM timesheet_periods WHERE owner_id=:o AND year=:y AND month=:m AND status='locked'",
               o=owner, y=year, m=month)


# ---------------- import lịch sử ----------------
def legacy_preview(conn, owner, filename: str, data: bytes):
    plan = plan_import(data)
    existing = {r["source_fingerprint"] for r in rows(conn, "SELECT source_fingerprint FROM actual_sessions WHERE owner_id = :o "
                                                            "AND source_fingerprint IS NOT NULL", o=owner)}
    new = [p for p in plan.sessions if p.fingerprint not in existing and p.day]
    stats = {"responses": len(plan.responses), "unique": len(plan.unique), "duplicates": len(plan.duplicates),
             "conflicts": len(plan.conflicts), "sessions": len(plan.sessions), "already_imported": len(plan.sessions) - len(new),
             "new": len(new), "needs_review": sum(1 for p in new if p.issues),
             "timesheet_rows": len(plan.timesheet_rows), "timesheet_unmatched": len(plan.timesheet_unmatched)}
    iid = scalar(conn, """INSERT INTO legacy_imports(owner_id, filename, file_sha256, file_bytes, stats)
                          VALUES (:o,:f,:h,:b,CAST(:s AS jsonb)) RETURNING id""",
                 o=owner, f=filename, h=plan.file_sha256, b=data, s=json.dumps(stats))
    return iid, plan, stats, existing


def legacy_apply(conn, owner, uid, import_id, skip_fingerprints: set | None = None, location_map: dict | None = None,
                 keep: set | None = None) -> dict:
    """Ghi các ca mới. Vân tay unique ở DB -> import lại cùng file không nhân đôi công."""
    imp = one(conn, "SELECT * FROM legacy_imports WHERE id = :i", i=import_id)
    if not imp or imp["status"] != "preview":
        raise BizError("Phiên import không còn ở trạng thái xem trước.")
    plan = plan_import(bytes(imp["file_bytes"]))
    skip = skip_fingerprints or set()
    loc_cache = {}
    inserted = skipped = 0
    for p in plan.sessions:
        if not p.day or p.fingerprint in skip or (keep is not None and p.fingerprint not in keep):
            skipped += 1
            continue
        key = fold(p.location) or "khong ro"
        if key not in loc_cache:
            mapped = (location_map or {}).get(key)
            if mapped:
                loc_cache[key] = one(conn, "SELECT * FROM locations WHERE id = :i", i=mapped)
            else:
                cid = add_center(conn, owner, p.location or "Chưa rõ")
                lid = add_location(conn, owner, p.location or "Chưa rõ", "center_branch", cid)
                loc_cache[key] = one(conn, "SELECT * FROM locations WHERE id = :i", i=lid)
        loc = loc_cache[key]
        review = "; ".join(p.issues) or None
        aid = scalar(conn, """INSERT INTO actual_sessions(owner_id, session_date, start_time, end_time, teaching_type, center_id,
                              location_id, location_label, session_kind, status, review_reason, source, source_fingerprint,
                              legacy_import_id, notes, confirmed_at, confirmed_by)
                              VALUES (:o,:d,:s,:e,'center',:c,:l,:ll,:k,:st,:rr,'import_legacy',:fp,:imp,:n,now(),:u)
                              ON CONFLICT (owner_id, source_fingerprint) WHERE source_fingerprint IS NOT NULL DO NOTHING
                              RETURNING id""",
                     o=owner, d=p.day, s=p.start, e=p.end, c=loc["center_id"], l=loc["id"], ll=loc["name"], k=p.kind,
                     st="needs_review" if p.issues else "confirmed", rr=review, fp=p.fingerprint, imp=import_id,
                     n=f"Import: {p.time_raw} | {p.names_raw}" if p.issues else None, u=uid)
        if aid is None:
            skipped += 1
            continue
        for i, name in enumerate(p.attendees, start=1):
            conn.execute(text("""INSERT INTO session_attendees(owner_id, session_id, position, name, verified)
                                 VALUES (:o,:s,:p,:n,false)"""), {"o": owner, "s": aid, "p": i, "n": name})
        inserted += 1
    conn.execute(text("UPDATE legacy_imports SET status='applied', applied_at=now(), stats = stats || CAST(:s AS jsonb) WHERE id=:i"),
                 {"i": import_id, "s": json.dumps({"inserted": inserted, "skipped": skipped})})
    return {"inserted": inserted, "skipped": skipped}


# ---------------- đối soát ----------------
def recon_upload(conn, owner, center_id, year, month, filename, data: bytes):
    if not data[:2] == b"PK":
        raise BizError("File không phải XLSX hợp lệ.")
    return scalar(conn, """INSERT INTO reconciliation_imports(owner_id, center_id, year, month, filename, file_sha256, file_bytes)
                           VALUES (:o,:c,:y,:m,:f,:h,:b) RETURNING id""",
                  o=owner, c=center_id or None, y=year, m=month, f=filename, h=hashlib.sha256(data).hexdigest(), b=data)


def recon_import(conn, iid):
    r = one(conn, "SELECT * FROM reconciliation_imports WHERE id = :i", i=iid)
    if not r:
        raise BizError("Không tìm thấy phiên đối soát.")
    return r


def last_mapping(conn, owner, center_id):
    r = one(conn, """SELECT mapping FROM reconciliation_imports WHERE owner_id = :o AND mapping IS NOT NULL
                     AND center_id IS NOT DISTINCT FROM CAST(:c AS uuid) ORDER BY created_at DESC LIMIT 1""",
            o=owner, c=str(center_id) if center_id else None)
    return r["mapping"] if r else None


def recon_run(conn, owner, iid, mapping: dict):
    imp = recon_import(conn, iid)
    data = bytes(imp["file_bytes"])
    if hashlib.sha256(data).hexdigest() != imp["file_sha256"]:
        raise BizError("File gốc đã bị thay đổi — dừng đối soát.")
    try:
        their, problems = parse_center_file(data, mapping, imp["year"], imp["month"])
    except (KeyError, ValueError) as e:
        raise BizError(f"Mapping chưa hợp lệ: {e}")
    center_id = str(imp["center_id"]) if imp["center_id"] else None
    view, recs, pay = timesheet(conn, owner, imp["year"], imp["month"], "center", center_id)
    rates_by_col = None
    m = money_for_export(conn, owner, view, recs, pay)
    if m:
        rates_by_col = {k: v for k, v in m.items() if k in "EFGHI"}
    items = compare(view, recs, their, rates_by_col, problems)
    conn.execute(text("DELETE FROM reconciliation_items WHERE import_id = :i AND resolution IN ('pending','auto_match')"), {"i": iid})
    for it in items:
        d = it.to_dict()
        conn.execute(text("""INSERT INTO reconciliation_items(owner_id, import_id, day, category, detail, our, their, amount_diff, resolution)
                             VALUES (:o,:i,:d,:c,:dt,CAST(:our AS jsonb),CAST(:th AS jsonb),:a,:r)"""),
                     {"o": owner, "i": iid, "d": it.day, "c": it.category, "dt": it.detail,
                      "our": json.dumps(d["our"], ensure_ascii=False, default=str),
                      "th": json.dumps(d["their"], ensure_ascii=False, default=str), "a": it.amount_diff,
                      "r": "auto_match" if it.category == "match" else "pending"})
    conn.execute(text("UPDATE reconciliation_imports SET mapping = CAST(:m AS jsonb), status='compared', compared_at=now() WHERE id=:i"),
                 {"m": json.dumps(mapping), "i": iid})
    return items


def recon_items(conn, iid, category=None, resolution=None):
    sql = "SELECT * FROM reconciliation_items WHERE import_id = :i"
    if category:
        sql += " AND category = :c"
    if resolution:
        sql += " AND resolution = :r"
    return rows(conn, sql + " ORDER BY day, category", i=iid, c=category, r=resolution)


def recon_resolve(conn, uid, item_id, resolution, note):
    if resolution not in ("accept_ours", "accept_theirs", "ignored", "pending"):
        raise BizError("Lựa chọn không hợp lệ.")
    if resolution in ("accept_theirs", "ignored") and not clean_ws(note):
        raise BizError("Cần ghi chú lý do khi chấp nhận phía trung tâm hoặc bỏ qua.")
    conn.execute(text("""UPDATE reconciliation_items SET resolution=:r, resolution_note=:n,
                         resolved_at = CASE WHEN :r = 'pending' THEN NULL ELSE now() END,
                         resolved_by = CASE WHEN :r = 'pending' THEN NULL ELSE CAST(:u AS uuid) END WHERE id=:i"""),
                 {"r": resolution, "n": note, "u": str(uid), "i": item_id})


RES_LABEL = {"pending": "Chờ duyệt", "accept_ours": "Giữ số liệu cá nhân", "accept_theirs": "Chấp nhận phía trung tâm",
             "ignored": "Bỏ qua", "auto_match": "Khớp"}


def recon_report_xlsx(conn, iid) -> bytes:
    imp = recon_import(conn, iid)
    wb = Workbook()
    ws = wb.active
    ws.title = "Biên bản đối soát"
    ws.append([f"Biên bản đối soát {imp['month']:02d}/{imp['year']} — file trung tâm: {imp['filename']}"])
    ws.append([f"SHA-256 file gốc: {imp['file_sha256']}"])
    ws.append([])
    ws.append(["Ngày", "Loại chênh lệch", "Chi tiết", "Phía cá nhân", "Phía trung tâm (hàng/giá trị)", "Chênh tiền", "Quyết định", "Ghi chú"])
    for it in recon_items(conn, iid):
        our = it["our"] or {}
        th = it["their"] or {}
        ws.append([it["day"], CATEGORY_LABEL.get(it["category"], it["category"]), it["detail"],
                   f"{our.get('time', '')} {', '.join(our.get('attendees') or [])}".strip() or json.dumps(our.get("counts", ""), ensure_ascii=False),
                   (f"hàng {th.get('row')}: {th.get('time', '')}" if th.get("row") else json.dumps(th.get("counts", ""), ensure_ascii=False)),
                   float(it["amount_diff"]) if it["amount_diff"] is not None else None,
                   RES_LABEL.get(it["resolution"], it["resolution"]), it["resolution_note"]])
        ws.cell(ws.max_row, 1).number_format = "dd/mm/yyyy"
    for col, w in zip("ABCDEFGH", (12, 26, 60, 34, 34, 14, 22, 30)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    protect_workbook(wb)
    wb.save(buf)
    return buf.getvalue()
