import os
import secrets


class Config:
    # Kết nối admin (chỉ dùng cho migration/tạo tài khoản) và kết nối ứng dụng (role anhu_app, chịu RLS).
    ADMIN_DATABASE_URL = os.environ.get("ANHU_ADMIN_DATABASE_URL", "")
    DATABASE_URL = os.environ.get("ANHU_DATABASE_URL", "")
    SECRET_KEY = os.environ.get("ANHU_SECRET_KEY") or secrets.token_hex(32)
    # Whitelist: chỉ các email này được đăng nhập (tối đa 2), phân tách bằng dấu phẩy.
    ALLOWED_EMAILS = [e.strip().lower() for e in os.environ.get("ANHU_ALLOWED_EMAILS", "").split(",") if e.strip()]
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024   # bản sao lưu; file XLSX tự giới hạn 10 MB
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("ANHU_COOKIE_SECURE", "1") == "1"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 12
    TIMEZONE = "Asia/Ho_Chi_Minh"
