"""Kiểm thử qua HTTP (Flask test client) với Postgres thật."""
import io
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from .conftest import ALLOWED, PW

FIX = Path(__file__).parent / "fixtures" / "anon_source.xlsx"


@pytest.fixture(scope="module")
def app(pg, users):
    from anhu.web import create_app
    return create_app({"DATABASE_URL": pg["app"], "ALLOWED_EMAILS": ALLOWED, "SECRET_KEY": "test", "TESTING": True,
                       "SESSION_COOKIE_SECURE": False})


def _csrf(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s["csrf"]


def login(app, email):
    c = app.test_client()
    tok = _csrf(c)
    r = c.post("/login", data={"email": email, "password": PW, "_csrf": tok})
    assert r.status_code == 302, r.data
    c.tok = _csrf(c)
    return c


def post(c, url, data=None, **kw):
    d = dict(data or {})
    d["_csrf"] = c.tok
    return c.post(url, data=d, **kw)


@pytest.fixture(scope="module")
def ca(app):
    return login(app, ALLOWED[0])


@pytest.fixture(scope="module")
def cb(app):
    return login(app, ALLOWED[1])


def test_requires_login_and_csrf(app):
    c = app.test_client()
    assert c.get("/").status_code == 302 and "/login" in c.get("/").headers["Location"]
    assert c.post("/login", data={"email": ALLOWED[0], "password": PW}).status_code == 400  # thiếu CSRF
    tok = _csrf(c)
    assert c.post("/login", data={"email": ALLOWED[0], "password": "sai", "_csrf": tok}).status_code == 401
    r = c.get("/login")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"


@pytest.mark.parametrize("url", ["/", "/lich", "/lich?che_do=ngay", "/lich?che_do=thang", "/lich/lap", "/buoi-day",
                                 "/hoc-vien", "/cham-cong", "/cham-cong?tab=detail", "/cham-cong?loai=all",
                                 "/cham-cong?thang=2028-02", "/doi-soat", "/cai-dat"])
def test_pages_render(ca, url):
    r = ca.get(url)
    assert r.status_code == 200, (url, r.data[:500])
    assert "Tổng quan" in r.get_data(as_text=True)


def _setup_location(c):
    post(c, "/cai-dat/trung-tam", {"name": "TT Web"})
    html = c.get("/cai-dat/don-gia").get_data(as_text=True)
    cid = re.search(r'<option value="([0-9a-f-]{36})">TT Web</option>', html).group(1)
    post(c, "/cai-dat/co-so", {"name": "CS Web", "kind": "center_branch", "center_id": cid})
    html = c.get("/lich/lap").get_data(as_text=True)
    return re.search(r'<option value="([0-9a-f-]{36})"[^>]*>CS Web', html).group(1)


def test_full_flow_schedule_confirm_timesheet_export(ca, cb):
    lid = _setup_location(ca)
    start = date(2031, 3, 3)  # thứ Hai
    r = post(ca, "/lich/lap", {"weekday": ["0", "2"], "start": "16:45", "end": "17:45", "valid_from": start.isoformat(),
                               "valid_to": "2031-03-31", "location_id": lid, "kind": "regular", "extra_names": "Phúc, My, My, Quỳnh"})
    assert r.status_code == 302
    html = ca.get(f"/lich?ngay={start.isoformat()}").get_data(as_text=True)
    # chỉ các ca của lịch vừa tạo (tài khoản A có thể còn lịch lặp khác từ test trước)
    pids = re.findall(r'href="/ca/([0-9a-f-]{36})"[^>]*>\s*<b>16:45–17:45</b>\s*<span>CS Web', html)
    assert len(pids) == 2  # thứ Hai + thứ Tư trong tuần
    page = ca.get(f"/ca/{pids[0]}").get_data(as_text=True)
    assert "Xác nhận đã dạy" in page and "Phúc" in page
    assert not re.search(r'name="(n|so_hv|count|hv_count|e|f|g|h)"', page)  # không có ô nhập số HV
    # xác nhận: giữ 4 tên dự kiến (tick), thêm 0 tên
    present = re.findall(r'name="present" value="([^"]*)"', page)
    assert present == ["|Phúc", "|My", "|My", "|Quỳnh"]
    r = post(ca, f"/ca/{pids[0]}/xac-nhan", {"present": present})
    assert r.status_code == 302 and "/buoi/" in r.headers["Location"]
    ts = ca.get("/cham-cong?thang=2031-03").get_data(as_text=True)
    assert "16g45-17g45" in ts and "Phúc, My, My, Quỳnh" in ts
    x = ca.get("/cham-cong/xuat?thang=2031-03")
    assert x.status_code == 200 and x.mimetype.endswith("sheet")
    ws = load_workbook(io.BytesIO(x.data))["Chấm công"]
    assert ws["A5"].value.date() == start and ws["H5"].value == 1 and ws["C5"].value == "16g45-17g45"
    assert ws["A4"].value.date() == date(2031, 3, 2) and all(ws[f"{k}4"].value is None for k in "BCDEFGHIJKL")
    assert ws["B34"].value == "TỔNG"
    # B không xem/sửa được dữ liệu của A qua URL trực tiếp
    assert cb.get(f"/ca/{pids[1]}").status_code == 404
    aid = r.headers["Location"].rsplit("/", 1)[1]
    assert cb.get(f"/buoi/{aid}").status_code == 404
    post(cb, f"/ca/{pids[1]}/xac-nhan", {"extra_names": "Kẻ lạ"})
    page = ca.get(f"/ca/{pids[1]}").get_data(as_text=True)
    assert "Dự kiến" in page or "Chờ xác nhận" in page
    assert "Kẻ lạ" not in ca.get("/cham-cong?thang=2031-03").get_data(as_text=True)
    assert "16g45" not in cb.get("/cham-cong?thang=2031-03").get_data(as_text=True)


def test_edit_actual_requires_reason_and_lock_blocks(ca):
    lid = _setup_location(ca)
    r = post(ca, "/buoi-day/them", {"day": "2031-05-06", "start": "09:00", "end": "10:00", "location_id": lid, "kind": "regular",
                                    "extra_names": "An, Bình", "confirm_now": "1"})
    aid = r.headers["Location"].rsplit("/", 1)[1]
    post(ca, f"/buoi/{aid}", {"start": "09:00", "end": "10:00", "kind": "regular", "extra_names": "An, Bình, Minh"})
    assert "An, Bình, Minh" not in ca.get("/cham-cong?thang=2031-05").get_data(as_text=True)  # thiếu lý do -> không lưu
    post(ca, f"/buoi/{aid}", {"start": "09:00", "end": "10:00", "kind": "regular", "extra_names": "An, Bình, Minh",
                              "reason": "thêm HV", "status": "confirmed"})
    ts = ca.get("/cham-cong?thang=2031-05").get_data(as_text=True)
    assert "An, Bình, Minh" in ts
    post(ca, "/cham-cong/chot", {"y": "2031", "m": "5"})
    r = post(ca, f"/buoi/{aid}", {"start": "09:00", "end": "10:00", "kind": "regular", "extra_names": "X",
                                  "reason": "thử sửa sau chốt", "status": "confirmed"}, follow_redirects=True)
    assert "đã chốt" in r.get_data(as_text=True)
    assert ca.get("/cham-cong/ban-chot?thang=2031-05").status_code == 200
    r = post(ca, "/cham-cong/mo", {"y": "2031", "m": "5", "reason": ""}, follow_redirects=True)
    assert "lý do" in r.get_data(as_text=True)


def test_recon_upload_mapping_and_report(ca):
    lid = _setup_location(ca)
    post(ca, "/buoi-day/them", {"day": "2031-06-03", "start": "16:45", "end": "17:45", "location_id": lid, "kind": "regular",
                                "extra_names": "An", "confirm_now": "1"})
    data = ca.get("/cham-cong/xuat?thang=2031-06").data
    wb = load_workbook(io.BytesIO(data))
    wb["Chấm công"]["C5"] = "16g45-17g30"
    buf = io.BytesIO(); wb.save(buf)
    r = post(ca, "/doi-soat", {"thang": "2031-06", "file": (io.BytesIO(buf.getvalue()), "tt.xlsx")},
             content_type="multipart/form-data")
    assert r.status_code == 302 and "/mapping" in r.headers["Location"]
    iid = r.headers["Location"].split("/")[2]
    m = ca.get(r.headers["Location"]).get_data(as_text=True)
    assert "Chạy đối chiếu" in m and 'selected>A</option>' in m.replace(" selected>", " selected>")
    cols = {"col_date": "A", "col_location": "B", "col_times": "C", "col_names": "D", "col_E": "E", "col_F": "F",
            "col_G": "G", "col_H": "H", "col_I": "I", "col_J": "J", "col_K": "K"}
    r = post(ca, f"/doi-soat/{iid}/mapping", {"sheet": "Chấm công", "start_row": "3", **cols})
    assert r.status_code == 302
    d = ca.get(f"/doi-soat/{iid}").get_data(as_text=True)
    assert "Lệch giờ" in d and "Hàng 5" in d
    rep = ca.get(f"/doi-soat/{iid}/bien-ban")
    assert rep.status_code == 200 and load_workbook(io.BytesIO(rep.data))
    assert ca.get(f"/doi-soat/{iid}/file-goc").data == buf.getvalue()


def test_legacy_import_via_web(cb):
    r = post(cb, "/cai-dat/import", {"file": (io.BytesIO(FIX.read_bytes()), "anon.xlsx")}, content_type="multipart/form-data")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "205" in html
    iid = re.search(r'name="import_id" value="([0-9a-f-]{36})"', html).group(1)
    keep = re.findall(r'name="keep" value="([0-9a-f]+)"', html)
    r = post(cb, "/cai-dat/import", {"import_id": iid, "keep": keep}, follow_redirects=True)
    assert f"Đã import {len(keep)} ca" in r.get_data(as_text=True)
    ts = cb.get("/cham-cong?thang=2026-10").get_data(as_text=True)
    assert "16g45-17g45" in ts and "HV15, HV16, HV16, HV06" in ts


def test_upload_rejects_non_xlsx(ca):
    r = post(ca, "/doi-soat", {"thang": "2031-06", "file": (io.BytesIO(b"not excel"), "x.xlsx")},
             content_type="multipart/form-data", follow_redirects=True)
    assert "không phải XLSX" in r.get_data(as_text=True)
