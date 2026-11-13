# Báo cáo nghiệm thu bản phát hành — ANHU Teaching Studio 3.0 (Streamlit)

Ngày kiểm thử: 13/11/2026.
- Máy: Windows 11, Python 3.12, PostgreSQL 16 (bản nhúng, chỉ dùng cho test).
- Trình duyệt: Chromium (Playwright), giao diện Streamlit 1.67.0.
- Ứng dụng luôn kết nối bằng role `anhu_app`: không superuser, không BYPASSRLS.
- Dữ liệu dùng khi kiểm thử là dữ liệu giả hoặc ẩn danh.
- **Không** đụng tới `private/pgdata`: mọi kiểm thử trình duyệt chạy trên cụm Postgres tạm riêng.

## 1. Kết luận ngắn

| Điều kiện nghiệm thu | Trạng thái |
|---|---|
| Giao diện đơn giản, dễ dùng (desktop + điện thoại) | **Đạt.** Kiểm thử trình duyệt thật; 9 vấn đề UX đã sửa (xem UX_REVIEW.md) |
| Nghiệp vụ cốt lõi hoạt động | **Đạt.** 169 test tự động + 26 kiểm tra trên trình duyệt |
| Admin tạo tài khoản mới từ web | **Đạt.** Lời mời một lần → người dùng tự đặt mật khẩu |
| Bảo mật dữ liệu từng giáo viên | **Đạt.** RLS kiểm chứng bằng SQL trực tiếp, AppTest và trình duyệt |
| Bảng công khớp quy tắc **đã xác nhận** | **Đạt về kỹ thuật.** Quy tắc 30 PHÚT / DẠY THỬ và đơn giá **vẫn chờ chủ dự án xác nhận** |
| Excel xuất đúng mẫu | **Đạt.** Header gốc, đủ ngày, golden-master 259/259 ô; có chống chèn công thức |
| Sử dụng đồng thời | **Đạt.** Hai phiên (desktop + mobile) cùng ghi một lúc, cả hai thành công, không lẫn dữ liệu |
| Sao lưu & khôi phục | **Đạt.** Khôi phục vào CSDL mới khớp từng ô; dump/restore toàn máy chủ |
| Chạy được trên môi trường triển khai đã chọn | **Đạt trên môi trường tương đương** (venv sạch + Postgres + `streamlit run`; đủ wheel Linux). **Chưa chạy trên Streamlit Cloud và Supabase thật**, vì cần tài khoản của bạn |
| Không còn lỗi nghiêm trọng | **Đạt.** Mọi lỗi tìm thấy trong vòng kiểm thử đã sửa (mục 4) |
| Hướng dẫn cho người không chuyên | **Đạt.** HUONG_DAN_SU_DUNG.md, QUAN_TRI_TAI_KHOAN.md |

**Chưa thể tuyên bố “đã chạy trên Internet”.** Phần còn lại là các bước cần tài khoản của bạn; checklist ở DEPLOY_STREAMLIT.md mục 6.

## 2. Có chuyển sang Streamlit thật không?

**Có.** Giao diện viết lại bằng Streamlit thật (`anhu/ui/*`), **không** bọc Flask bên trong. Dùng các cơ chế riêng của Streamlit:
- `st.navigation`, `st.form`, `st.popover`, `st.dialog`, `st.data_editor`, `st.segmented_control`;
- `st.session_state` cho phiên;
- `st.components.v2` cho tính năng ghi nhớ thiết bị.

Được giữ nguyên **không sửa logic**:
- schema và migration 001–004;
- toàn bộ `anhu/domain`;
- `anhu/services` (chỉ bổ sung);
- quy tắc tính công, golden-master, RLS, chốt kỳ, sao lưu.

Giao diện Flask vẫn còn, chạy được và có test, làm phương án hosting thay thế.

Kiểm soát việc Streamlit chạy lại toàn bộ script sau mỗi tương tác (rerun):
- `app.py` **không** migrate, không tạo user, không import khi chạy lại. Nó chỉ *đọc* `schema_migrations`, và nếu thiếu migration thì hiện trang hướng dẫn.
- Mọi thao tác ghi đi qua `act()`: chỉ chạy khi bấm nút, chặn bấm trùng (cùng thao tác, cùng dữ liệu, trong 5 giây), dịch lỗi CSDL sang tiếng Việt.
- Thêm vào đó là các ràng buộc duy nhất ở CSDL.
- Đã test: bấm “Lưu ca” hai lần liền → chỉ 1 ca.
- Phát hiện và sửa lỗi bộ nhớ đệm: tạo lịch cố định xong mà ca dự kiến không hiện trong cùng phiên.

## 3. Bằng chứng kiểm thử

### 3.1 Tự động: `python -m pytest` → **169 passed** (khoảng 49 giây)

| Nhóm | Số test | Nội dung |
|---|---|---|
| V1 + 2.0 (giữ nguyên) | 152 | Domain, golden-master Excel, RLS/DB, luồng nghiệp vụ, Flask HTTP, quản trị, sao lưu/khôi phục |
| `test_accounts.py` (mới) | 10 | Khởi tạo một lần (sai mã / hết hạn / lần 2 bị chặn / **4 luồng đồng thời → đúng 1 quản trị**); lời mời một lần (sai mã, sai email, dùng lại, thường dân gọi hàm quản trị → `ADMIN_ONLY`); quản trị **không** đọc được dữ liệu và bảng tài khoản; thu hồi phiên, khóa, mã đặt lại; chống dò mật khẩu lưu trong DB; mã HV tự sinh; nhập/xuất Excel học viên (trùng mã/tên, ngày lỗi, **chống chèn công thức**); gắn tên cũ (kỳ đã chốt giữ nguyên); báo cáo theo giáo viên; ghi nhớ thiết bị gắn với phiên; **XXE** bị chặn; migrate từ chối role thiếu BYPASSRLS |
| `test_streamlit_ui.py` (mới) | 7 | Giao diện Streamlit thật qua `AppTest`: khởi tạo → đăng nhập; **mọi trang + mọi chế độ + 10 mục Cài đặt** hiển thị không lỗi; thêm trung tâm/cơ sở/học viên/ca; bộ đếm “= 4 học viên” cho “Phúc, My, My, Quỳnh”; chặn bấm trùng; lịch cố định → xác nhận nhiều ca có xem trước → báo nghỉ; mời → kích hoạt → phân quyền; thu hồi phiên; chốt kỳ; mất kết nối CSDL |

Không sửa test để che lỗi. Bốn chỗ test được điều chỉnh, và cả bốn đều do giao diện thay đổi có chủ đích:
- số migration so theo số file (trước đây ghi cứng là 4);
- Tổng quan đổi sang lưới số liệu HTML;
- id lựa chọn lấy từ CSDL (AppTest trả về nhãn);
- tick ô xác nhận trước khi bấm Chốt kỳ.

### 3.2 Trình duyệt thật: **26/26 kiểm tra đạt**

Chạy `streamlit run app.py` trên CSDL sạch, mất khoảng 100 giây; 0 exception, 0 lỗi JavaScript.

**Desktop 1366×900 (quản trị viên):**
- mã khởi tạo sai bị từ chối;
- khởi tạo quản trị → đăng nhập;
- thêm trung tâm và cơ sở;
- thêm học viên, mã HV0001 tự sinh;
- bộ đếm tự động = 4;
- thêm và xác nhận ca phát sinh;
- tạo lịch cố định 7 ngày;
- xác nhận 8 ca có xem trước;
- bảng công đủ ngày;
- xuất Excel, kiểm tra header gốc `NGÀY ` / `1 HV`;
- tải chính file đó lên Đối soát → ghép cột tự gợi ý → **9 khớp, 0 chênh lệch**;
- xuất báo cáo Excel;
- tạo lời mời: mã và đường dẫn hiện **một lần**;
- tải lại trang vẫn đăng nhập;
- đăng xuất rồi tải lại trang thì **không** tự đăng nhập.

**Điện thoại 390×844, cảm ứng (giáo viên thứ hai, mở song song):**
- đường dẫn kích hoạt điền sẵn mã;
- tự đặt mật khẩu → đăng nhập;
- thanh điều hướng 5 mục + ⚙ nằm trên một hàng;
- **không thấy học viên của quản trị**;
- thêm học viên (HV0001 riêng của mình);
- tải lại trang vẫn đăng nhập;
- thêm địa điểm dạy riêng và ca;
- **hai người lưu ca cùng lúc → cả hai thành công**;
- không trang nào tràn ngang;
- đăng xuất → đăng nhập lại, dữ liệu còn nguyên;
- quản trị **thu hồi phiên** → tải lại trang thì về màn hình đăng nhập (dù có mã ghi nhớ).

**Môi trường sạch:**
- venv mới chỉ cài `requirements.txt`, không có Flask, pgserver hay pytest;
- `streamlit run` trên CSDL mới: khởi tạo, đăng nhập, cả 6 trang không lỗi.

**Linux:** máy này không có WSL hay Docker nên **không chạy được Linux thật**. Thay vào đó, `pip download --platform manylinux_2_28_x86_64 --python-version 3.12 --only-binary=:all:` giải đủ **44/44** gói (psycopg-binary, argon2, numpy, pandas, pyarrow, sqlalchemy… đều có wheel Linux). Mã không dùng API riêng của Windows.

**Log:** log máy chủ sau toàn bộ kiểm thử trình duyệt có **0** dòng chứa email, mật khẩu, tên học viên hay mã kích hoạt.

## 4. Lỗi tìm thấy trong vòng này và đã sửa

1. Thiếu import `seg` ở trang Học viên: trang bị lỗi khi mở. Được AppTest phát hiện.
2. Lịch cố định mới không sinh ca trong cùng phiên, do bộ nhớ đệm `_ensured` không được xóa sau thao tác ghi.
3. Sau khi kích hoạt tài khoản, ứng dụng lỗi vì sửa state của widget đã tạo (`StreamlitWidgetAlreadyInstantiatedError`).
4. **Chèn công thức Excel:** tên bắt đầu bằng `=` bị xuất thành công thức. Đã ép kiểu chuỗi cho mọi ô dữ liệu người dùng.
5. Tải lại trang hoặc chuyển ứng dụng trên điện thoại làm mất đăng nhập. Đã thêm ghi nhớ thiết bị an toàn.
6. Menu chính bị ẩn trên điện thoại. Đã thêm thanh điều hướng mobile.
7. Tổng quan quá dài trên điện thoại; các nút xếp chồng; tiêu đề phụ quá to.
8. `requirements.txt` có chú thích tiếng Việt có dấu làm `pip` trên Windows lỗi (cp1252). Đã chuyển sang ASCII.
9. `use_container_width` của Streamlit đã lỗi thời. Đã thay toàn bộ bằng `width=`.
10. Migration chưa kiểm tra role có BYPASSRLS. Thiếu quyền này thì đăng nhập sẽ hỏng âm thầm trên một số nền tảng. Đã thêm bước kiểm tra trước (preflight) và test.

## 5. Trả lời 10 câu hỏi bàn giao

1. **Giao diện thay đổi thế nào:**
   - Chuyển sang Streamlit; giữ 5 mục chính + ⚙ Cài đặt.
   - Thao tác ngay trên thẻ ca (✓ Đã dạy / Điều chỉnh / Dạy bù); Tổng quan đưa ca hôm nay lên đầu.
   - Điện thoại có thanh điều hướng riêng.
   - Cài đặt gọn trong một ô chọn mục.
   - Tài khoản dùng lời mời và mã một lần.
   - Học viên có mã tự sinh, nhập/xuất Excel, gắn tên cũ.
   - Báo cáo có thêm theo tháng và theo giáo viên.
2. **Giữ nguyên:**
   - schema, RLS, trigger, quy tắc xếp cột, golden-master;
   - chốt kỳ / bản chốt bất biến;
   - đối soát, import lịch sử không nhân đôi;
   - sao lưu / khôi phục, nhật ký;
   - giao diện Flask (dự phòng).
3. **Có chuyển sang Streamlit thật:** có (mục 2).
4. **Số test đạt:** 169/169 tự động, cộng 26/26 kiểm tra trên trình duyệt thật.
5. **Luồng đã kiểm thử trên trình duyệt:** xem mục 3.2.
6. **Dùng được trên Streamlit Community Cloud chưa:** mã và cấu hình đã sẵn sàng, đã kiểm thử trên môi trường tương đương. Còn chờ bạn tạo tài khoản và làm theo DEPLOY_STREAMLIT.md, rồi chạy checklist mục 6.
   - Rủi ro cần kiểm khi triển khai thật:
     - kết nối qua Supabase *Session pooler* bằng role `anhu_app.<ref>`;
     - role `postgres` của Supabase có BYPASSRLS (migrate tự kiểm tra và dừng nếu không có).
7. **File entrypoint:** `app.py` ở thư mục gốc. Trên Streamlit Cloud: *Main file path* = `app.py`, Python 3.12.
8. **File được đưa lên GitHub:** kết quả `git add -n .` là 120 file, gồm:
   - mã nguồn `anhu/`, `app.py`, `db/migrations/`, `scripts/`, `tests/`, `docs/`;
   - `requirements*.txt`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`, `.env.example`, `.gitignore`, `pytest.ini`, `README.md`;
   - file Excel duy nhất `tests/fixtures/anon_source.xlsx` (đã ẩn danh).

   **Không** có `private/`, `.venv/`, secrets hay bản sao lưu. Chưa commit, chưa push.
9. **Các bước để đưa lên Internet:** DEPLOY_STREAMLIT.md mục 1→6.
   1. Push lên repo private.
   2. Tạo Supabase.
   3. `migrate.py`.
   4. `check_connection.py`.
   5. Tạo app trên Streamlit Cloud, khai báo Secrets.
   6. Khởi tạo quản trị.
   7. Mời giáo viên thứ hai.
   8. Chạy checklist.
10. **Còn cần phê duyệt:**
    - Push GitHub và triển khai production (bạn tự làm, hoặc cho phép tôi làm).
    - **Quy tắc xếp cột 30 PHÚT / DẠY THỬ / ca thêm giờ** (đề xuất bỏ phần cộng đôi của công thức cũ).
    - **Đơn giá thật** (200k/200k/250k/250k chỉ là gợi ý).
    - **Ý nghĩa cột “Còn không”.**
    - **Ca hủy có tính công không.**
    - Có bật lớp “Only specific people can view” của Streamlit Cloud không (khuyến nghị: có).

## 6. Chưa làm / giới hạn đã biết

- Chưa triển khai thật lên Supabase và Streamlit Cloud; chưa chạy trên Linux thật (xem mục 3.2).
- Tải lại trang luôn về Tổng quan.
- Chưa có 2FA trong ứng dụng; khuyến nghị dùng lớp chia sẻ của Streamlit Cloud.
- Chưa có:
  - bút toán điều chỉnh sau chốt (dùng mở kỳ có lý do, rồi chốt lại thành phiên bản mới);
  - gói học phí dạy riêng;
  - đồng bộ Google Forms, Sheets, Calendar.
- Ca hủy “có tính công” mới chỉ được ghi nhận quy tắc, chưa tự cộng vào E–H.
- Giao diện Flask chưa có luồng lời mời / mã kích hoạt; vẫn dùng mật khẩu tạm như bản 2.0.
