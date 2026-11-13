"""Áp dụng migration (dùng kết nối ADMIN) và tạo/cập nhật role anhu_app.

Dùng: ANHU_ADMIN_DATABASE_URL=... ANHU_APP_DB_PASSWORD=... python scripts/migrate.py
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _s in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(ROOT))
from sqlalchemy import text  # noqa: E402

from anhu.db import engine  # noqa: E402


def _raw(conn, sql: str):
    """Chạy cả file SQL qua psycopg không tham số (để '%I' trong format() không bị hiểu là placeholder)."""
    conn.connection.driver_connection.execute(sql)


def migrate(admin_url: str, app_password: str):
    eng = engine(admin_url)
    with eng.begin() as c:
        # Hàm SECURITY DEFINER (đăng nhập, khởi tạo, lời mời…) chạy với quyền của role chạy migration. Bảng dùng FORCE RLS,
        # nên role này PHẢI là superuser hoặc có BYPASSRLS — nếu không, ứng dụng sẽ không đăng nhập được. Kiểm tra trước.
        su, br = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
        if not (su or br):
            raise SystemExit("Role chạy migration không phải superuser và không có BYPASSRLS. Trên Supabase hãy dùng role "
                             "'postgres' (connection string của Dashboard). Xem docs/DEPLOY_STREAMLIT.md, mục Sự cố.")
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"))
        exists = c.execute(text("SELECT 1 FROM pg_roles WHERE rolname='anhu_app'")).first()
        pw = app_password.replace("'", "''")
        if exists:
            c.execute(text(f"ALTER ROLE anhu_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{pw}'"))
        else:
            c.execute(text(f"CREATE ROLE anhu_app LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD '{pw}'"))
        done = {r[0] for r in c.execute(text("SELECT name FROM schema_migrations"))}
    applied = []
    files = sorted((ROOT / "db" / "migrations").glob("*.sql"))
    for f in files:
        if f.name in done or f.name.endswith("_grants.sql"):
            continue
        with eng.begin() as c:  # mỗi file một transaction: lỗi giữa chừng -> không áp dụng gì của file đó
            _raw(c, f.read_text(encoding="utf-8"))
            c.execute(text("INSERT INTO schema_migrations(name) VALUES (:n)"), {"n": f.name})
        applied.append(f.name)
    with eng.begin() as c:  # quyền luôn chạy lại sau cùng cho bảng mới
        for f in files:
            if f.name.endswith("_grants.sql"):
                _raw(c, f.read_text(encoding="utf-8"))
                c.execute(text("INSERT INTO schema_migrations(name) VALUES (:n) ON CONFLICT DO NOTHING"), {"n": f.name})
    return applied


def status(admin_url: str) -> dict:
    """Migration đã áp dụng / còn chờ (dùng trước khi cập nhật phiên bản)."""
    eng = engine(admin_url)
    with eng.begin() as c:
        # Hàm SECURITY DEFINER (đăng nhập, khởi tạo, lời mời…) chạy với quyền của role chạy migration. Bảng dùng FORCE RLS,
        # nên role này PHẢI là superuser hoặc có BYPASSRLS — nếu không, ứng dụng sẽ không đăng nhập được. Kiểm tra trước.
        su, br = c.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).first()
        if not (su or br):
            raise SystemExit("Role chạy migration không phải superuser và không có BYPASSRLS. Trên Supabase hãy dùng role "
                             "'postgres' (connection string của Dashboard). Xem docs/DEPLOY_STREAMLIT.md, mục Sự cố.")
        c.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"))
        done = {r[0]: r[1] for r in c.execute(text("SELECT name, applied_at FROM schema_migrations"))}
    files = [f.name for f in sorted((ROOT / "db" / "migrations").glob("*.sql"))]
    return {"applied": [n for n in files if n in done], "pending": [n for n in files if n not in done]}


if __name__ == "__main__":
    if "--status" in sys.argv:
        print(status(os.environ["ANHU_ADMIN_DATABASE_URL"]))
    else:
        print("Đã áp dụng:", migrate(os.environ["ANHU_ADMIN_DATABASE_URL"], os.environ["ANHU_APP_DB_PASSWORD"]) or "không có migration mới")
