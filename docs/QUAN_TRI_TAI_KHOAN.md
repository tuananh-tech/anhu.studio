# Quản trị tài khoản

Tài liệu dành cho quản trị viên, cũng là một trong hai giáo viên.

## Nguyên tắc

| Nguyên tắc | Chi tiết |
|---|---|
| Không có tài khoản / mật khẩu mặc định | Quản trị viên đầu tiên tạo bằng **mã khởi tạo**, đặt trong secrets của máy chủ. |
| Tối đa 2 tài khoản hoạt động | CSDL chặn tài khoản thứ ba, kể cả khi thao tác thẳng bằng SQL. |
| Quản trị không biết mật khẩu người khác | Người dùng tự đặt mật khẩu khi kích hoạt. Quản trị chỉ cấp **mã một lần**. |
| Quản trị không xem dữ liệu dạy học của người kia | Trừ khi người đó chủ động **chia sẻ**. Quyền này do Row Level Security trong CSDL đảm bảo, không chỉ do giao diện ẩn nút. |
| Mọi việc quản trị đều được ghi lại | Ghi vào **Nhật ký quản trị**: ai làm, lúc nào, việc gì. Không ghi mật khẩu hay mã. |

## 1. Khởi tạo quản trị viên đầu tiên (một lần)

1. Người triển khai đặt `ANHU_BOOTSTRAP_TOKEN` trong secrets. Đây là chuỗi ngẫu nhiên ≥ 16 ký tự; có thể đặt thêm hạn dùng `ANHU_BOOTSTRAP_EXPIRES`.
2. Mở website. Màn hình **Thiết lập lần đầu** chỉ xuất hiện khi CSDL **chưa có tài khoản nào**.
3. Nhập mã khởi tạo, email, tên và mật khẩu, rồi bấm **Tạo tài khoản quản trị**.
4. Từ lúc này bước khởi tạo bị **khóa vĩnh viễn** trong CSDL. Không bật lại được, kể cả khi biết mã, và hai người làm cùng lúc thì chỉ một người thành công. Nên xóa `ANHU_BOOTSTRAP_TOKEN` khỏi secrets.

## 2. Thêm giáo viên thứ hai

1. ⚙ **Cài đặt → Tài khoản → ➕ Thêm tài khoản giáo viên**.
2. Nhập email và tên, chọn thời hạn mã (24 giờ – 7 ngày). Chỉ tick **Cấp quyền quản trị** khi thật sự cần.
3. Bấm **Tạo lời mời**. Màn hình hiện **mã kích hoạt** và **đường dẫn kích hoạt** (nếu đã đặt `ANHU_APP_URL`) **một lần duy nhất**.
4. Gửi đường dẫn cho giáo viên qua kênh riêng, ví dụ Zalo hoặc tin nhắn.
5. Giáo viên mở đường dẫn, tự đặt mật khẩu và bấm **Kích hoạt**. Mã chỉ dùng được một lần và hết hạn theo thời gian đã chọn.

Mã bị thất lạc: vào **Lời mời & mã đã tạo**, bấm **Thu hồi**, rồi tạo lời mời mới.

## 3. Các thao tác với một tài khoản

Vào **Cài đặt → Tài khoản → ⚙ [tên giáo viên]**:

| Thao tác | Tác dụng |
|---|---|
| **Khóa tài khoản** | Người đó không đăng nhập được; mọi phiên đang mở bị đăng xuất ngay. Dữ liệu giữ nguyên. **Mở khóa** để dùng lại. |
| **Thu hồi phiên đăng nhập** | Đăng xuất người đó khỏi mọi thiết bị, kể cả thiết bị đã “ghi nhớ đăng nhập”. Dùng khi mất điện thoại. |
| **Cấp / bỏ quyền quản trị** | Hệ thống luôn giữ ít nhất một quản trị viên hoạt động. |
| **Tạo mã đặt lại quyền truy cập** | Cấp mã một lần (24 giờ) để người đó tự đặt mật khẩu mới. Khi mã được dùng, mọi phiên cũ của người đó bị thu hồi. |

## 4. Tự quản lý tài khoản của mình

- **Hồ sơ & mật khẩu của tôi**: đổi tên hiển thị và mật khẩu.
- **Thiết bị đang đăng nhập**: xem các thiết bị đang đăng nhập. Bấm **Đăng xuất mọi thiết bị khác** khi nghi ngờ có người khác đang dùng tài khoản.
- **Chia sẻ dữ liệu**: cho giáo viên kia **chỉ xem**, hoặc **xem và sửa**, dữ liệu của mình. Có thể **Ngừng** bất kỳ lúc nào.

## 5. Bảo mật đăng nhập

- Mật khẩu ≥ 10 ký tự, lưu dạng băm argon2. Không ai, kể cả quản trị, xem lại được mật khẩu.
- Sai mật khẩu 8 lần trong 15 phút thì email đó bị tạm khóa đăng nhập 15 phút. Bộ đếm lưu trong CSDL.
- Phiên đăng nhập hết hạn sau 12 giờ, hoặc sau 2 giờ không dùng.
- Mã kích hoạt / đặt lại: 80 bit ngẫu nhiên. CSDL chỉ lưu SHA-256 của mã, không lưu mã gốc.
- Quản trị máy chủ có thể giới hạn danh sách email được đăng nhập bằng `ANHU_ALLOWED_EMAILS` (không bắt buộc).

## 6. Mất hết quyền quản trị

Trường hợp này xảy ra khi tài khoản quản trị duy nhất bị khóa hoặc quên mật khẩu, mà không còn quản trị viên nào khác. Người quản trị máy chủ chạy SQL bằng role `postgres` (xem *docs/QUAN_TRI_VAN_HANH.md*, mục Sự cố), hoặc tạo mã đặt lại bằng SQL. Không có “cửa sau” nào trên website.
