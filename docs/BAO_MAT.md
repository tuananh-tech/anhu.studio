# Bảo mật: quyền truy cập, secrets, sao lưu

## 1. Mô hình quyền

| Lớp | Cơ chế | Bằng chứng kiểm thử |
|---|---|---|
| Nền tảng | (Tùy chọn) Streamlit Cloud “Only specific people can view” | Cấu hình khi triển khai |
| Ứng dụng | Đăng nhập email + mật khẩu argon2; phiên phía máy chủ, kiểm tra lại ở CSDL mỗi lần giao diện chạy lại | `test_accounts.py`, `test_streamlit_ui.py` |
| CSDL | Ứng dụng dùng role `anhu_app` (**không** superuser, **không** BYPASSRLS); mỗi transaction đặt `app.user_id`; **RLS + FORCE RLS** trên mọi bảng nghiệp vụ | `test_db_security.py`: truy vấn SQL trực tiếp bằng role ứng dụng, không qua giao diện |
| Bảng tài khoản | `account_invites`, `app_sessions`, `login_attempts`, `app_meta`: role ứng dụng **không có quyền đọc/ghi trực tiếp** (permission denied); chỉ đi qua hàm SECURITY DEFINER có kiểm tra quyền | `test_admin_cannot_read_teacher_data` |
| Toàn vẹn | Trigger chặn: sửa kỳ đã chốt, mở kỳ không lý do, sửa bản chốt, đổi chủ dữ liệu, gắn dữ liệu chéo tài khoản, quá 2 tài khoản, loại ca lạ, tự nâng quyền | `test_db_security.py`, `test_long_term.py` |

**Hai giáo viên không xem được dữ liệu của nhau**, kể cả quản trị viên, trừ khi chủ dữ liệu chủ động chia sẻ. Đã kiểm chứng ở ba mức:
- SQL trực tiếp bằng role ứng dụng;
- giao diện Streamlit (AppTest);
- trình duyệt thật: giáo viên thứ hai không thấy học viên của quản trị.

## 2. Tài khoản

- **Khởi tạo quản trị một lần**:
  - cần `ANHU_BOOTSTRAP_TOKEN` (so sánh hằng thời gian, có thể đặt hạn);
  - CSDL khóa bảng khi khởi tạo, nên nhiều yêu cầu đồng thời thì chỉ đúng một yêu cầu thành công (đã test với 4 luồng);
  - sau khi xong, cờ `bootstrap_done` khóa vĩnh viễn.
- **Lời mời / mã đặt lại**:
  - 80 bit ngẫu nhiên; CSDL chỉ lưu SHA-256;
  - dùng một lần, có hạn 1–168 giờ;
  - phải khớp email;
  - mã chỉ hiển thị một lần cho quản trị;
  - người dùng tự đặt mật khẩu, quản trị không biết.
- **Phiên đăng nhập**:
  - mã phiên nằm ở máy chủ (session state), không nằm trong URL hay cookie;
  - hết hạn sau 12 giờ, hoặc 2 giờ không dùng;
  - bị thu hồi khi: đăng xuất, khóa tài khoản, quản trị thu hồi, đặt lại mật khẩu, hoặc người dùng chọn “đăng xuất thiết bị khác”.
- **Ghi nhớ thiết bị** (tùy chọn, mặc định bật, bỏ chọn khi dùng máy chung):
  - trình duyệt giữ một mã ngẫu nhiên 256 bit trong `localStorage`;
  - CSDL chỉ lưu SHA-256 của mã, gắn với một phiên, nên mã chịu đủ các giới hạn hết hạn và thu hồi của phiên;
  - đăng xuất sẽ xóa mã.
  - Rủi ro còn lại: mã đọc được bởi JavaScript chạy trên trang ứng dụng. Ứng dụng không hiển thị HTML do người dùng nhập mà không thoát ký tự; Streamlit cũng lọc markdown.
- **Chống dò mật khẩu**: sai 8 lần / 15 phút thì email đó bị tạm khóa. Bộ đếm lưu trong CSDL nên đúng cả khi có nhiều tiến trình.
- **Không** có tài khoản hay mật khẩu mặc định. **Không** hiển thị lại mật khẩu, kể cả mật khẩu đang dùng.

## 3. Dữ liệu đầu vào

- **Upload Excel**:
  - chỉ nhận `.xlsx`, kiểm tra chữ ký ZIP, giới hạn 10 MB;
  - đọc với `defusedxml`, nên file chứa thực thể ngoài (XXE) bị từ chối (đã test);
  - ô công thức trong file tải lên không được thực thi; chỉ đọc giá trị đã lưu.
- **Xuất Excel**:
  - chống *formula injection*: ô dữ liệu người dùng bắt đầu bằng `=` được ép thành chữ (đã test với `=CMD|…`);
  - riêng hàng TỔNG và hàng tiền của mẫu gốc vẫn giữ công thức.
- **Hiển thị**: mọi dữ liệu người dùng đưa vào bảng HTML đều qua `html.escape`.
- **SQL**: mọi câu lệnh dùng tham số (SQLAlchemy `text()` + bind). Không ghép chuỗi giá trị người dùng vào SQL.

## 4. Secrets

| Secret | Ở đâu | Không bao giờ |
|---|---|---|
| `ANHU_DATABASE_URL` (role `anhu_app`) | Streamlit Cloud → Secrets; cục bộ: `.streamlit/secrets.toml` hoặc biến môi trường | Commit lên GitHub |
| `ANHU_BOOTSTRAP_TOKEN` | Secrets, **xóa** sau khi khởi tạo | Gửi qua kênh công khai |
| Mật khẩu `postgres` / `ANHU_ADMIN_DATABASE_URL` | Chỉ trên máy quản trị khi chạy migrate / sao lưu | Đưa vào Streamlit Secrets |

`.gitignore` chặn: `private/`, `.streamlit/secrets.toml`, `.env*` (trừ `.env.example`), `*.zip`, `*.dump`, mọi file Excel (trừ fixture ẩn danh).
- Đã quét các file sẽ commit: không có chuỗi kết nối kèm mật khẩu thật, không có tên học viên thật từ file gốc.
- Dữ liệu test và ảnh chụp chỉ dùng tên giả hoặc mã HVxx.

## 5. Log

- Streamlit đặt `showErrorDetails = "none"`: người dùng chỉ thấy thông báo chung, không thấy traceback.
- Log máy chủ sau toàn bộ kiểm thử trình duyệt đã được kiểm tra: **0** dòng chứa email, mật khẩu, tên học viên hay mã kích hoạt.
- Ứng dụng không chủ động ghi dữ liệu cá nhân ra log. Khi kết nối CSDL lỗi, chỉ ghi loại lỗi, không ghi chuỗi kết nối.

## 6. Sao lưu

- **Giáo viên**: ⚙ Sao lưu → tải bản sao lưu `.zip`, gồm manifest, SHA-256 và số dòng từng bảng. **Kiểm tra file** trước khi cần dùng.
  - Khôi phục chỉ chạy vào **tài khoản trống**, trong một transaction duy nhất, rồi đếm lại để đối chiếu.
  - Khôi phục giữ nguyên mã băm của các file đã chốt.
- **Quản trị**: `scripts/backup.py dump`, chạy hằng ngày bằng cron hoặc Task Scheduler. Kiểm tra khôi phục mỗi quý theo *docs/QUAN_TRI_VAN_HANH.md*.
- File sao lưu chứa dữ liệu thật: lưu ở nơi riêng tư, ít nhất 2 nơi, không gửi công khai.

## 7. Rủi ro còn lại và khuyến nghị

1. **Chưa kiểm thử trên Supabase và Streamlit Cloud thật.** Phải chạy `scripts/check_connection.py` và làm checklist ở `DEPLOY_STREAMLIT.md` mục 6.
2. **Chưa có xác thực hai lớp (2FA).** Khuyến nghị bật lớp “Only specific people can view” của Streamlit Cloud, vì lớp này dùng tài khoản Google/GitHub có 2FA của bạn.
3. Mã “ghi nhớ thiết bị” nằm trong `localStorage`. Trên máy chung, hãy bỏ chọn khi đăng nhập.
4. Giới hạn số lần đăng nhập sai áp dụng theo email, không theo địa chỉ IP.
