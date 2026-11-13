"""Tài khoản bản phát hành: khởi tạo quản trị một lần, lời mời, mã đặt lại, phiên, chống dò mật khẩu.
Chạy bằng role ứng dụng thật (RLS bật), trên CSDL mới hoàn toàn."""
import threading
from datetime import date, time

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from anhu.db import scalar, tx
from anhu.services import BizError, accounts, core, schedule, students_io

TOKEN = "khoi-tao-bi-mat-0123456789"
PW = "mat-khau-rat-dai-123"


def _boot(db, email="admin@example.test", token=TOKEN, expected=TOKEN, expires=None):
    with tx(db["app"], None, "khởi tạo") as c:
        return accounts.bootstrap_admin(c, given_token=token, expected_token=expected, expires=expires, email=email,
                                        name="Quản trị", password=PW, password2=PW)


def _login(db, email, pw=PW):
    with tx(db["app"]) as c:
        return accounts.login(c, email, pw, [], "test")


@pytest.fixture(scope="module")
def db(make_db):
    return make_db()


def test_bootstrap_requires_secret_and_runs_only_once(db):
    with tx(db["app"]) as c:
        assert accounts.bootstrap_state(c) == "open"
    with pytest.raises(BizError, match="chưa cấu hình"):
        _boot(db, expected=None)
    with pytest.raises(BizError, match="chưa cấu hình"):
        _boot(db, expected="ngan")
    with pytest.raises(BizError, match="không đúng"):
        _boot(db, token="sai-ma-khoi-tao-123456")
    with pytest.raises(BizError, match="hết hạn"):
        _boot(db, expires="2020-01-01T00:00")
    uid = _boot(db)
    with tx(db["app"]) as c:
        assert accounts.bootstrap_state(c) == "done"
    with pytest.raises(DBAPIError, match="BOOTSTRAP_DONE"):   # lần 2 bị chặn ở DB dù biết mã
        _boot(db, email="ke-gian@example.test")
    s = _login(db, "admin@example.test")
    assert s["id"] == uid and s["is_admin"] is True
    with tx(db["app"]) as c:   # role ứng dụng không tự gọi được hàm tạo tài khoản nội bộ
        with pytest.raises(ProgrammingError, match="permission denied"):
            c.execute(text("SELECT app._new_account('x@x.vn','x','h',true,'{}')"))


def test_concurrent_bootstrap_creates_exactly_one_admin(make_db):
    d = make_db()
    results = []

    def run(i):
        try:
            results.append(("ok", _boot(d, email=f"a{i}@example.test")))
        except Exception as e:  # noqa: BLE001
            results.append(("err", str(e)))
    th = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    [t.start() for t in th]
    [t.join() for t in th]
    assert [r[0] for r in results].count("ok") == 1, results
    with tx(d["admin"]) as c:
        assert scalar(c, "SELECT count(*) FROM app_users") == 1


def test_invite_flow_one_time_code_and_roles(db):
    admin = _login(db, "admin@example.test")
    with tx(db["app"], admin["id"], "mời") as c:
        code = accounts.create_invite(c, "gv2@example.test", "Giáo viên 2", False, 72)
        assert len(code.replace("-", "")) == 20
        # DB chỉ lưu mã băm, không lưu mã
        assert all(code.replace("-", "") not in str(r) for r in accounts.list_invites(c))
    with tx(db["app"]) as c:   # mã sai / email sai -> từ chối
        with pytest.raises(BizError, match="Mã không đúng"):
            accounts.accept_invite(c, email="gv2@example.test", token="AAAAA-BBBBB-CCCCC-DDDDD", password=PW, password2=PW)
    with tx(db["app"]) as c:
        with pytest.raises(BizError, match="Mã không đúng"):
            accounts.accept_invite(c, email="khac@example.test", token=code, password=PW, password2=PW)
    with tx(db["app"]) as c:
        with pytest.raises(BizError, match="tối thiểu 10"):
            accounts.accept_invite(c, email="gv2@example.test", token=code, password="ngan", password2="ngan")
    with tx(db["app"]) as c:
        accounts.accept_invite(c, email="GV2@example.test", token=code.lower(), password=PW, password2=PW)
    with tx(db["app"]) as c:   # mã dùng một lần
        with pytest.raises(BizError, match="đã dùng"):
            accounts.accept_invite(c, email="gv2@example.test", token=code, password=PW, password2=PW)
    t = _login(db, "gv2@example.test")
    assert t and t["is_admin"] is False and t["must_change"] is False
    with tx(db["app"], t["id"]) as c:   # giáo viên thường không làm được việc quản trị
        for fn in (lambda: accounts.create_invite(c, "x@example.test", "X"), lambda: accounts.list_invites(c),
                   lambda: accounts.set_role(c, t["id"], True), lambda: accounts.admin_audit(c)):
            with pytest.raises(DBAPIError, match="ADMIN_ONLY"):
                with c.begin_nested():
                    fn()
    with tx(db["app"], admin["id"]) as c:   # đủ 2 tài khoản -> không mời thêm
        with pytest.raises(DBAPIError, match="MAX_USERS"):
            accounts.create_invite(c, "gv3@example.test", "GV3")
    with tx(db["app"], admin["id"], "đổi vai trò") as c:
        with pytest.raises(DBAPIError, match="ít nhất một quản trị"):
            with c.begin_nested():
                accounts.set_role(c, admin["id"], False)
        log = accounts.admin_audit(c)
        assert any("lời mời" in (r["reason"] or "") for r in log) and all("password_hash" not in str(r) for r in log)


def test_admin_cannot_read_teacher_data(db):
    admin = _login(db, "admin@example.test")
    t = _login(db, "gv2@example.test")
    with tx(db["app"], t["id"], "dữ liệu") as c:
        cid = core.add_center(c, t["id"], "TT riêng")
        lid = core.add_location(c, t["id"], "CS riêng", "center_branch", cid)
        students_io.add_with_code(c, t["id"], "Bé Bí Mật")
        schedule.add_and_confirm_extra(c, t["id"], t["id"], day=date(2026, 11, 2), start=time(9), end=time(10), location_id=lid,
                                       session_kind="regular", attendees=[{"name": "Bé Bí Mật"}])
    with tx(db["app"], admin["id"]) as c:
        for tbl in ("students", "actual_sessions", "session_attendees", "centers"):
            assert scalar(c, f"SELECT count(*) FROM {tbl} WHERE owner_id = :o", o=t["id"]) == 0, tbl
    for tbl in ("account_invites", "app_sessions", "login_attempts", "app_meta"):   # bảng tài khoản: không truy cập trực tiếp
        with tx(db["app"], admin["id"]) as c:
            with pytest.raises(ProgrammingError, match="permission denied"):
                c.execute(text(f"SELECT count(*) FROM {tbl}"))


def test_sessions_revoke_and_lock_and_reset_code(db):
    admin = _login(db, "admin@example.test")
    t1 = _login(db, "gv2@example.test")
    t2 = _login(db, "gv2@example.test")
    with tx(db["app"]) as c:
        assert accounts.check_session(c, t1["session"])["id"] == t1["id"]
        assert accounts.check_session(c, "00000000-0000-0000-0000-000000000000") is None
        assert accounts.check_session(c, "khong-phai-uuid") is None
    with tx(db["app"], t1["id"]) as c:     # tự đăng xuất thiết bị khác
        assert accounts.revoke_other_sessions(c, t1["session"]) >= 1
    with tx(db["app"]) as c:
        assert accounts.check_session(c, t2["session"]) is None and accounts.check_session(c, t1["session"])
    with tx(db["app"], admin["id"], "thu hồi") as c:
        assert accounts.revoke_sessions(c, t1["id"]) >= 1
    with tx(db["app"]) as c:
        assert accounts.check_session(c, t1["session"]) is None
    with tx(db["app"], admin["id"], "đặt lại") as c:
        code = accounts.create_reset(c, t1["id"], 24)
    with tx(db["app"]) as c:
        accounts.accept_invite(c, email="gv2@example.test", token=code, password="mat-khau-moi-456", password2="mat-khau-moi-456")
    assert _login(db, "gv2@example.test") is None                         # mật khẩu cũ hết hiệu lực
    t3 = _login(db, "gv2@example.test", "mat-khau-moi-456")
    with tx(db["app"], admin["id"], "khóa") as c:
        core_admin = __import__("anhu.services.admin", fromlist=["x"])
        core_admin.set_account_active(c, t3["id"], False)
    with tx(db["app"]) as c:
        assert accounts.check_session(c, t3["session"]) is None           # khóa tài khoản -> mọi phiên hết hiệu lực
    assert _login(db, "gv2@example.test", "mat-khau-moi-456") is None
    with tx(db["app"], admin["id"], "mở khóa") as c:
        core_admin.set_account_active(c, t3["id"], True)
    with tx(db["app"]) as c:
        accounts.logout(c, admin["session"])
        assert accounts.check_session(c, admin["session"]) is None


def test_login_throttle_is_stored_in_database(db):
    for _ in range(8):
        assert _login(db, "admin@example.test", "sai-mat-khau-xx") is None
    with pytest.raises(BizError, match="quá nhiều lần"):
        _login(db, "admin@example.test")                                 # kể cả mật khẩu đúng
    with tx(db["admin"]) as c:   # dọn để test khác không bị ảnh hưởng (mô phỏng 15 phút trôi qua)
        c.execute(text("DELETE FROM login_attempts"))
    assert _login(db, "admin@example.test")


def test_student_codes_import_export_and_legacy_name_linking(db):
    from openpyxl import Workbook
    import io
    t = _login(db, "gv2@example.test", "mat-khau-moi-456")
    o = t["id"]
    with tx(db["app"], o, "hv") as c:
        a = students_io.add_with_code(c, o, "Bé Một")
        b = students_io.add_with_code(c, o, "Bé Hai", code="")
        codes = [scalar(c, "SELECT code FROM students WHERE id = :i", i=x) for x in (a, b)]
        assert codes[1] == f"HV{int(codes[0][2:]) + 1:04d}"
        with pytest.raises(BizError, match="đã tồn tại"):
            students_io.add_with_code(c, o, "Trùng mã", code=codes[0].lower())
    wb = Workbook()
    ws = wb.active
    ws.append(["Mã", "Tên học viên", "Lớp / nhóm", "Môn", "Ngày bắt đầu", "Trạng thái", "Ghi chú"])
    ws.append(["", "Bé Ba", "Lớp A", "Toán", "05/09/2026", "Đang học", ""])
    ws.append([codes[0], "Bé Bốn", "", "", "", "", ""])
    ws.append(["", "Bé Một", "", "", "", "", ""])
    ws.append(["", "Bé Năm", "", "", "32/13/2026", "", ""])
    ws.append(["", "=CMD|' /C calc'!A0", "", "", "", "Tạm nghỉ", ""])          # chữ giống công thức
    ws.cell(ws.max_row, 2).data_type = "s"
    ws.append(["", "=HYPERLINK(\"http://x\")", "", "", "", "", ""])         # ô CÔNG THỨC thật -> không được thực thi/nhập
    buf = io.BytesIO()
    wb.save(buf)
    with tx(db["app"], o, "nhập") as c:
        items = students_io.preview_import(c, o, buf.getvalue())
        st = {i["name"]: i["status"] for i in items}
        assert st == {"Bé Ba": "new", "Bé Bốn": "dup_code", "Bé Một": "dup_name", "Bé Năm": "error", "=CMD|' /C calc'!A0": "new"}
        res = students_io.apply_import(c, o, items)
        assert res == {"inserted": 2, "skipped": 3}
        with pytest.raises(BizError):
            students_io.preview_import(c, o, b"<html>not excel</html>")
        out = students_io.export_xlsx(c, o)
    from openpyxl import load_workbook
    ws = load_workbook(io.BytesIO(out)).active
    cells = {r[1].value: r[1] for r in ws.iter_rows(min_row=2)}
    assert "Bé Ba" in cells and cells["=CMD|' /C calc'!A0"].data_type == "s"   # xuất ra là CHỮ, không phải công thức
    # gắn tên cũ (buổi import chỉ có tên) -> hồ sơ; buổi trong kỳ đã chốt giữ nguyên
    with tx(db["app"], o, "buổi") as c:
        lid = core.locations(c, o)[0]["id"]
        for d in (date(2026, 9, 3), date(2026, 10, 3)):
            schedule.add_and_confirm_extra(c, o, o, day=d, start=time(15), end=time(16), location_id=lid, session_kind="regular",
                                           attendees=[{"name": "Bé Sáu"}, {"name": "Bé Sáu"}], override_reason="t")
        from anhu.services import reports
        reports.lock_period(c, o, o, 2026, 9)
    with tx(db["app"], o, "gắn tên") as c:
        un = {u["name"]: u for u in students_io.unlinked_names(c, o)}
        assert un["Bé Sáu"]["n"] == 4 and un["Bé Bí Mật"]["suggest"]
        r = students_io.link_name(c, o, "Bé Sáu", None, create_new=True)
        assert r["linked"] == 2 and r["locked_left"] == 2
        with pytest.raises(BizError):
            students_io.link_name(c, o, "Bé Bí Mật", None)
        rep = students_io.by_teacher(c, core.visible_owners(c, o), date(2026, 1, 1), date(2026, 12, 31))
        assert rep[0]["sessions"] == 3 and rep[0]["self"]


def test_remember_device_token_is_bound_to_session(db):
    s = _login(db, "admin@example.test")
    with tx(db["app"]) as c:
        tok = accounts.remember_device(c, s["session"])
    with tx(db["app"]) as c:                       # mã chỉ đặt được một lần cho phiên
        with pytest.raises(DBAPIError, match="SESSION_INVALID"):
            accounts.remember_device(c, s["session"])
    with tx(db["app"]) as c:
        assert accounts.resume(c, tok) == s["session"]
        assert accounts.resume(c, tok + "x") is None and accounts.resume(c, "") is None
        assert scalar(c, "SELECT count(*) FROM pg_catalog.pg_tables WHERE tablename = 'app_sessions'") == 1
    with tx(db["admin"]) as c:                     # DB chỉ lưu mã băm
        assert scalar(c, "SELECT count(*) FROM app_sessions WHERE resume_hash = :t", t=tok) == 0
        assert scalar(c, "SELECT count(*) FROM app_sessions WHERE resume_hash = :h", h=accounts.token_hash(tok)) == 1
    with tx(db["app"]) as c:
        accounts.logout(c, s["session"])
    with tx(db["app"]) as c:                       # đăng xuất -> mã ghi nhớ hết hiệu lực
        assert accounts.resume(c, tok) is None


def test_malicious_xlsx_xxe_is_rejected(db, tmp_path):
    """XLSX có thực thể XML ngoài (XXE) trỏ tới file trên máy chủ: phải bị từ chối, không đọc lộ nội dung."""
    import io
    import zipfile
    from openpyxl import Workbook
    secret = tmp_path / "bi_mat.txt"
    secret.write_text("NOI-DUNG-BI-MAT", encoding="utf-8")
    wb = Workbook()
    wb.active.append(["Tên học viên"])
    wb.active.append(["Bé X"])
    buf = io.BytesIO()
    wb.save(buf)
    src = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for n in src.namelist():
            data = src.read(n)
            if n == "xl/worksheets/sheet1.xml":
                data = data.replace(b"<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>",
                                    b"<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY e SYSTEM \"" + secret.as_uri().encode() + b"\">]>", 1)
                data = data.replace(b"<v>", b"<v>&e;", 1) if b"<v>" in data else data
            z.writestr(n, data)
    t = _login(db, "gv2@example.test", "mat-khau-moi-456")
    with tx(db["app"], t["id"]) as c:
        try:
            items = students_io.preview_import(c, t["id"], out.getvalue())
        except BizError:
            items = None
    assert items is None or all("NOI-DUNG-BI-MAT" not in str(i) for i in items)


def test_migrate_refuses_role_without_bypassrls(db):
    """Chạy migrate bằng role không đủ quyền (VD nhầm sang anhu_app) -> dừng với thông báo rõ, không làm hỏng CSDL."""
    from scripts.migrate import migrate
    with pytest.raises(SystemExit, match="BYPASSRLS"):
        migrate(db["app"], "app-pw-test")
