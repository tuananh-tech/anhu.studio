"""Giao diện Streamlit THẬT (streamlit.testing AppTest, không trình duyệt) trên CSDL mới, role ứng dụng, RLS bật.
Kiểm: khởi tạo quản trị, đăng nhập, mọi trang hiển thị không lỗi, thao tác ghi chính, chống bấm trùng, lời mời, phân quyền, phiên."""
import os
from datetime import date, time, timedelta
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from anhu.db import scalar, tx

HARNESS = str(Path(__file__).parent / "st_apps" / "harness.py")
TOKEN = "ma-khoi-tao-kiem-thu-0123456789"
PW = "mat-khau-ui-test-123"
PAGES = ["tong_quan", "lich_day", "hoc_vien", "cham_cong", "bao_cao", "cai_dat"]


@pytest.fixture(scope="module")
def db(make_db):
    d = make_db()
    old = {k: os.environ.get(k) for k in ("ANHU_DATABASE_URL", "ANHU_BOOTSTRAP_TOKEN")}
    os.environ["ANHU_DATABASE_URL"] = d["app"]
    os.environ["ANHU_BOOTSTRAP_TOKEN"] = TOKEN
    yield d
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def new_app():
    at = AppTest.from_file(HARNESS, default_timeout=60)
    at.run()
    return at


def w(coll, label):
    found = [x for x in coll if x.label == label]
    assert found, f"không thấy '{label}' trong {[x.label for x in coll]}"
    return found[0]


def texts(at):
    out = []
    for kind in ("markdown", "success", "error", "warning", "info", "caption", "title", "subheader", "metric"):
        for e in getattr(at, kind):
            out.append(str(getattr(e, "value", "")) + " " + str(getattr(e, "label", "")))
    return "\n".join(out)


def login(at, email, pw=PW):
    w(at.text_input, "Email").input(email)
    w(at.text_input, "Mật khẩu").input(pw)
    w(at.button, "Đăng nhập").click().run()
    return at


def ok(at):
    assert not at.exception, [e.value for e in at.exception]


def show(at, page, **state):
    at.session_state["_test_page"] = page
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    ok(at)
    return at


def test_01_bootstrap_once_then_login(db):
    at = new_app()
    ok(at)
    assert "Thiết lập lần đầu" in texts(at)
    w(at.text_input, "Mã khởi tạo").input("sai-ma-khoi-tao-1234567890")
    w(at.text_input, "Email của bạn").input("quantri@example.test")
    w(at.text_input, "Tên hiển thị").input("Cô Quản Trị")
    w(at.text_input, "Mật khẩu (≥ 10 ký tự)").input(PW)
    w(at.text_input, "Nhập lại mật khẩu").input(PW)
    w(at.button, "Tạo tài khoản quản trị").click().run()
    assert "Mã khởi tạo không đúng" in texts(at)
    w(at.text_input, "Mã khởi tạo").input(TOKEN)
    w(at.button, "Tạo tài khoản quản trị").click().run()
    ok(at)
    assert "Đã tạo quản trị viên" in texts(at)
    assert "Thiết lập lần đầu" not in texts(at)          # bước khởi tạo đã biến mất
    at2 = new_app()                                     # phiên khác cũng không thấy bước khởi tạo
    assert "Thiết lập lần đầu" not in texts(at2) and any(b.label == "Đăng nhập" for b in at2.button)
    login(at2, "quantri@example.test", "sai-mat-khau-123")
    assert "không đúng" in texts(at2)
    login(at2, "quantri@example.test")
    ok(at2)
    assert "Chào Cô Quản Trị" in texts(at2)


@pytest.fixture(scope="module")
def admin_app(db):
    at = new_app()
    login(at, "quantri@example.test")
    ok(at)
    return at


def test_02_every_page_renders_empty_and_settings_sections(admin_app):
    at = admin_app
    for p in PAGES:
        show(at, p)
    for v in ["Hôm nay", "Tuần", "Tháng", "Chờ xác nhận", "Thêm ca", "Lịch cố định"]:
        show(at, "lich_day", lich_view=v)
    for v in ["Bảng công", "Chi tiết ca", "Đối soát", "Kỳ chấm công"]:
        show(at, "cham_cong", cc_view=v)
    for v in ["Năm", "Tháng", "Học viên", "Giáo viên", "Cơ sở", "Dạy riêng"]:
        show(at, "bao_cao", bc_view=v)
    for v in ["Danh sách", "Gắn tên cũ", "Nhập / xuất Excel"]:
        show(at, "hoc_vien", hv_view=v)
    from anhu.ui.p_settings import SECTIONS
    for s in SECTIONS:
        show(at, "cai_dat", set_sec=s)


def test_03_setup_catalog_student_and_add_confirmed_session(admin_app, db):
    at = show(admin_app, "cai_dat", set_sec="Trung tâm & cơ sở")
    w(at.text_input, "Trung tâm mới").input("TT Ánh Dương")
    w(at.button, "Thêm trung tâm").click().run()
    ok(at)
    assert "Đã thêm trung tâm" in texts(at)
    at = show(at, "cai_dat", set_sec="Trung tâm & cơ sở")
    w(at.text_input, "Tên cơ sở / địa điểm mới").input("Cơ sở A")
    with tx(db["admin"]) as c:
        cid = str(scalar(c, "SELECT id FROM centers WHERE name = 'TT Ánh Dương'"))
    w(at.selectbox, "Thuộc trung tâm (với cơ sở trung tâm)").set_value(cid)
    w(at.button, "Thêm").click().run()
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT count(*) FROM locations WHERE name = 'Cơ sở A'") == 1
    at = show(at, "hoc_vien", hv_view="Danh sách")
    w(at.text_input, "Tên học viên *").input("Bé Phúc")
    w(at.button, "Thêm học viên").click().run()
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT code FROM students WHERE display_name = 'Bé Phúc'") == "HV0001"   # mã tự sinh
    # thêm ca phát sinh, đã dạy, tên trùng được đếm riêng
    at = show(at, "lich_day", lich_view="Thêm ca")
    d = date.today() - timedelta(days=1)
    at.date_input(key="add_d").set_value(d)
    at.time_input(key="add_s").set_value(time(16, 45))
    at.time_input(key="add_e").set_value(time(17, 45))
    at.text_input(key="add_extra").input("Phúc, My, My, Quỳnh")
    at.run()
    assert "= 4 học viên" in texts(at)
    at.checkbox(key="add_done").check()
    w(at.button, "Lưu ca").click().run()
    ok(at)
    w(at.button, "Lưu ca").click().run()                  # bấm trùng ngay -> bị chặn, không ghi lần 2
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT count(*) FROM actual_sessions WHERE session_date = :d AND status = 'confirmed'", d=d) == 1
        assert scalar(c, "SELECT count(*) FROM session_attendees sa JOIN actual_sessions a ON a.id = sa.session_id "
                         "WHERE a.session_date = :d", d=d) == 4
    at = show(at, "cham_cong", cc_view="Bảng công", cc_month=(d.year, d.month))
    at = show(at, "tong_quan")
    assert "Hôm nay" in texts(at) and "Cần chú ý" in texts(at)


def test_04_recurring_schedule_quick_actions_and_bulk_confirm(admin_app, db):
    from anhu.services import schedule
    with tx(db["admin"]) as c:
        uid = scalar(c, "SELECT id FROM app_users WHERE email = 'quantri@example.test'")
        lid = scalar(c, "SELECT id FROM locations WHERE name = 'Cơ sở A'")
        sid = scalar(c, "SELECT id FROM students WHERE display_name = 'Bé Phúc'")
    t = date.today()
    with tx(db["app"], str(uid), "lịch") as c:   # lịch hằng ngày từ 10 ngày trước (thao tác tạo lịch có test riêng ở tầng dịch vụ)
        for wd_ in range(7):
            schedule.create_template(c, str(uid), weekday=wd_, start=time(8), end=time(9), valid_from=t - timedelta(days=10),
                                     location_id=lid, expected=[{"student_id": str(sid), "name": "Bé Phúc"}], override_reason="t")
    at = show(admin_app, "lich_day", lich_view="Chờ xác nhận")
    assert "chưa xác nhận" in texts(at)
    w(at.button, next(b.label for b in at.button if b.label.startswith("Xem trước xác nhận"))).click().run()
    ok(at)
    btn = next(b for b in at.button if b.label.startswith("Xác nhận ") and b.label.endswith(" ca"))
    n = int(btn.label.split()[2])
    btn.click().run()
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT count(*) FROM actual_sessions WHERE start_time = '08:00' AND status = 'confirmed'") == n >= 10
    # báo nghỉ ca ngày mai bằng thao tác nhanh
    at = show(at, "lich_day", lich_view="Hôm nay", lich_ngay=t + timedelta(days=1))
    with tx(db["admin"]) as c:
        pid = str(scalar(c, "SELECT id FROM planned_sessions WHERE session_date = :d AND start_time = '08:00'", d=t + timedelta(days=1)))
    at.radio(key=f"mode_{pid}").set_value("Báo nghỉ").run()
    at.selectbox(key=f"cx_r_{pid}").set_value("student_off")
    w(at.button, "Báo nghỉ ca này").click().run()
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT status FROM planned_sessions WHERE id = :i", i=pid) == "cancelled"


def test_05_invite_teacher_activate_and_permissions(admin_app, db):
    at = show(admin_app, "cai_dat", set_sec="Tài khoản")
    w(at.text_input, "Email giáo viên").input("gv.hai@example.test")
    w(at.text_input, "Tên giáo viên").input("Cô Hai")
    w(at.button, "Tạo lời mời").click().run()
    ok(at)
    code = at.code[0].value
    assert len(code.replace("-", "")) == 20
    show(at, "cai_dat", set_sec="Tài khoản")
    assert all(cd.value != code for cd in at.code)          # mã chỉ hiện một lần
    t = new_app()
    t.session_state["auth_mode"] = "Tôi có mã kích hoạt"
    t.run()
    w(t.text_input, "Email").input("gv.hai@example.test")
    w(t.text_input, "Mã kích hoạt").input(code)
    w(t.text_input, "Mật khẩu mới (≥ 10 ký tự)").input("mat-khau-co-hai-1")
    w(t.text_input, "Nhập lại mật khẩu").input("mat-khau-co-hai-1")
    w(t.button, "Kích hoạt").click().run()
    ok(t)
    assert "Đã kích hoạt" in texts(t)
    t2 = new_app()
    login(t2, "gv.hai@example.test", "mat-khau-co-hai-1")
    ok(t2)
    assert "Chào Cô Hai" in texts(t2)
    show(t2, "cai_dat", set_sec="Tài khoản")
    assert "Quản lý tài khoản chỉ dành cho quản trị viên" in texts(t2)
    show(t2, "hoc_vien", hv_view="Danh sách")
    assert "Bé Phúc" not in texts(t2)                       # không thấy dữ liệu của quản trị
    # quản trị thu hồi phiên -> giáo viên bị đăng xuất ở lần tương tác kế tiếp
    with tx(db["admin"]) as c:
        tid = scalar(c, "SELECT id FROM app_users WHERE email = 'gv.hai@example.test'")
    at = show(admin_app, "cai_dat", set_sec="Tài khoản")
    w(at.button, "Thu hồi phiên đăng nhập").click().run()
    ok(at)
    t2.run()
    assert any(b.label == "Đăng nhập" for b in t2.button) and "hết hạn" in texts(t2)


def test_06_timesheet_lock_flow_in_ui(admin_app, db):
    d = date.today() - timedelta(days=1)
    at = show(admin_app, "cham_cong", cc_view="Bảng công", cc_month=(d.year, d.month))
    at.checkbox(key="cc_lock_ok").check().run()
    w(at.button, f"Chốt kỳ {d.month:02d}/{d.year}").click().run()
    ok(at)
    assert "Đã chốt kỳ" in texts(at)
    at = show(at, "cham_cong", cc_view="Bảng công", cc_month=(d.year, d.month))
    assert "bản đã chốt" in texts(at)
    w(at.button, "Chuẩn bị file Excel mẫu trung tâm").click().run()
    ok(at)
    with tx(db["admin"]) as c:
        assert scalar(c, "SELECT count(*) FROM timesheet_period_versions") == 1


def test_07_database_down_shows_friendly_page(db):
    old = os.environ["ANHU_DATABASE_URL"]
    os.environ["ANHU_DATABASE_URL"] = "postgresql://anhu_app:x@127.0.0.1:1/khong_co"
    try:
        from anhu.ui.main import schema_pending
        schema_pending.clear()
        at = new_app()
        ok(at)
        assert "Không kết nối được cơ sở dữ liệu" in texts(at)
    finally:
        os.environ["ANHU_DATABASE_URL"] = old
        schema_pending.clear()
