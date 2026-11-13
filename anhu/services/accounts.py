"""Vòng đời tài khoản cho bản phát hành: khởi tạo quản trị một lần, lời mời / mã đặt lại một lần, phiên đăng nhập.

Nguyên tắc:
- Không có tài khoản/mật khẩu mặc định. Quản trị đầu tiên chỉ tạo được khi CSDL chưa có ai VÀ người làm biết
  ANHU_BOOTSTRAP_TOKEN (đặt trong secrets của môi trường triển khai). Sau khi xong, DB khóa bước này vĩnh viễn.
- Mật khẩu: argon2. Mã mời/mã đặt lại: ngẫu nhiên 160 bit, DB chỉ lưu SHA-256, dùng một lần, có hạn.
- Quản trị không bao giờ biết hay xem lại mật khẩu của người khác.
- Phiên: id lưu phía máy chủ (session_state của Streamlit), kiểm tra lại ở DB mỗi lần giao diện chạy lại.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import text

from ..db import one, rows, scalar
from ..domain.text import clean_ws
from ..domain.timesheet import TimesheetRules
from . import BizError
from .core import _DUMMY, _ph, hash_password


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").strip().encode("utf-8")).hexdigest()


def new_token() -> str:
    # 20 byte ngẫu nhiên, nhóm 5 ký tự cho dễ đọc/gõ trên điện thoại
    raw = secrets.token_hex(10).upper()
    return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def _norm_token(t: str) -> str:
    return "".join(ch for ch in (t or "").upper() if ch.isalnum()).strip()


def _fmt_token(t: str) -> str:
    n = _norm_token(t)
    return "-".join(n[i:i + 5] for i in range(0, len(n), 5))


def _valid_email(email: str) -> str:
    e = (email or "").strip().lower()
    if "@" not in e or "." not in e.split("@")[-1] or len(e) > 200 or " " in e:
        raise BizError("Email không hợp lệ.")
    return e


def _rules_json():
    return json.dumps(TimesheetRules().to_dict())


# ---------------- khởi tạo ----------------
def bootstrap_state(conn) -> str:
    return scalar(conn, "SELECT app.bootstrap_state()")


def bootstrap_admin(conn, *, given_token: str, expected_token: str | None, expires: str | None,
                    email: str, name: str, password: str, password2: str) -> str:
    if not expected_token or len(expected_token) < 16:
        raise BizError("Máy chủ chưa cấu hình ANHU_BOOTSTRAP_TOKEN (tối thiểu 16 ký tự) trong secrets — không thể khởi tạo.")
    if expires:
        try:
            exp = datetime.fromisoformat(expires)
            exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        except ValueError:
            raise BizError("ANHU_BOOTSTRAP_EXPIRES không đúng định dạng (VD 2026-12-01T00:00).")
        if datetime.now(timezone.utc) > exp:
            raise BizError("Mã khởi tạo đã hết hạn. Người quản trị máy chủ cần đặt mã mới trong secrets.")
    if not hmac.compare_digest((given_token or "").strip().encode(), expected_token.strip().encode()):
        raise BizError("Mã khởi tạo không đúng.")
    email = _valid_email(email)
    if not clean_ws(name):
        raise BizError("Nhập tên hiển thị.")
    if password != password2:
        raise BizError("Mật khẩu nhập lại không khớp.")
    return str(scalar(conn, "SELECT app.bootstrap_admin(:e, :n, :h, CAST(:r AS jsonb))",
                      e=email, n=clean_ws(name), h=hash_password(password), r=_rules_json()))


# ---------------- đăng nhập / phiên ----------------
def login(conn, email: str, password: str, allowed: list[str], client: str = "") -> dict | None:
    """Trả {'session','id','name','is_admin','must_change'} hoặc None. Sai quá 8 lần / 15 phút -> tạm khóa email."""
    email = (email or "").strip().lower()
    if not scalar(conn, "SELECT app.login_allowed(:e)", e=email):
        raise BizError("Đăng nhập sai quá nhiều lần. Vui lòng đợi 15 phút rồi thử lại.")
    r = one(conn, "SELECT * FROM app.auth_lookup(:e)", e=email)
    try:
        ok = _ph.verify(r["password_hash"] if r else _DUMMY, password or "")
    except Exception:
        ok = False
    ok = bool(ok and r and r["is_active"] and (not allowed or email in allowed))
    conn.execute(text("SELECT app.login_record(:e, :ok)"), {"e": email, "ok": ok})
    if not ok:
        return None
    sid = scalar(conn, "SELECT app.session_start(:u, :c)", u=r["id"], c=client or "")
    return {"session": str(sid), "id": str(r["id"]), "name": r["display_name"], "is_admin": r["is_admin"],
            "must_change": r["must_change_password"]}


def check_session(conn, session_id) -> dict | None:
    if not session_id:
        return None
    try:
        r = one(conn, "SELECT * FROM app.session_check(CAST(:s AS uuid))", s=str(session_id))
    except Exception:
        return None
    if not r:
        return None
    return {"id": str(r["user_id"]), "name": r["display_name"], "is_admin": r["is_admin"], "must_change": r["must_change_password"]}


def logout(conn, session_id):
    if session_id:
        conn.execute(text("SELECT app.session_end(CAST(:s AS uuid))"), {"s": str(session_id)})


def my_sessions(conn):
    return rows(conn, "SELECT * FROM app.my_sessions()")


def revoke_other_sessions(conn, session_id) -> int:
    return scalar(conn, "SELECT app.session_revoke_others(CAST(:s AS uuid))", s=str(session_id))


# ---------------- quản trị ----------------
def create_invite(conn, email: str, name: str, make_admin: bool = False, hours: int = 72) -> str:
    """Trả mã mời (hiện MỘT lần). DB chỉ lưu SHA-256."""
    email = _valid_email(email)
    if not clean_ws(name):
        raise BizError("Nhập tên giáo viên.")
    tok = new_token()
    conn.execute(text("SELECT app.admin_create_invite('invite', :e, :n, :a, NULL, :h, :hr)"),
                 {"e": email, "n": clean_ws(name), "a": bool(make_admin), "h": token_hash(_norm_token(tok)), "hr": int(hours)})
    return tok


def create_reset(conn, user_id, hours: int = 24) -> str:
    tok = new_token()
    conn.execute(text("SELECT app.admin_create_invite('reset', '', '', false, CAST(:u AS uuid), :h, :hr)"),
                 {"u": str(user_id), "h": token_hash(_norm_token(tok)), "hr": int(hours)})
    return tok


def list_invites(conn):
    return rows(conn, "SELECT * FROM app.admin_list_invites()")


def revoke_invite(conn, invite_id):
    conn.execute(text("SELECT app.admin_revoke_invite(CAST(:i AS uuid))"), {"i": str(invite_id)})


def accept_invite(conn, *, email: str, token: str, password: str, password2: str, name: str = "") -> str:
    if password != password2:
        raise BizError("Mật khẩu nhập lại không khớp.")
    h = hash_password(password)
    email = (email or "").strip().lower()
    if not scalar(conn, "SELECT app.login_allowed(:e)", e=email):
        raise BizError("Thử sai quá nhiều lần. Vui lòng đợi 15 phút.")
    try:
        with conn.begin_nested():
            uid = scalar(conn, "SELECT app.accept_invite(:e, :t, :h, :n, CAST(:r AS jsonb))",
                         e=email, t=token_hash(_norm_token(token)), h=h, n=clean_ws(name or ""), r=_rules_json())
    except Exception as e:
        conn.execute(text("SELECT app.login_record(:e, false)"), {"e": email})
        msg = str(getattr(e, "orig", e))
        if "INVITE_INVALID" in msg:
            raise BizError("Mã không đúng, đã dùng, đã bị thu hồi hoặc đã hết hạn. Hãy nhờ quản trị tạo mã mới.")
        if "MAX_USERS" in msg:
            raise BizError("Hệ thống đã đủ 2 tài khoản hoạt động — nhờ quản trị khóa bớt một tài khoản.")
        raise
    return str(uid)


def set_role(conn, user_id, make_admin: bool):
    conn.execute(text("SELECT app.admin_set_role(CAST(:u AS uuid), :a)"), {"u": str(user_id), "a": bool(make_admin)})


def revoke_sessions(conn, user_id) -> int:
    return scalar(conn, "SELECT app.admin_revoke_sessions(CAST(:u AS uuid))", u=str(user_id))


def session_counts(conn) -> dict:
    return {str(r["user_id"]): r for r in rows(conn, "SELECT * FROM app.admin_session_counts()")}


def admin_audit(conn, limit=100):
    return rows(conn, "SELECT * FROM app.admin_audit(:l)", l=limit)


def format_token(t: str) -> str:
    return _fmt_token(t)


# ---------------- ghi nhớ trên thiết bị ----------------
def remember_device(conn, session_id) -> str:
    """Tạo mã ghi nhớ cho phiên vừa đăng nhập. Trả mã (chỉ gửi xuống trình duyệt), DB lưu SHA-256."""
    tok = secrets.token_urlsafe(32)
    conn.execute(text("SELECT app.session_set_resume(CAST(:s AS uuid), :h)"), {"s": str(session_id), "h": token_hash(tok)})
    return tok


def resume(conn, device_token: str) -> str | None:
    """Mã ghi nhớ hợp lệ -> trả id phiên (phiên vẫn chịu mọi giới hạn hết hạn/thu hồi)."""
    if not device_token or len(device_token) > 200:
        return None
    sid = scalar(conn, "SELECT app.session_resume(:h)", h=token_hash(device_token))
    return str(sid) if sid else None
