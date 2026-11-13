# Đánh giá & tối ưu thao tác (UX)

Đánh giá trên giao diện Streamlit mới, so với giao diện Flask trước đó. Số liệu đo từ kịch bản trình duyệt thật (Playwright) và từng bước thao tác.
- **Nhấp:** số lần chạm hoặc bấm, không tính gõ phím.
- **Trang:** số lần chuyển sang trang khác.
- **Trường:** số ô phải nhập.

## 1. Mười tình huống thực tế

| # | Tình huống | Trước (Flask) | Sau (Streamlit) | Ghi chú |
|---|---|---|---|---|
| 1 | Xem hôm nay dạy ca nào | 0 nhấp; danh sách nằm dưới 6 ô số liệu | **0 nhấp**; “Hôm nay” là mục **đầu tiên** sau lời chào | Trên điện thoại không phải cuộn |
| 2 | Báo nghỉ một ca | 2 nhấp + 1 trang (mở ca → chọn lý do → lưu) | **3 nhấp, 0 trang** (Điều chỉnh → Báo nghỉ → Báo nghỉ ca này); lý do mặc định “Giáo viên nghỉ” | Làm ngay trên thẻ ca. Hoàn tác: **Khôi phục ca** |
| 3 | Đổi giờ một học viên (hằng tuần) | 4 nhấp + 1 trang, 2 trường | **4 nhấp, 0 trang, 2 trường** (Lịch cố định → mở lịch → giờ → Áp dụng) | Đổi một buổi: Điều chỉnh → Đổi giờ/ngày (3 nhấp) |
| 4 | Thêm học viên mới | 3 nhấp; tự nghĩ mã hoặc để trống | **3 nhấp, 1 trường** (chỉ tên; mã HV0001… tự sinh) | Trường không bắt buộc ẩn đi |
| 5 | Xác nhận tất cả ca đã dạy | 4 nhấp + 2 trang | **3 nhấp** từ Tổng quan (Xác nhận N ca → Xem trước → Xác nhận) | Có bước xem trước, theo đúng yêu cầu |
| 6 | Xem số giờ dạy trong tháng | 0 | **0** (ô “Giờ dạy T11” ở Tổng quan) | — |
| 7 | Xem bảng công theo Excel | 1 nhấp | **1 nhấp** | Bảng giữ header 2 tầng, cột NGÀY cố định khi cuộn ngang |
| 8 | So sánh bảng trung tâm | 6 nhấp + 3 trang, ghép 11 cột | **4 nhấp, 0 trang**; ghép cột tự gợi ý; **lần sau tự nhớ hồ sơ ánh xạ** | Bằng chứng ghi kèm từng chênh lệch |
| 9 | Thêm tài khoản người dùng thứ hai | Quản trị đọc mật khẩu tạm cho người kia; người kia phải đổi mật khẩu | Quản trị: **1 nhấp, 2 trường** (mục mở sẵn khi chưa đủ 2 tài khoản) → gửi **đường dẫn có sẵn mã**. Người dùng: mở link → 2 trường → Kích hoạt | Quản trị không bao giờ biết mật khẩu người dùng |
| 10 | Sửa đơn giá tháng sau | 2 biểu mẫu (kết thúc giá cũ + thêm giá mới), 6 trường | **1 thao tác “Đổi giá từ ngày”**: ngày mặc định = đầu tháng sau, chỉ nhập số tiền | Giá cũ tự kết thúc vào hôm trước; lịch sử giữ nguyên |

## 2. Góc nhìn từng vai

**Giáo viên không biết lập trình**
- *Trước:* phải hiểu “ca dự kiến” với “buổi dạy” là hai trang khác nhau; Cài đặt dài một trang.
- *Sau:* chỉ một khái niệm “ca” với nút **✓ Đã dạy**. Trạng thái hiện bằng nhãn màu: Chờ xác nhận / Đã dạy / Nghỉ / Cần kiểm tra. Trang nào cũng có “Hướng dẫn nhanh” gập sẵn.

**Giáo viên dùng điện thoại** (kiểm thử ở 390×844, cảm ứng)
- *Lỗi tìm thấy và đã sửa:*
  1. Menu chính của Streamlit bị thu vào nút `>>` khó thấy. Đã thêm **thanh 5 mục + ⚙ cố định** ngay dưới đầu trang (cao 31px, không tràn ngang).
  2. Tổng quan có 6 ô số liệu xếp dọc, phải cuộn rất xa. Đã chuyển sang **lưới 3×2 gọn** và đưa ca hôm nay lên đầu.
  3. Các nút ◀ / ▶ / Hôm nay và nút trên thẻ ca bị xếp chồng thành 4 hàng. Đã chuyển sang **hàng ngang**.
  4. **Tải lại trang hoặc chuyển ứng dụng làm mất đăng nhập.** Đã thêm **ghi nhớ thiết bị** (an toàn, có hạn, thu hồi được).
  5. Tiêu đề phụ to hơn tiêu đề trang. Đã chỉnh cỡ chữ.
- *Đã kiểm:* không trang nào tràn ngang ở 390px.

**Quản trị viên cấu hình lần đầu**
- Màn hình **Thiết lập lần đầu** tự xuất hiện khi CSDL trống, rồi biến mất vĩnh viễn.
- Cài đặt chỉ còn **một ô chọn mục**; trong mỗi mục, phần ít dùng được gập lại.
- Tổng quan liệt kê việc cần làm, ví dụ “Quy tắc chưa xác nhận”, “Chưa có đơn giá”.

**Người đối soát cuối tháng**
- Từ Tổng quan, nút **Đối soát (N)** dẫn thẳng tới chênh lệch chờ duyệt.
- Mỗi mục hiện ngay bằng chứng (hàng trong file trung tâm so với ca của mình) và chọn được quyết định tại chỗ.
- Chốt kỳ cần tick “Tôi đã kiểm tra xong” để tránh bấm nhầm. Mở kỳ bắt buộc ghi lý do.

**Chuyên gia UX độc lập (tự phản biện)**
- Đã bỏ: nút In riêng (dùng chức năng in của trình duyệt), trang chi tiết riêng cho từng ca (thay bằng thao tác ngay trên thẻ), trang “Đồng bộ” trống. Mục “Hệ thống” chỉ còn thông tin cần thiết.
- **Không** thêm biểu đồ trang trí. Số liệu là chữ và bảng, xuất được Excel.
- Giữ một bước xác nhận cho thao tác khó hoàn tác (chốt kỳ, khôi phục sao lưu); các thao tác khác không hỏi lại.

**Chuyên gia bảo mật / kiểm thử:** xem *docs/BAO_MAT.md*.

## 3. Khả năng hoàn tác / sửa sai

| Thao tác | Cách sửa |
|---|---|
| Xác nhận nhầm | Sửa buổi đã dạy → **Hủy xác nhận** (cần lý do; buổi đó không bị xóa, chỉ chuyển sang trạng thái hủy xác nhận) |
| Báo nghỉ nhầm | **Khôi phục ca** |
| Lưu học viên sai | Sửa hồ sơ; lưu trữ hoặc khôi phục |
| Đơn giá sai | Bỏ xác nhận / lưu trữ đơn giá |
| Quy tắc sai | Tạo phiên bản mới; các phiên bản cũ vẫn giữ |
| Chốt kỳ sai | Mở kỳ (có lý do) → sửa → chốt lại thành phiên bản mới |
| Bấm trùng một nút | Hệ thống bỏ qua lần bấm thứ hai trong 5 giây nếu cùng dữ liệu (đã test: không tạo 2 ca) |

## 4. Còn tồn tại (không nghiêm trọng)

- Tải lại trang luôn về **Tổng quan**, không về trang đang mở. Đây là giới hạn của cách Streamlit khởi tạo phiên. Dữ liệu không mất.
- Bảng “Chờ xác nhận” dùng ô tick của `st.data_editor`; trên điện thoại ô tick khá nhỏ. Có thể dùng phần “Xác nhận từng ca” bên dưới thay thế.
- Nút chọn chế độ (Hôm nay/Tuần/…) xuống 2 dòng trên điện thoại hẹp.
- Ứng dụng miễn phí “ngủ” sau khoảng 12 giờ không ai truy cập; lần mở đầu chờ khoảng 30–60 giây.

## 5. Ảnh chụp

Đã chụp trên trình duyệt thật. Ảnh **không** được commit vì có thể chứa dữ liệu; tất cả đều là dữ liệu giả hoặc ẩn danh.
- *Trước* (Flask): bộ `shots2/`.
- *Sau* (Streamlit): bộ `shots_st/`, gồm khởi tạo, Tổng quan, Học viên, lịch cố định, xem trước xác nhận, lịch tuần, bảng công, ghép cột, kết quả đối soát, báo cáo, lời mời; mobile: kích hoạt, Tổng quan, Học viên, lịch hôm nay, bảng công, báo cáo.
