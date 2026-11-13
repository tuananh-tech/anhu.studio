# Hướng dẫn quản trị & vận hành

> Từ bản 3.0: triển khai giao diện Streamlit theo *docs/DEPLOY_STREAMLIT.md*; tài khoản theo *docs/QUAN_TRI_TAI_KHOAN.md*. Tài liệu này vẫn đúng cho sao lưu toàn máy chủ, khôi phục, chuyển máy chủ và giao diện Flask dự phòng.

Tài liệu dành cho người cài đặt và vận hành máy chủ. Các thao tác nghiệp vụ hằng ngày **không** cần đến tài liệu này.

## 1. Kiến trúc

| Thành phần | Chi tiết |
|---|---|
| Web | Flask, render phía server, JS tối thiểu, CSP `default-src 'self'`. Chạy bằng WSGI, ví dụ `gunicorn "anhu.web:create_app()"`, đặt sau HTTPS. |
| Cơ sở dữ liệu | PostgreSQL 15+ (Supabase hoặc Postgres bất kỳ), là nguồn dữ liệu duy nhất. |
| Role `postgres`/admin | Chỉ dùng để chạy migration và sao lưu toàn máy chủ. |
| Role `anhu_app` | Ứng dụng dùng role này. Không phải superuser, không có BYPASSRLS. Mỗi transaction đặt `app.user_id`, nên RLS trong DB quyết định ai được đọc/ghi gì. |
| Bảo vệ ở tầng DB | Khóa kỳ, bắt buộc lý do khi mở kỳ, bản chốt bất biến, tối đa 2 tài khoản, chặn đổi chủ sở hữu, kiểm tra loại ca, nhật ký tự động. Tất cả đều là trigger, nên vẫn có hiệu lực khi có người sửa thẳng bằng SQL. |
| Xóa dữ liệu | Dữ liệu gốc không xóa cứng được: `anhu_app` không có quyền DELETE trên các bảng danh mục, kỳ chốt và đơn giá. |

### Biến môi trường

| Biến | Ý nghĩa |
|---|---|
| `ANHU_DATABASE_URL` | `postgresql://anhu_app:<pw>@host:5432/db`. Web dùng biến này. |
| `ANHU_ADMIN_DATABASE_URL` | Kết nối admin, **chỉ** dùng cho migrate, tạo tài khoản đầu tiên và sao lưu toàn bộ. |
| `ANHU_APP_DB_PASSWORD` | Mật khẩu của role `anhu_app` (dùng khi migrate). |
| `ANHU_SECRET_KEY` | Chuỗi ngẫu nhiên dài. Đổi khóa này thì mọi người phải đăng nhập lại. |
| `ANHU_ALLOWED_EMAILS` | Tùy chọn. Nếu đặt, chỉ các email này được đăng nhập. Để trống thì chỉ áp dụng giới hạn 2 tài khoản trong DB; khi đó quản trị viên thêm tài khoản trên web. |
| `ANHU_COOKIE_SECURE` | `1` (mặc định, bắt buộc khi dùng HTTPS). |
| `ANHU_DB_CONNECT_TIMEOUT` | Số giây chờ kết nối DB (mặc định 5). Quá thời gian này, người dùng thấy trang “Không kết nối được cơ sở dữ liệu” thay vì trang bị treo. |

Không đưa secrets, file Excel thật hay thư mục `private/` lên GitHub. `.gitignore` đã chặn sẵn.

## 2. Cài đặt lần đầu

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
ANHU_ADMIN_DATABASE_URL=... ANHU_APP_DB_PASSWORD=... python scripts/migrate.py
ANHU_ADMIN_DATABASE_URL=... python scripts/create_user.py co.a@example.com "Tên giáo viên"   # tài khoản đầu = quản trị
```

Tài khoản thứ hai: quản trị viên tạo trên web (*Cài đặt → Tài khoản*), không cần chạy script.

Kiểm tra sức khỏe: `GET /suc-khoe` trả `{"status":"ok"}` khi kết nối được DB, hoặc HTTP 503 khi mất kết nối. Có thể dùng địa chỉ này cho công cụ giám sát.

## 3. Sao lưu

| Loại | Ai làm | Cách làm | Tần suất khuyến nghị |
|---|---|---|---|
| Bản sao lưu theo tài khoản (.zip) | Giáo viên | Cài đặt → Sao lưu & khôi phục → Tải | Sau mỗi lần chốt kỳ |
| Bản sao lưu tài khoản (cả 2) | Quản trị | `python scripts/backup.py accounts` | Hằng tuần |
| Toàn bộ CSDL (`pg_dump`) | Quản trị | `python scripts/backup.py dump` (ghi kèm file `.sha256`) | Hằng ngày (cron), giữ 30 bản |
| Snapshot của Supabase | Supabase | Bật trong Dashboard | Theo gói dịch vụ |

- Lưu bản sao lưu ở **ít nhất 2 nơi**, trong đó có một nơi ngoài máy chủ.
- File chứa dữ liệu thật của học viên: lưu ở nơi riêng tư, có mã hóa nếu được.
- **Kiểm tra bản sao lưu** định kỳ, mỗi tháng một lần:
  - File của web: dùng *Cài đặt → Sao lưu → Kiểm tra*, hoặc `python scripts/backup.py verify file.zip`. Thao tác này kiểm tra mã băm và số dòng từng bảng.
  - Bản `pg_dump`: khôi phục thử vào một CSDL tạm (xem mục 4). Test tự động `test_full_server_dump_and_restore_keeps_data_and_rls` cũng làm đúng quy trình này.

## 4. Khôi phục / chuyển máy chủ

### Cách A: toàn bộ máy chủ (khuyến nghị khi chuyển máy chủ)

1. Trên máy chủ cũ, chạy `python scripts/backup.py dump`. Kết quả gồm file `.dump` và `.sha256`.
2. Trên máy chủ mới, tạo một CSDL **trống**.
3. Chạy `python scripts/backup.py restore-dump <file.dump> --target <admin-url-CSDL-mới>`. Lệnh này kiểm tra mã băm và chạy trong một transaction duy nhất.
4. Chạy `ANHU_ADMIN_DATABASE_URL=<mới> ANHU_APP_DB_PASSWORD=... python scripts/migrate.py`. Bước này tạo role `anhu_app` nếu chưa có, cấp lại quyền và áp dụng migration mới nếu có.
5. Trỏ `ANHU_DATABASE_URL` của web sang máy chủ mới rồi khởi động lại.
6. Kiểm tra:
   - `/suc-khoe` trả kết quả ok;
   - đăng nhập được;
   - *Chấm công → Kỳ chấm công*: mã băm các kỳ đã chốt trùng với máy chủ cũ.

### Cách B: từng tài khoản qua web

1. Trên máy chủ mới: migrate, rồi tạo tài khoản. Tài khoản phải **trống**.
2. Giáo viên đăng nhập, vào *Cài đặt → Sao lưu → Khôi phục*, chọn file .zip và gõ `KHOI PHUC`.
3. Toàn bộ khôi phục chạy trong một transaction:
   - ID được giữ nguyên;
   - các kỳ đã chốt và file Excel đã chốt được giữ nguyên mã băm;
   - nhật ký cũ được nhập lại;
   - sau khi ghi, hệ thống đếm lại từng bảng và so với bản sao lưu.

   Hệ thống từ chối khôi phục khi tài khoản đã có dữ liệu, khi file hỏng hoặc bị sửa, hoặc khi bản sao lưu đến từ phiên bản CSDL mới hơn.

## 5. Cập nhật phiên bản an toàn

1. **Sao lưu toàn bộ:** `python scripts/backup.py dump`.
2. Xem migration nào sẽ được chạy: `python scripts/migrate.py --status`.
3. Thử trên một bản sao:
   1. khôi phục bản dump vào CSDL tạm;
   2. chạy migrate trên CSDL tạm;
   3. chạy `pytest` (khoảng 1 phút). Test tự dựng Postgres riêng nên không đụng dữ liệu thật.
4. Chạy migrate trên CSDL thật. Mỗi file migration chạy trong một transaction: nếu lỗi thì file đó không được áp dụng gì, và CSDL giữ nguyên trạng thái cũ.
5. Triển khai mã nguồn mới, khởi động lại WSGI, kiểm tra `/suc-khoe` và *Cài đặt → Thông tin hệ thống*.
6. Nếu có sự cố: quay lại mã nguồn cũ. Migration chỉ **thêm** bảng/cột nên mã cũ thường vẫn chạy được. Nếu cần, khôi phục bản dump ở bước 1.

Quy ước khi viết migration mới:
- đặt tên `NNN_mo_ta.sql`;
- chỉ thêm, không sửa file đã phát hành;
- không xóa cột đang dùng;
- file `*_grants.sql` luôn được chạy lại sau cùng.

## 6. Sự cố thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| Trang “Không kết nối được cơ sở dữ liệu”, `/suc-khoe` trả 503 | Postgres còn chạy không, mật khẩu `anhu_app`, tường lửa, chuỗi kết nối. Kiểm tra trạng thái dự án Supabase (dự án miễn phí bị tạm dừng khi không dùng). |
| Đăng nhập báo “chưa được duyệt” | Email có trong `ANHU_ALLOWED_EMAILS` (nếu có đặt) và tài khoản chưa bị khóa. |
| Quá nhiều lần đăng nhập sai | Bị khóa 15 phút theo địa chỉ IP. Khởi động lại tiến trình web sẽ xóa bộ đếm. |
| Upload file báo quá lớn | File Excel tối đa 10 MB, bản sao lưu tối đa 25 MB (`MAX_CONTENT_LENGTH`). |
| Quên mật khẩu tài khoản quản trị | Tài khoản kia không có quyền quản trị. Chạy SQL bằng role admin: tạo hash bằng `python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash('MatKhauTam123'))"`, rồi `SET app.admin_op='1'; UPDATE app_users SET password_hash='<hash>', must_change_password=true WHERE email='...';` |

## 7. Môi trường phát triển

- `python scripts/dev.py --demo`: dựng Postgres nhúng trong `private/pgdata`, nạp dữ liệu **ẩn danh**.
- `python -m pytest`: chạy toàn bộ test.

Không dùng dữ liệu thật trong test, ảnh chụp màn hình hay repo công khai.
