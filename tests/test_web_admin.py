"""Quản trị trên web (HTTP, Postgres thật): mọi cấu hình vận hành làm được qua giao diện."""
import io
import re
import zipfile
from pathlib import Path
from datetime import date, time

import pytest
from openpyxl import load_workbook

from .conftest import ALLOWED, PW
from .test_web import login, post


@pytest.fixture(scope="module")
def app(pg, users):
    from anhu.web import create_app
    return create_app({"DATABASE_URL": pg["app"], "ALLOWED_EMAILS": ALLOWED, "SECRET_KEY": "test2", "TESTING": True,
                       "SESSION_COOKIE_SECURE": False})


@pytest.fixture(scope="module")
def ca(app):
    return login(app, ALLOWED[0])


def _html(r):
    return r.get_data(as_text=True)


def _center_and_location(c, name):
    post(c, "/cai-dat/trung-tam", {"name": f"TT {name}"})
    html = _html(c.get("/cai-dat/don-gia"))
    cid = re.search(rf'<option value="([0-9a-f-]{{36}})">TT {name}</option>', html).group(1)
    post(c, "/cai-dat/co-so", {"name": f"CS {name}", "kind": "center_branch", "center_id": cid})
    html = _html(c.get("/lich/lap"))
    lid = re.search(rf'<option value="([0-9a-f-]{{36}})"[^>]*>CS {name}', html).group(1)
    return cid, lid


def test_main_navigation_has_exactly_five_items_and_settings_link(ca):
    html = _html(ca.get("/"))
    nav = re.search(r'<nav class="nav".*?</nav>', html, re.S).group(0)
    assert re.findall(r">([^<]+)</a>", nav) == ["Tổng quan", "Lịch dạy", "Học viên", "Chấm công", "Báo cáo"]
    assert 'class="gear' in html and "/cai-dat" in html


@pytest.mark.parametrize("sec", ["tai-khoan", "danh-muc", "lop-hoc", "loai-ca", "don-gia", "quy-tac", "excel", "nhat-ky",
                                 "sao-luu", "dong-bo", "he-thong"])
def test_every_settings_section_renders_with_help(ca, sec):
    r = ca.get(f"/cai-dat/{sec}")
    assert r.status_code == 200, _html(r)[:400]
    assert "← Cài đặt" in _html(r)


def test_settings_hub_and_redirect_sections(ca):
    html = _html(ca.get("/cai-dat"))
    for t in ("Tài khoản &amp; quyền truy cập", "Trung tâm, cơ sở &amp; địa điểm", "Môn học, lớp học &amp; nhóm học viên",
              "Thời khóa biểu cố định &amp; ngoại lệ", "Loại ca dạy &amp; trạng thái buổi học", "Đơn giá &amp; ngày hiệu lực",
              "Quy tắc chấm công", "Mẫu Excel &amp; ánh xạ dữ liệu", "Kỳ chấm công", "Lịch sử chỉnh sửa &amp; nhật ký",
              "Sao lưu &amp; khôi phục", "Đồng bộ dữ liệu"):
        assert t in html, t
    assert ca.get("/cai-dat/lich").headers["Location"].endswith("/lich/lap")
    assert ca.get("/cai-dat/hoc-vien").headers["Location"].endswith("/hoc-vien")
    assert ca.get("/cai-dat/ky").headers["Location"].endswith("/cham-cong/ky")
    assert ca.get("/cai-dat/khong-co").status_code == 404


@pytest.mark.parametrize("tab", ["nam", "hoc-vien", "co-so", "day-rieng"])
def test_reports_render_and_export(ca, tab):
    r = ca.get(f"/bao-cao?tab={tab}&nam=2026&tu=2026-01-01&den=2026-12-31")
    assert r.status_code == 200 and "Báo cáo" in _html(r)
    x = ca.get(f"/bao-cao?tab={tab}&nam=2026&tu=2026-01-01&den=2026-12-31&xuat=xlsx")
    assert x.status_code == 200 and load_workbook(io.BytesIO(x.data)).active.max_row >= 1


def test_catalog_edit_archive_via_web(ca):
    cid, lid = _center_and_location(ca, "Quản trị")
    r = post(ca, "/cai-dat/sua-co-so", {"id": lid, "name": "CS Quản trị 2", "kind": "center_branch", "center_id": cid},
             follow_redirects=True)
    assert "Tên mới áp dụng cho ca mới" in _html(r)
    r = post(ca, "/cai-dat/luu-tru-co-so", {"id": lid, "archived": "1"}, follow_redirects=True)
    assert "Đã lưu trữ cơ sở" in _html(r) and "đã lưu trữ" in _html(r)
    assert "CS Quản trị 2" not in _html(ca.get("/lich/lap"))           # ẩn khỏi danh sách chọn
    post(ca, "/cai-dat/luu-tru-co-so", {"id": lid, "archived": "0"})
    assert "CS Quản trị 2" in _html(ca.get("/lich/lap"))
    r = post(ca, "/cai-dat/sua-co-so", {"id": lid, "name": "  ", "kind": "center_branch", "center_id": cid}, follow_redirects=True)
    assert "không được trống" in _html(r)


def test_validation_messages_are_friendly(ca):
    r = post(ca, "/cai-dat/don-gia", {"category": "hv1", "amount": "-5", "valid_from": "2030-01-01"}, follow_redirects=True)
    assert "Số tiền phải là số không âm" in _html(r)
    r = post(ca, "/cai-dat/quy-tac", {"thirty_minute_max": "30", "j_kinds": "trial", "hv_kinds": "trial"}, follow_redirects=True)
    assert "cộng đôi" in _html(r)
    r = post(ca, "/cai-dat/xuat-excel", {"sheet_name": "a[b]"}, follow_redirects=True)
    assert "Tên sheet" in _html(r)
    r = post(ca, "/cai-dat/anh-xa", {"name": "x", "col_times": "C"}, follow_redirects=True)
    assert "phải có cột Ngày" in _html(r)


def test_kind_labels_and_new_kind_via_web(ca):
    r = post(ca, "/cai-dat/them-loai-ca", {"label": "Ca kèm 1-1"}, follow_redirects=True)
    assert "CHƯA được tính công" in _html(r)
    html = _html(ca.get("/cai-dat/loai-ca"))
    code = re.search(r'name="label_(c_[a-z0-9_]+)" value="Ca kèm 1-1"', html).group(1)
    assert "Ca kèm 1-1" in _html(ca.get("/buoi-day"))                     # có trong danh sách chọn loại ca
    labels = dict(re.findall(r'name="label_([a-z0-9_]+)" value="([^"]*)"', html))
    data = {f"label_{k}": v for k, v in labels.items()}
    data.update({f"active_{k}": "1" for k in labels if k != code})       # ẩn loại ca mới
    data[f"label_{code}"] = "Ca kèm riêng"
    post(ca, "/cai-dat/loai-ca", data)
    html = _html(ca.get("/buoi-day"))
    assert "Ca kèm riêng" not in html and "Ca kèm 1-1" not in html
    assert "Ca kèm riêng" in _html(ca.get("/cai-dat/quy-tac"))             # vẫn cấu hình được trong quy tắc


def test_student_contacts_groups_archive_via_web(ca):
    r = post(ca, "/hoc-vien", {"display_name": "Bé Web Liên Hệ", "code": "W01"})
    sid = r.headers["Location"].rsplit("/", 1)[1]
    url = f"/hoc-vien/{sid}"
    r = post(ca, url, {"act": "contact", "name": "Mẹ Bé Web", "relation": "Mẹ", "phone": "0900 000 000", "is_primary": "1"},
             follow_redirects=True)
    assert "Đã lưu người liên hệ" in _html(r) and "Mẹ Bé Web" in _html(r)
    r = post(ca, url, {"act": "contact", "name": "Sai", "phone": "gọi sau"}, follow_redirects=True)
    assert "Số điện thoại" in _html(r)
    post(ca, "/cai-dat/lop", {"name": "Nhóm Web", "kind": "group"})
    gid = re.search(r'<option value="([0-9a-f-]{36})">Nhóm Web</option>', _html(ca.get(url))).group(1)
    post(ca, url, {"act": "group", "group_id": gid})
    assert "Bé Web Liên Hệ" in _html(ca.get(f"/hoc-vien?nhom={gid}"))
    r = post(ca, url, {"act": "archive"}, follow_redirects=True)
    assert "Đã lưu trữ học viên" in _html(r)
    assert "Bé Web Liên Hệ" not in _html(ca.get("/hoc-vien"))
    assert "Bé Web Liên Hệ" in _html(ca.get("/hoc-vien?trang_thai=archived"))


def test_audit_log_page_filters(ca):
    html = _html(ca.get("/cai-dat/nhat-ky?bang=students&q=Web"))
    assert "Học viên" in html and "Bé Web Liên Hệ" in html
    assert "trang 1/" in html


def test_backup_download_and_verify_via_web(ca):
    r = post(ca, "/cai-dat/sao-luu/tai")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.data))
    assert set(z.namelist()) == {"manifest.json", "data.json"}
    v = post(ca, "/cai-dat/sao-luu/kiem-tra", {"file": (io.BytesIO(r.data), "b.zip")}, content_type="multipart/form-data")
    assert "File nguyên vẹn" in _html(v)
    bad = post(ca, "/cai-dat/sao-luu/kiem-tra", {"file": (io.BytesIO(b"xx"), "b.zip")}, content_type="multipart/form-data",
               follow_redirects=True)
    assert "không phải bản sao lưu" in _html(bad)
    rs = post(ca, "/cai-dat/sao-luu/khoi-phuc", {"file": (io.BytesIO(r.data), "b.zip"), "confirm_text": "KHOI PHUC"},
              content_type="multipart/form-data", follow_redirects=True)
    assert "đã có dữ liệu" in _html(rs)                                    # không ghi đè tài khoản đang dùng
    assert "Bản sao lưu gần nhất" in _html(ca.get("/cai-dat/sao-luu"))


def test_locked_month_shows_snapshot_and_exports_locked_file(ca):
    cid, lid = _center_and_location(ca, "Chốt Web")
    post(ca, "/buoi-day/them", {"day": "2032-07-08", "start": "09:00", "end": "10:00", "location_id": lid, "kind": "regular",
                                "extra_names": "Hà, Hà", "confirm_now": "1", "override_reason": "test"})
    post(ca, "/cham-cong/chot", {"y": "2032", "m": "7"})
    html = _html(ca.get("/cham-cong?thang=2032-07"))
    assert "bản đã chốt" in html and "Hà, Hà" in html
    f1 = ca.get("/cham-cong/xuat?thang=2032-07").data
    f2 = ca.get("/cham-cong/ban-chot?thang=2032-07").data
    assert f1 == f2
    ky = _html(ca.get("/cham-cong/ky?thang=2032-07"))
    assert "07/2032" in ky and "Phiên bản 1" in ky
    vid = re.search(r"/cham-cong/ky/phien-ban/([0-9a-f-]{36})", ky).group(1)
    assert ca.get(f"/cham-cong/ky/phien-ban/{vid}").data == f2


def test_non_admin_cannot_manage_accounts(app):
    cb = login(app, ALLOWED[1])
    html = _html(cb.get("/cai-dat/tai-khoan"))
    assert "Tài khoản giáo viên" not in html
    r = post(cb, "/cai-dat/tao-tai-khoan", {"email": "x@example.test", "display_name": "X"}, follow_redirects=True)
    assert "quản trị" in _html(r)


def test_temp_password_forces_change(app, ca):
    html = _html(ca.get("/cai-dat/tai-khoan"))
    bid = re.search(r'name="user_id" value="([0-9a-f-]{36})"><button class="btn small">Đặt lại', html).group(1)
    r = post(ca, "/cai-dat/dat-lai-mat-khau", {"user_id": bid}, follow_redirects=True)
    temp = re.search(r'<code class="pw">([^<]+)</code>', _html(r)).group(1)
    assert temp not in _html(ca.get("/cai-dat/tai-khoan"))                  # chỉ hiện một lần
    c = app.test_client()
    from .test_web import _csrf
    tok = _csrf(c)
    c.post("/login", data={"email": ALLOWED[1], "password": temp, "_csrf": tok})
    c.tok = _csrf(c)
    r = c.get("/cham-cong")
    assert r.status_code == 302 and "/cai-dat/tai-khoan" in r.headers["Location"]
    r = post(c, "/cai-dat/mat-khau", {"old": temp, "new": PW, "new2": PW}, follow_redirects=True)
    assert "Đã đổi mật khẩu" in _html(r)
    assert c.get("/cham-cong").status_code == 200


def test_health_and_database_down_page(app, ca):
    n_files = len(list((Path(__file__).parents[1] / "db" / "migrations").glob("*.sql")))
    assert ca.get("/suc-khoe").get_json() == {"status": "ok", "db": "ok", "migrations": n_files}
    from anhu.web import create_app
    down = create_app({"DATABASE_URL": "postgresql://anhu_app:x@127.0.0.1:1/khong_co", "ALLOWED_EMAILS": ALLOWED,
                       "SECRET_KEY": "t", "TESTING": True, "SESSION_COOKIE_SECURE": False})
    c = down.test_client()
    r = c.get("/suc-khoe")
    assert r.status_code == 503 and r.get_json()["db"] == "unreachable"
    with c.session_transaction() as s:
        s["uid"] = "00000000-0000-0000-0000-000000000000"
    r = c.get("/lich")
    assert r.status_code == 503 and "Không kết nối được cơ sở dữ liệu" in _html(r) and "không bị mất" in _html(r)
