# Triển khai: GitHub (riêng tư) → Supabase → Streamlit Community Cloud

Tài liệu dành cho người đưa ứng dụng lên Internet. Làm một lần, mất khoảng 30–45 phút. **Không** cần dịch vụ trả phí: cả Supabase lẫn Streamlit Community Cloud đều có gói miễn phí.

> **Trạng thái kiểm chứng.** Quy trình này đã được kiểm thử trên các thành phần **tương đương** sau:
> - PostgreSQL 16 thật;
> - venv sạch cài **chỉ** từ `requirements.txt`;
> - `streamlit run app.py` thật, trên trình duyệt Chromium (desktop và mobile).
>
> Cả 44 gói phụ thuộc đều có wheel Linux x86_64 cho Python 3.12 (`pip download --platform manylinux…`).
>
> **Chưa** chạy trên Supabase và Streamlit Cloud thật, vì cần tài khoản của chủ dự án. Hãy làm đủ các bước kiểm tra ở mục 6.

## 0. Cần chuẩn bị

- Tài khoản GitHub, Supabase, Streamlit Community Cloud (đăng nhập bằng GitHub).
- Máy tính có Python 3.12 để chạy migration lần đầu. Có thể dùng chính máy đang chứa dự án.

## 1. Đưa mã lên GitHub (kho **Private**)

1. Tạo repository **Private** mới trên GitHub, ví dụ `anhu-studio`.
2. Trong thư mục dự án:
   ```bash
   git status            # kiểm tra: KHÔNG có private/, .streamlit/secrets.toml, .env, *.zip, *.dump, file Excel thật
   git add .
   git commit -m "ANHU Teaching Studio"
   git remote add origin https://github.com/<ban>/anhu-studio.git
   git push -u origin master
   ```
   `.gitignore` đã chặn các file bí mật và dữ liệu thật. File Excel duy nhất được commit là `tests/fixtures/anon_source.xlsx`, chứa dữ liệu đã ẩn danh.

## 2. Tạo CSDL Supabase

1. Supabase → **New project**. Chọn region gần Việt Nam (Singapore) và đặt **mật khẩu database** mạnh. Lưu mật khẩu vào trình quản lý mật khẩu.
2. **Project Settings → Database → Connection string**, chọn **Session pooler** (cổng 5432, hỗ trợ IPv4).
   - Kết nối *Direct* của Supabase chỉ có IPv6. Streamlit Cloud và nhiều mạng gia đình không kết nối IPv6 được, nên luôn dùng **Session pooler**.
3. Chạy migration từ máy tính. Lệnh này dùng role `postgres` và **chỉ chạy trên máy bạn**, không đưa vào Streamlit:
   ```bash
   set ANHU_ADMIN_DATABASE_URL=postgresql://postgres.<project-ref>:<MAT_KHAU_DB>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   set ANHU_APP_DB_PASSWORD=<mat-khau-moi-cho-anhu_app>      # chuỗi ngẫu nhiên dài, khác mật khẩu DB
   python scripts/migrate.py
   ```
   (macOS/Linux: thay `set` bằng `export`.)

   Script tạo role `anhu_app` (không superuser, không BYPASSRLS), cấu trúc bảng, RLS, trigger và quyền.
   - Script tự kiểm tra role chạy migration có đủ quyền không. Nếu không đủ, nó dừng với thông báo rõ ràng.
   - Chạy lại không sao: migration nào đã áp dụng sẽ bị bỏ qua.
4. Kiểm tra kết nối **bằng role ứng dụng**. Tên đăng nhập qua pooler có dạng `anhu_app.<project-ref>`:
   ```bash
   set ANHU_DATABASE_URL=postgresql://anhu_app.<project-ref>:<mat-khau-anhu_app>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   python scripts/check_connection.py
   ```
   Phải thấy `Role: anhu_app superuser=False bypassrls=False`, `SSL: True`, `Migration: đủ`, `KẾT QUẢ: ĐẠT`.

## 3. Triển khai trên Streamlit Community Cloud

1. share.streamlit.io → **Create app → Deploy a public app from GitHub**. Cấp quyền cho Streamlit đọc repo private.
2. **Repository**: `<ban>/anhu-studio`. **Branch**: `master`. **Main file path**: `app.py`.
3. **Advanced settings → Python version: 3.12.**
4. **Secrets** (Advanced settings, hoặc App settings → Secrets sau khi tạo). Dán nội dung sau, điền giá trị thật:
   ```toml
   ANHU_DATABASE_URL = "postgresql://anhu_app.<project-ref>:<mat-khau-anhu_app>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require"
   ANHU_BOOTSTRAP_TOKEN = "<chuoi-ngau-nhien-it-nhat-16-ky-tu>"
   ANHU_BOOTSTRAP_EXPIRES = "2026-12-31T23:59"
   ANHU_APP_URL = "https://<ten-app>.streamlit.app"
   # ANHU_ALLOWED_EMAILS = "co.a@...,co.b@..."
   ```
   - **Không** đưa `ANHU_ADMIN_DATABASE_URL` hay mật khẩu `postgres` vào đây.
   - Tạo chuỗi ngẫu nhiên: `python -c "import secrets; print(secrets.token_urlsafe(24))"`.
5. Bấm **Deploy**. Lần đầu mất vài phút để cài thư viện.

## 4. Giới hạn ai được mở ứng dụng

ANHU đã có đăng nhập riêng. Có thể thêm một lớp nữa: **App settings → Sharing → “Only specific people can view this app”**, rồi thêm email của hai giáo viên. Người lạ khi đó không thấy cả màn hình đăng nhập.
- Gói miễn phí được mô tả là giới hạn số ứng dụng **riêng tư**. Hãy kiểm tra trang giới hạn hiện hành của Streamlit.

## 5. Khởi tạo và sử dụng

1. Mở `https://<ten-app>.streamlit.app`, màn hình **Thiết lập lần đầu** sẽ hiện ra. Nhập `ANHU_BOOTSTRAP_TOKEN`, email, tên và mật khẩu.
2. Xóa `ANHU_BOOTSTRAP_TOKEN` khỏi Secrets. Bước khởi tạo đã tự khóa vĩnh viễn trong CSDL, xóa mã chỉ để gọn gàng hơn.
3. Mời giáo viên thứ hai: xem *docs/QUAN_TRI_TAI_KHOAN.md*.
4. Nếu có file Excel cũ: ⚙ **Cài đặt → Mẫu Excel & dữ liệu cũ → Nhập lịch sử**. File chỉ đi qua ứng dụng vào CSDL riêng, **không** vào GitHub.

## 6. Kiểm tra sau triển khai (bắt buộc)

- [ ] Đăng nhập được. Tải lại trang (F5) vẫn đăng nhập (ô “Ghi nhớ” được chọn).
- [ ] Giáo viên thứ hai kích hoạt bằng đường dẫn, đăng nhập được, và **không** thấy học viên của người kia.
- [ ] Thêm một ca thử → Chấm công hiện đúng → tải file Excel mở được.
- [ ] ⚙ Cài đặt → Hệ thống: số migration bằng số file trong `db/migrations/`.
- [ ] ⚙ Sao lưu: tải bản sao lưu → **Kiểm tra file** báo “File nguyên vẹn”.
- [ ] Streamlit Cloud → **Manage app → Logs**: không có email, mật khẩu hay tên học viên. Ứng dụng tắt hiển thị chi tiết lỗi cho người dùng (`showErrorDetails = "none"`).

## 7. Cập nhật phiên bản mà vẫn giữ dữ liệu

Dữ liệu nằm trong Supabase, không nằm trong mã. Cập nhật mã **không** làm mất dữ liệu.

1. Tải bản sao lưu: ⚙ Sao lưu, hoặc `python scripts/backup.py dump` với `ANHU_ADMIN_DATABASE_URL`.
2. Xem có migration mới không: `python scripts/migrate.py --status`.
3. Nếu có: chạy `python scripts/migrate.py` **trước** khi push mã mới. Migration chỉ thêm, không xóa cột đang dùng, nên mã cũ vẫn chạy được trên CSDL mới.
4. `git push`: Streamlit Cloud tự triển khai lại.
5. Nếu push mã mới mà quên migrate, ứng dụng **không** tự sửa CSDL. Nó hiện trang “Cơ sở dữ liệu cần cập nhật” và liệt kê các migration còn thiếu.

## 8. Đặc điểm của nền tảng miễn phí (cần biết)

| Đặc điểm | Ảnh hưởng | Cách xử lý |
|---|---|---|
| Ứng dụng “ngủ” sau khoảng 12 giờ không ai truy cập | Lần mở đầu tiên chờ khoảng 30–60 giây | Bấm nút đánh thức; dữ liệu không mất |
| Dự án Supabase miễn phí tạm dừng nếu không hoạt động nhiều ngày | Ứng dụng báo “Không kết nối được CSDL” | Mở Supabase → Restore project. Dùng hằng ngày thì không gặp |
| Tài nguyên giới hạn (RAM khoảng 1 GB) | Đủ cho 2 giáo viên | — |
| Mỗi tab trình duyệt là một phiên Streamlit | Mở tab mới vẫn đăng nhập nhờ “ghi nhớ thiết bị” | — |
| Tải lại trang luôn về trang **Tổng quan** | Không mất dữ liệu đang lưu | — |

## 9. Khi không muốn dùng Streamlit Cloud

Dự án vẫn có giao diện Flask (`anhu/web`), dùng chung toàn bộ CSDL và nghiệp vụ.
- Chạy trên bất kỳ hosting Python nào (Render, Railway, VPS…): `gunicorn "anhu.web:create_app()"` sau HTTPS.
- Cài `Flask` và `gunicorn` thêm vào `requirements.txt`.
- Chi tiết vận hành xem *docs/QUAN_TRI_VAN_HANH.md*. Lưu ý: Flask dùng luồng tài khoản cũ (mật khẩu tạm), chưa có lời mời và mã kích hoạt như bản Streamlit.

## 10. Sự cố

| Hiện tượng | Nguyên nhân thường gặp |
|---|---|
| “Chưa cấu hình cơ sở dữ liệu” | Thiếu `ANHU_DATABASE_URL` trong Secrets |
| “Không kết nối được cơ sở dữ liệu” | Dùng kết nối Direct (IPv6) thay vì Session pooler; sai mật khẩu; dự án Supabase đang tạm dừng |
| `password authentication failed for user "anhu_app"` qua pooler | Tên đăng nhập phải là `anhu_app.<project-ref>` |
| `migrate.py` báo thiếu BYPASSRLS | Đang chạy migration bằng role khác `postgres`. Dùng chuỗi kết nối của role `postgres` |
| “Cơ sở dữ liệu cần cập nhật” | Chạy `scripts/migrate.py` (mục 7) |
| `check_connection.py` báo `superuser=True` | Secrets đang dùng role `postgres`. Bắt buộc đổi sang `anhu_app`, nếu không RLS bị vô hiệu |
