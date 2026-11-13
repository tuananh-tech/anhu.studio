"""Sao lưu / kiểm tra / khôi phục dữ liệu của MỘT tài khoản giáo viên qua website.

Định dạng: file .zip gồm
  manifest.json  — định dạng, phiên bản ứng dụng, migration, số dòng từng bảng, SHA-256 của data.json
  data.json      — {bảng: [dòng JSON...]} lấy bằng to_jsonb() (bytea dạng '\\x..'), kèm nhật ký (audit)

Khôi phục:
  - chỉ vào tài khoản CHƯA có dữ liệu nghiệp vụ (kịch bản chuyển máy chủ / khôi phục sau sự cố trên DB mới);
  - giữ nguyên ID (khóa định danh ổn định), đổi owner_id và các cột người dùng sang tài khoản hiện tại;
  - chạy trong MỘT transaction: lỗi ở bất kỳ bảng nào -> không ghi gì;
  - sau khi ghi, đếm lại từng bảng và so với manifest.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from ..db import one, rows, scalar
from . import BizError

FORMAT = "anhu-backup"
FORMAT_VERSION = 1

# Thứ tự theo khóa ngoại.
TABLES = ["centers", "locations", "subjects", "students", "contacts", "class_groups", "class_group_members",
          "student_enrollments", "session_kinds", "schedule_templates", "planned_sessions", "legacy_imports",
          "actual_sessions", "session_attendees", "rate_rules", "timesheet_rule_versions", "reconciliation_imports",
          "reconciliation_items", "recon_mappings", "timesheet_periods", "timesheet_period_versions"]
SINGLETONS = ["teacher_profiles", "app_settings"]
USER_COLS = ("confirmed_by", "locked_by", "resolved_by", "created_by")
EMPTY_CHECK = ["centers", "locations", "students", "schedule_templates", "planned_sessions", "actual_sessions", "rate_rules",
               "timesheet_periods", "legacy_imports", "reconciliation_imports"]


def _migrations(conn):
    return [r["name"] for r in rows(conn, "SELECT name FROM schema_migrations ORDER BY name")]


def export_backup(conn, owner, uid) -> tuple[bytes, str, dict]:
    from .admin import APP_VERSION
    data = {}
    for t in SINGLETONS + TABLES:
        data[t] = [r["j"] for r in rows(conn, f"SELECT to_jsonb(x) AS j FROM {t} x WHERE x.owner_id = :o", o=owner)]
    data["audit_log"] = [r["j"] for r in rows(conn, """SELECT jsonb_build_object('table_name', table_name, 'row_id', row_id,
                                 'action', action, 'before', before, 'after', after, 'reason', reason, 'at', at) AS j
                                 FROM audit_log WHERE owner_id = :o ORDER BY id""", o=owner)]
    body = json.dumps(data, ensure_ascii=False, default=str, separators=(",", ":")).encode("utf-8")
    who = one(conn, "SELECT email, display_name FROM app_users WHERE id = :o", o=owner) or {}
    manifest = {"format": FORMAT, "format_version": FORMAT_VERSION, "app_version": APP_VERSION,
                "created_at": datetime.now().isoformat(timespec="seconds"), "migrations": _migrations(conn),
                "owner": {"email": who.get("email"), "display_name": who.get("display_name")},
                "counts": {t: len(v) for t, v in data.items()}, "data_sha256": hashlib.sha256(body).hexdigest(),
                "locked_exports": {f"{p['year']}-{p['month']:02d}": p["export_sha256"]
                                   for p in data["timesheet_periods"] if p.get("status") == "locked"}}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        z.writestr("data.json", body)
    out = buf.getvalue()
    sha = hashlib.sha256(out).hexdigest()
    name = f"anhu-saoluu-{datetime.now():%Y%m%d-%H%M}.zip"
    conn.execute(text("""INSERT INTO backup_runs(owner_id, kind, filename, sha256, stats, created_by)
                         VALUES (:o,'export',:f,:h,CAST(:s AS jsonb),:u)"""),
                 {"o": owner, "f": name, "h": sha, "s": json.dumps(manifest["counts"]), "u": uid})
    return out, name, manifest


def read_backup(blob: bytes) -> tuple[dict, dict]:
    """Đọc + kiểm tra toàn vẹn. Lỗi -> BizError với thông điệp dễ hiểu."""
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
        manifest = json.loads(z.read("manifest.json"))
        body = z.read("data.json")
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise BizError("File không phải bản sao lưu ANHU hợp lệ (cần file .zip tải từ Cài đặt → Sao lưu).")
    if manifest.get("format") != FORMAT:
        raise BizError("File không phải bản sao lưu ANHU.")
    if manifest.get("format_version", 0) > FORMAT_VERSION:
        raise BizError("Bản sao lưu được tạo bởi phiên bản mới hơn — cập nhật ứng dụng trước khi khôi phục.")
    if hashlib.sha256(body).hexdigest() != manifest.get("data_sha256"):
        raise BizError("Bản sao lưu bị hỏng hoặc đã bị sửa (mã băm không khớp) — không khôi phục.")
    data = json.loads(body)
    for t, n in (manifest.get("counts") or {}).items():
        if len(data.get(t, [])) != n:
            raise BizError(f"Bản sao lưu thiếu dữ liệu ở bảng {t}.")
    return manifest, data


def verify_backup(conn, owner, uid, blob: bytes, filename: str = "") -> dict:
    manifest, data = read_backup(blob)
    have = set(_migrations(conn))
    newer = [m for m in manifest.get("migrations", []) if m not in have]
    report = {"manifest": manifest, "newer_migrations": newer, "empty_target": is_empty(conn, owner),
              "restorable": not newer}
    conn.execute(text("""INSERT INTO backup_runs(owner_id, kind, filename, sha256, stats, ok, created_by)
                         VALUES (:o,'verify',:f,:h,CAST(:s AS jsonb),:ok,:u)"""),
                 {"o": owner, "f": filename, "h": hashlib.sha256(blob).hexdigest(), "s": json.dumps(manifest.get("counts")),
                  "ok": not newer, "u": uid})
    return report


def is_empty(conn, owner) -> bool:
    return all(not scalar(conn, f"SELECT count(*) FROM {t} WHERE owner_id = :o", o=owner) for t in EMPTY_CHECK)


def _columns(conn, table):
    return [r["column_name"] for r in rows(conn, """SELECT column_name FROM information_schema.columns
                                                    WHERE table_schema = 'public' AND table_name = :t AND is_generated = 'NEVER'
                                                    ORDER BY ordinal_position""", t=table)]


def restore_backup(conn, owner, uid, blob: bytes, filename: str = "") -> dict:
    manifest, data = read_backup(blob)
    have = set(_migrations(conn))
    if any(m not in have for m in manifest.get("migrations", [])):
        raise BizError("Máy chủ này đang ở phiên bản CSDL cũ hơn bản sao lưu — chạy migration trước khi khôi phục.")
    if not is_empty(conn, owner):
        raise BizError("Tài khoản này đã có dữ liệu. Chỉ khôi phục vào tài khoản trống (vd. trên máy chủ mới) "
                       "để không ghi đè hay trộn lẫn dữ liệu hiện có.")

    def remap(r):
        r = dict(r)
        r["owner_id"] = owner
        for c in USER_COLS:
            if r.get(c):
                r[c] = uid
        if "user_id" in r:
            r["user_id"] = owner
        return r

    # cấu hình một dòng: cập nhật dòng sẵn có của tài khoản mới
    for r in data.get("app_settings", [])[:1]:
        conn.execute(text("""UPDATE app_settings s SET timesheet_rules = x.timesheet_rules, cancel_pay_rules = x.cancel_pay_rules,
                             flag_meaning = x.flag_meaning, flag_meaning_confirmed = x.flag_meaning_confirmed,
                             status_labels = COALESCE(x.status_labels, '{}'), export_options = COALESCE(x.export_options, '{}')
                             FROM jsonb_populate_record(NULL::app_settings, CAST(:j AS jsonb)) x WHERE s.owner_id = :o"""),
                     {"j": json.dumps(remap(r), ensure_ascii=False), "o": owner})
    for r in data.get("teacher_profiles", [])[:1]:
        conn.execute(text("UPDATE teacher_profiles SET subject = :s WHERE user_id = :o"), {"s": r.get("subject"), "o": owner})

    restored = {}
    sp = conn.begin_nested()
    try:
        for t in TABLES:
            items = [remap(r) for r in data.get(t, [])]
            if not items:
                restored[t] = 0
                continue
            links = None
            if t == "planned_sessions":
                links = [{"id": r["id"], "r": r.get("rescheduled_from_id"), "m": r.get("makeup_for_id")} for r in items
                         if r.get("rescheduled_from_id") or r.get("makeup_for_id")]
                for r in items:
                    r["rescheduled_from_id"] = r["makeup_for_id"] = None
            cols = [c for c in _columns(conn, t)]
            cl = ", ".join(f'"{c}"' for c in cols)
            sql = f"INSERT INTO {t} ({cl}) SELECT {cl} FROM jsonb_populate_recordset(NULL::{t}, CAST(:j AS jsonb))"
            if t == "timesheet_rule_versions":
                upd = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in cols if c not in ("id", "owner_id", "version"))
                sql += f" ON CONFLICT (owner_id, version) DO UPDATE SET {upd}"
            for i in range(0, len(items), 500):
                conn.execute(text(sql), {"j": json.dumps(items[i:i + 500], ensure_ascii=False, default=str)})
            if links:
                conn.execute(text("""UPDATE planned_sessions p SET rescheduled_from_id = x.r, makeup_for_id = x.m
                                     FROM jsonb_to_recordset(CAST(:j AS jsonb)) AS x(id uuid, r uuid, m uuid) WHERE p.id = x.id"""),
                             {"j": json.dumps(links)})
            restored[t] = len(items)
        audit_n = scalar(conn, "SELECT app.restore_audit(CAST(:j AS jsonb))",
                         j=json.dumps(data.get("audit_log", []), ensure_ascii=False, default=str))
        sp.commit()
    except IntegrityError as e:
        sp.rollback()
        raise BizError("Không khôi phục được: ID trong bản sao lưu đã tồn tại trên máy chủ này (có thể đã khôi phục trước đó, "
                       "hoặc dữ liệu gốc vẫn còn). Khôi phục vào một máy chủ/CSDL mới. Chi tiết: "
                       + str(getattr(e, "orig", e)).split("\n")[0])
    except DBAPIError:
        sp.rollback()
        raise

    # kiểm tra sau khôi phục: đếm lại
    mismatch = {t: (n, scalar(conn, f"SELECT count(*) FROM {t} WHERE owner_id = :o", o=owner))
                for t, n in restored.items()}
    mismatch = {t: v for t, v in mismatch.items() if v[0] != v[1]}
    if mismatch:
        raise BizError(f"Khôi phục không khớp số dòng: {mismatch} — đã hủy, dữ liệu không thay đổi.")
    stats = {"restored": restored, "audit": audit_n, "from": manifest.get("owner"), "created_at": manifest.get("created_at")}
    conn.execute(text("""INSERT INTO backup_runs(owner_id, kind, filename, sha256, stats, created_by)
                         VALUES (:o,'restore',:f,:h,CAST(:s AS jsonb),:u)"""),
                 {"o": owner, "f": filename, "h": hashlib.sha256(blob).hexdigest(),
                  "s": json.dumps(stats, ensure_ascii=False, default=str), "u": uid})
    return stats


def history(conn, owner, limit=20):
    return rows(conn, """SELECT b.*, u.display_name AS actor FROM backup_runs b LEFT JOIN app_users u ON u.id = b.created_by
                         WHERE b.owner_id = :o ORDER BY b.created_at DESC LIMIT :l""", o=owner, l=limit)
