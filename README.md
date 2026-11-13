# ANHU Teaching Studio

Website quản lý lịch dạy, xác nhận buổi dạy, chấm công theo mẫu Excel gốc, đối soát với trung tâm và báo cáo. Dành cho hai giáo viên dùng hằng ngày. Mọi nghiệp vụ và cấu hình thường ngày đều làm trên web.

- **Giao diện chính:** Streamlit. Entrypoint là `app.py` ở thư mục gốc.
- **Dữ liệu:** PostgreSQL (Supabase hoặc Postgres bất kỳ) với Row Level Security. Ứng dụng kết nối bằng role `anhu_app`, không có quyền vượt RLS.
- **Nghiệp vụ:** `anhu/domain` và `anhu/services`, đã có test. Hai giao diện dùng chung tầng này:
  - Streamlit (`anhu/ui`), giao diện chính;
  - Flask (`anhu/web`), giữ làm phương án dự phòng cho hosting không phải Streamlit.

## Tài liệu

| Tài liệu | Dành cho |
|---|---|
| [docs/HUONG_DAN_SU_DUNG.md](docs/HUONG_DAN_SU_DUNG.md) | Giáo viên (không cần biết kỹ thuật) |
| [docs/QUAN_TRI_TAI_KHOAN.md](docs/QUAN_TRI_TAI_KHOAN.md) | Quản trị viên: tạo và quản lý tài khoản |
| [docs/DEPLOY_STREAMLIT.md](docs/DEPLOY_STREAMLIT.md) | Người triển khai: GitHub riêng tư → Supabase → Streamlit Community Cloud |
| [docs/BAO_MAT.md](docs/BAO_MAT.md) | Quyền truy cập, secrets, sao lưu |
| [docs/UX_REVIEW.md](docs/UX_REVIEW.md) | Đánh giá và tối ưu thao tác |
| [docs/NGHIEM_THU_CUOI.md](docs/NGHIEM_THU_CUOI.md) | Báo cáo kiểm thử và nghiệm thu |
| [docs/QUAN_TRI_VAN_HANH.md](docs/QUAN_TRI_VAN_HANH.md) | Vận hành: sao lưu toàn bộ, chuyển máy chủ, cập nhật phiên bản |
| [docs/EXCEL_MAPPING.md](docs/EXCEL_MAPPING.md) | Phân tích file Excel gốc và quy tắc xếp cột |

## Chạy trên máy (Windows / macOS / Linux)

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt        # macOS/Linux: .venv/bin/pip

# Cách 1 — Postgres nhúng có sẵn dữ liệu demo ẩn danh (không cần cài Postgres):
.venv/Scripts/python scripts/dev.py --demo --no-serve    # tạo private/pgdata, migrate, 2 tài khoản demo
#   -> chuỗi kết nối ghi ở private/dev_app_url.txt, mật khẩu demo ở private/dev_accounts.txt

# Cách 2 — Postgres có sẵn:
#   ANHU_ADMIN_DATABASE_URL=postgresql://postgres:...@host/db ANHU_APP_DB_PASSWORD=... python scripts/migrate.py

# Chạy giao diện Streamlit:
#   đặt ANHU_DATABASE_URL (role anhu_app) và ANHU_BOOTSTRAP_TOKEN, hoặc tạo .streamlit/secrets.toml từ file .example
.venv/Scripts/streamlit run app.py                         # http://localhost:8501
```

Lần đầu mở một CSDL chưa có tài khoản nào, ứng dụng hiện màn hình **Thiết lập lần đầu**, cần nhập `ANHU_BOOTSTRAP_TOKEN`. Sau khi tạo xong quản trị viên đầu tiên, bước này bị khóa vĩnh viễn.

## Kiểm thử

```bash
.venv/Scripts/python -m pytest       # 169 test: nghiệp vụ, golden-master Excel, RLS/DB, tài khoản, giao diện Streamlit (AppTest), HTTP Flask
```

Test tự dựng PostgreSQL 16 nhúng và kết nối bằng role `anhu_app`, giống môi trường thật. Kiểm thử trên trình duyệt thật (Playwright, desktop và mobile) được mô tả trong `docs/NGHIEM_THU_CUOI.md`.

## Cấu trúc

```
app.py                     entrypoint Streamlit (chỉ dựng khung, không migrate / tạo user / import khi chạy lại)
anhu/domain/               quy tắc thuần: đếm tên, giờ, bảng công A:L, tiền, Excel, đối soát, import cũ
anhu/services/             nghiệp vụ trên DB (mọi hàm nhận connection đã gắn app.user_id)
anhu/ui/                   giao diện Streamlit: Tổng quan, Lịch dạy, Học viên, Chấm công, Báo cáo, Cài đặt
anhu/web/, anhu/templates/ giao diện Flask (dự phòng)
db/migrations/             001…006 — SQL có phiên bản; *_grants.sql luôn chạy lại sau cùng
scripts/                   migrate.py, backup.py, create_user.py, dev.py, golden_compare.py
tests/                     pytest (+ tests/st_apps/harness.py cho Streamlit AppTest)
.streamlit/config.toml     giao diện xanh rừng / champagne / kem; tắt hiển thị chi tiết lỗi cho người dùng
```

Không bao giờ commit: `private/`, `.streamlit/secrets.toml`, `.env`, file Excel thật, bản sao lưu (`.zip`, `.dump`). `.gitignore` đã chặn các file này.
