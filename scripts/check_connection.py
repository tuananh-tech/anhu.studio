"""Kiểm tra trước khi triển khai: chuỗi ANHU_DATABASE_URL dùng đúng role ứng dụng, RLS có hiệu lực, CSDL đủ migration.

  ANHU_DATABASE_URL="postgresql://anhu_app.<ref>:<pw>@<pooler-host>:5432/postgres?sslmode=require" python scripts/check_connection.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

from anhu.db import tx  # noqa: E402


def main() -> int:
    url = os.environ.get("ANHU_DATABASE_URL")
    if not url:
        print("Thiếu ANHU_DATABASE_URL")
        return 2
    ok = True
    with tx(url) as c:
        user, su, br = c.exec_driver_sql("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user").first()
        print(f"Role: {user}  superuser={su}  bypassrls={br}")
        if su or br:
            print("SAI: ứng dụng phải kết nối bằng role anhu_app (không superuser, không BYPASSRLS) — nếu không RLS bị vô hiệu.")
            ok = False
        ssl = c.exec_driver_sql("SELECT coalesce((SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()), false)").scalar()
        print(f"SSL: {ssl}")
        done = {r[0] for r in c.exec_driver_sql("SELECT name FROM schema_migrations")}
        want = sorted(p.name for p in (ROOT / "db" / "migrations").glob("*.sql"))
        missing = [m for m in want if m not in done]
        print("Migration:", "đủ" if not missing else f"THIẾU {missing}")
        ok = ok and not missing
        n = c.exec_driver_sql("SELECT count(*) FROM actual_sessions").scalar()
        print(f"Không gắn người dùng -> thấy {n} buổi dạy (phải là 0 nhờ RLS)")
        ok = ok and n == 0
        print("Trạng thái khởi tạo:", c.exec_driver_sql("SELECT app.bootstrap_state()").scalar())
    print("KẾT QUẢ:", "ĐẠT" if ok else "CHƯA ĐẠT")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — thông báo ngắn gọn, không in chuỗi kết nối
        print("KHÔNG KẾT NỐI ĐƯỢC:", type(e).__name__, str(getattr(e, "orig", e)).splitlines()[0][:200] if str(e) else "")
        print("Kiểm tra: host/port của Session pooler, tên đăng nhập dạng anhu_app.<project-ref>, mật khẩu, ?sslmode=require.")
        sys.exit(1)
