"""Chạy thử cục bộ: Postgres nhúng (private/pgdata) + migration + 2 tài khoản + web.

  python scripts/dev.py                 # chạy web tại http://127.0.0.1:5000
  python scripts/dev.py --demo          # thêm dữ liệu demo ẩn danh (fixture) cho tài khoản 1
  python scripts/dev.py --no-serve      # chỉ chuẩn bị DB (dùng cho kiểm thử/ảnh chụp)

Email lấy từ ANHU_ALLOWED_EMAILS (mặc định 2 email demo). Mật khẩu sinh ngẫu nhiên, in MỘT lần và lưu private/dev_accounts.txt.
KHÔNG dùng cho môi trường thật (xem README: triển khai Supabase).
"""
import os
import secrets
import sys
from datetime import date, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _s in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))
PRIVATE = ROOT / "private"


def main():
    import pgserver
    from anhu.db import scalar, tx
    from anhu.services import ConflictError, core, reports, schedule
    from scripts.migrate import migrate

    PRIVATE.mkdir(exist_ok=True)
    srv = pgserver.get_server(str(PRIVATE / "pgdata"), cleanup_mode=None)
    base = srv.get_uri()
    pw_file = PRIVATE / "dev_app_pw.txt"
    app_pw = pw_file.read_text().strip() if pw_file.exists() else secrets.token_urlsafe(18)
    pw_file.write_text(app_pw)
    admin = base
    migrate(admin, app_pw)
    app_url = f"postgresql://anhu_app:{app_pw}@{base.split('@', 1)[1]}"
    emails = [e.strip().lower() for e in os.environ.get("ANHU_ALLOWED_EMAILS", "giaovien1@example.test,giaovien2@example.test").split(",")]
    acc_file = PRIVATE / "dev_accounts.txt"
    ids = {}
    with tx(admin) as c:
        for i, e in enumerate(emails[:2], 1):
            uid = scalar(c, "SELECT id FROM app_users WHERE email = :e", e=e)
            if not uid:
                pw = secrets.token_urlsafe(12)
                uid = core.create_user(c, e, f"Giáo viên {i}", pw, emails)
                with acc_file.open("a", encoding="utf-8") as f:
                    f.write(f"{e}\t{pw}\n")
                print(f"Tạo tài khoản {e} — mật khẩu: {pw}")
            ids[e] = str(uid)
    print(f"Tài khoản dev lưu ở {acc_file}")

    if "--demo" in sys.argv:
        uid = ids[emails[0]]
        with tx(app_url, uid, "dữ liệu demo") as c:
            if not scalar(c, "SELECT count(*) FROM schedule_templates WHERE owner_id = :o", o=uid):
                data = (ROOT / "tests" / "fixtures" / "anon_source.xlsx").read_bytes()
                iid, *_ = reports.legacy_preview(c, uid, "anon_source.xlsx", data)
                reports.legacy_apply(c, uid, uid, iid)
                lid = scalar(c, "SELECT id FROM locations WHERE owner_id = :o LIMIT 1", o=uid)
                priv = core.add_location(c, uid, "Nhà HV (dạy riêng)", "private_place")
                names = [{"name": n} for n in ("HV15", "HV16", "HV16", "HV06")]
                today = date.today()
                vf = today - timedelta(days=today.weekday() + 14)
                for wd, s, e, ex in [(0, time(16, 45), time(17, 45), [{"name": "HV21"}]), (0, time(17, 45), time(18, 45), names),
                                     (2, time(17, 30), time(18, 30), names), (4, time(13), time(14), [{"name": "HV18"}])]:
                    schedule.create_template(c, uid, weekday=wd, start=s, end=e, valid_from=vf, location_id=lid, expected=ex)
                schedule.create_template(c, uid, weekday=5, start=time(9), end=time(10, 30), valid_from=vf, location_id=priv,
                                         expected=[{"name": "HV40"}])
                schedule.ensure_planned(c, uid, vf, today + timedelta(days=30))
                for p in schedule.planned_between(c, uid, vf, today - timedelta(days=3)):
                    if p["status"] == "planned" and p["session_date"].weekday() != 4:
                        try:
                            schedule.confirm(c, uid, uid, p["id"], p["expected_students"])
                        except ConflictError:
                            pass  # trùng với ca đã import -> để Chờ xác nhận
                print("Đã nạp dữ liệu demo ẩn danh.")
    os.environ["ANHU_DATABASE_URL"] = app_url
    os.environ["ANHU_ALLOWED_EMAILS"] = ",".join(emails[:2])
    os.environ.setdefault("ANHU_COOKIE_SECURE", "0")
    (PRIVATE / "dev_app_url.txt").write_text(app_url)
    if "--no-serve" in sys.argv:
        return
    from anhu.web import create_app
    app = create_app({"DATABASE_URL": app_url, "ALLOWED_EMAILS": emails[:2], "SESSION_COOKIE_SECURE": False})
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)


if __name__ == "__main__":
    main()
