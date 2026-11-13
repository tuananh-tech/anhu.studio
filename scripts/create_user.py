"""Tạo tài khoản được duyệt (môi trường thật). Mật khẩu nhập từ bàn phím, không lưu ra file.

  ANHU_ADMIN_DATABASE_URL=... ANHU_ALLOWED_EMAILS=a@x,b@y python scripts/create_user.py a@x "Tên hiển thị"
"""
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _s in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
from anhu.db import tx  # noqa: E402
from anhu.services.core import create_user  # noqa: E402

if __name__ == "__main__":
    email, name = sys.argv[1], sys.argv[2]
    allowed = [e.strip().lower() for e in os.environ.get("ANHU_ALLOWED_EMAILS", "").split(",") if e.strip()]
    if not allowed:
        sys.exit("Cần đặt ANHU_ALLOWED_EMAILS (whitelist).")
    pw = getpass.getpass("Mật khẩu (≥ 10 ký tự): ")
    if pw != getpass.getpass("Nhập lại: "):
        sys.exit("Không khớp.")
    with tx(os.environ["ANHU_ADMIN_DATABASE_URL"]) as c:
        print("Đã tạo:", create_user(c, email, name, pw, allowed))
