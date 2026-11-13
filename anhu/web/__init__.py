"""Ứng dụng web (Flask, render phía server, JS tối thiểu)."""
from __future__ import annotations

import secrets
import time as _time
from collections import defaultdict
from datetime import date, datetime, time
from functools import wraps
from zoneinfo import ZoneInfo

from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from ..config import Config
from ..db import tx
from ..services import BizError, ConflictError, db_error_message
from ..services import core

VN = ZoneInfo("Asia/Ho_Chi_Minh")
_login_attempts: dict = defaultdict(list)


def today() -> date:
    return datetime.now(VN).date()


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    if not app.config["DATABASE_URL"]:
        raise RuntimeError("Thiếu ANHU_DATABASE_URL")

    from . import filters
    filters.register(app)

    @app.before_request
    def _guards():
        g.uid = session.get("uid")
        if request.method == "POST":
            tok = request.form.get("_csrf") or request.headers.get("X-CSRF")
            if not tok or not secrets.compare_digest(tok, session.get("csrf", "")):
                abort(400, "CSRF token không hợp lệ — tải lại trang.")
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        # Mật khẩu tạm do quản trị cấp: bắt đổi trước khi dùng.
        if g.uid and session.get("must_change") and request.endpoint not in (
                "settings_section", "settings_action", "logout", "static", "health"):
            flash("Bạn đang dùng mật khẩu tạm — vui lòng đổi mật khẩu trước khi tiếp tục.", "warn")
            return redirect(url_for("settings_section", sec="tai-khoan") + "#mat-khau")

    @app.after_request
    def _headers(resp):
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                                   "script-src 'self'; frame-ancestors 'none'; form-action 'self'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Frame-Options"] = "DENY"
        if g.get("uid"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(400)
    def _bad(e):
        return render_template("error.html", message=getattr(e, "description", "Yêu cầu không hợp lệ")), 400

    @app.errorhandler(404)
    def _nf(e):
        return render_template("error.html", message="Không tìm thấy trang."), 404

    @app.errorhandler(413)
    def _big(e):
        return render_template("error.html", message="File quá lớn (tối đa 25 MB)."), 413

    @app.errorhandler(OperationalError)
    @app.errorhandler(InterfaceError)
    def _db_down(e):
        app.logger.error("Lỗi kết nối CSDL: %s", str(getattr(e, "orig", e)).split("\n")[0])
        return render_template("error.html", title="Không kết nối được cơ sở dữ liệu",
                               message="Máy chủ dữ liệu đang không phản hồi. Dữ liệu của bạn không bị mất. "
                                       "Hãy thử lại sau ít phút; nếu vẫn lỗi, liên hệ người quản trị (xem Hướng dẫn quản trị, mục Sự cố).",
                               retry=True), 503

    @app.errorhandler(500)
    def _err(e):
        return render_template("error.html", title="Đã xảy ra lỗi",
                               message="Thao tác chưa hoàn tất và không có dữ liệu nào bị ghi dở. Thử lại; nếu lỗi lặp lại, "
                                       "ghi lại thời điểm và thao tác để báo người quản trị.", retry=True), 500

    @app.get("/suc-khoe")
    def health():
        """Kiểm tra sức khỏe cho người quản trị / giám sát: chỉ trả trạng thái, không lộ dữ liệu."""
        from ..db import scalar
        try:
            with tx(app.config["DATABASE_URL"]) as c:
                n = scalar(c, "SELECT count(*) FROM schema_migrations")
            return jsonify(status="ok", db="ok", migrations=n)
        except (OperationalError, InterfaceError, DBAPIError):
            return jsonify(status="error", db="unreachable"), 503

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            ip = request.remote_addr or "?"
            now = _time.time()
            _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < 900]
            if len(_login_attempts[ip]) >= 8:
                flash("Quá nhiều lần thử. Vui lòng đợi 15 phút.", "error")
                return render_template("login.html"), 429
            with tx(app.config["DATABASE_URL"]) as c:
                u = core.authenticate(c, request.form.get("email", ""), request.form.get("password", ""),
                                      app.config["ALLOWED_EMAILS"])
            if not u:
                _login_attempts[ip].append(now)
                flash("Email hoặc mật khẩu không đúng, hoặc tài khoản chưa được duyệt.", "error")
                return render_template("login.html"), 401
            session.clear()
            session.permanent = True
            session["uid"], session["name"] = u["id"], u["name"]
            session["is_admin"], session["must_change"] = bool(u.get("is_admin")), bool(u.get("must_change"))
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(request.args.get("next") if _safe_next(request.args.get("next")) else url_for("dashboard"))
        return render_template("login.html")

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    from . import pages
    pages.register(app)
    return app


def _safe_next(n):
    return bool(n) and n.startswith("/") and not n.startswith("//")


# ---------------- tiện ích cho routes ----------------
def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not g.get("uid"):
            return redirect(url_for("login", next=request.full_path if request.method == "GET" else None))
        return fn(*a, **kw)
    return w


def db(reason: str | None = None):
    from flask import current_app
    return tx(current_app.config["DATABASE_URL"], g.uid, reason)


def owner_ctx(conn):
    """Giáo viên đang xem: chính mình hoặc người đã chia sẻ (tham số ?gv=)."""
    from ..services import admin
    owners = core.visible_owners(conn, g.uid)
    want = request.args.get("gv") or session.get("gv") or g.uid
    cur = next((o for o in owners if o["id"] == want), owners[0])
    session["gv"] = cur["id"]
    g.owners, g.owner = owners, cur
    ks = admin.kinds(conn, cur["id"])
    g.kind_list = ks
    g.kind_label = {k["code"]: k["label"] for k in ks}
    g.kinds_active = [k["code"] for k in ks if k["active"]]
    lb = admin.labels(conn, cur["id"])
    g.status_label, g.cancel_label = lb["status"], lb["cancel"]
    return cur["id"]


def handle_errors(back_endpoint=None):
    """Bắt lỗi nghiệp vụ / lỗi trigger DB -> flash và quay lại."""
    def deco(fn):
        @wraps(fn)
        def w(*a, **kw):
            try:
                return fn(*a, **kw)
            except ConflictError as e:
                flash(str(e), "warn")
            except BizError as e:
                flash(str(e), "error")
            except DBAPIError as e:
                if isinstance(e, (OperationalError, InterfaceError)):
                    raise
                msg = db_error_message(e)
                if not msg:
                    raise
                flash(msg, "error")
            return redirect(request.referrer or url_for(back_endpoint or "dashboard"))
        return w
    return deco


def parse_date(v, default=None) -> date | None:
    if not v:
        return default
    for f in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(v.strip(), f).date()
        except ValueError:
            pass
    raise BizError(f"Ngày không hợp lệ: {v}")


def parse_time(v) -> time | None:
    if not v:
        return None
    from ..domain.timeparse import parse_hhmm
    t = parse_hhmm(v)
    if t is None:
        raise BizError(f"Giờ không hợp lệ: {v}")
    return t


def ym_from_args(default: date) -> tuple[int, int]:
    v = request.args.get("thang")  # yyyy-mm
    if v:
        try:
            y, m = map(int, v.split("-"))
            if 1 <= m <= 12 and 2000 <= y <= 2100:
                return y, m
        except ValueError:
            pass
    return default.year, default.month


def attendees_from_form(conn, owner, prefix="") -> list[dict]:
    """Danh sách thực tế = các HV được tick (theo ID, giữ trùng/thứ tự) + tên nhập thêm. Không có ô nhập số lượng."""
    from ..domain.names import split_names
    out = []
    for raw in request.form.getlist(prefix + "present"):
        sid, _, name = raw.partition("|")
        out.append({"student_id": sid or None, "name": name})
    if out and any(o["student_id"] for o in out):
        ids = [o["student_id"] for o in out if o["student_id"]]
        from ..db import rows
        ok = {str(r["id"]) for r in rows(conn, "SELECT id FROM students WHERE owner_id = :o AND id = ANY(CAST(:i AS uuid[]))", o=owner, i=ids)}
        for o in out:
            if o["student_id"] and o["student_id"] not in ok:
                o["student_id"] = None
    out += [{"student_id": None, "name": n} for n in split_names(request.form.get(prefix + "extra_names"))]
    return out
