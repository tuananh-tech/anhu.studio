# Báo cáo nghiệm thu — ANHU Teaching Studio 2.0 (nâng cấp vận hành dài hạn)

Ngày kiểm thử: 07/11/2026.
- Môi trường: Windows 11, Python 3.12, PostgreSQL 16 (bản nhúng `pgserver`), Chromium (Playwright).
- Ứng dụng kết nối bằng role `anhu_app`: không phải superuser, không có BYPASSRLS, giống môi trường thật.
- Toàn bộ dữ liệu dùng khi kiểm thử là dữ liệu ẩn danh.

## 1. Bằng chứng kiểm thử

| Loại | Kết quả |
|---|---|
| Test tự động (`python -m pytest`) | **152/152 đạt** trong khoảng 48 giây: 112 test V1 giữ nguyên và 40 test mới (`test_long_term.py`: 13, `test_web_admin.py`: 27 tính cả các trường hợp tham số hóa). |
| Trình duyệt thật — desktop 1366×900 | Đi qua 26 trang và 16 thao tác thật qua form: thêm trung tâm/cơ sở, học viên, người liên hệ, ca phát sinh (bộ đếm hiện “4 HV” cho “Phúc, My, My, Quỳnh”), đổi tên loại ca, nhập sai quy tắc, tải và kiểm tra sao lưu, xuất báo cáo, xuất Excel mẫu, mở hướng dẫn. **16/16 đạt.** |
| Trình duyệt thật — mobile 390×844 (cảm ứng) | Cùng kịch bản, **16/16 đạt**. Không có lỗi console, không có phản hồi HTTP ≥ 400, không trang nào tràn ngang. |
| Kiểm tra trực quan | Đã xem ảnh chụp và sửa 2 lỗi phát hiện được: menu mobile bị che mục, lịch tháng tràn ngang 8px. |

## 2. Phân loại chức năng

### 2.1 Hoàn thành và đã kiểm thử

**Quản trị trên web**

| Chức năng | Bằng chứng |
|---|---|
| Thanh điều hướng chỉ gồm 5 mục: Tổng quan / Lịch dạy / Học viên / Chấm công / Báo cáo; ⚙ Cài đặt tách riêng | `test_main_navigation_has_exactly_five_items…`, e2e |
| Trang Cài đặt có 14 mục, mỗi mục có hướng dẫn ngắn | `test_every_settings_section_renders_with_help`, `test_settings_hub…` |
| **Giáo viên & quyền truy cập:** tài khoản đầu tiên là quản trị; quản trị tạo tài khoản thứ 2 với mật khẩu tạm (bắt đổi ở lần đăng nhập đầu), khóa/mở khóa, đặt lại mật khẩu; giáo viên tự sửa hồ sơ và mật khẩu; chia sẻ chỉ xem / có sửa. Quản trị **không** đọc được dữ liệu của tài khoản kia. Người không phải quản trị bị DB chặn (`ADMIN_ONLY`), kể cả khi tự nâng quyền bằng SQL. | `test_first_account_is_admin…`, `test_admin_lock_and_reset…`, `test_admin_creates_second_account…`, `test_temp_password_forces_change`, `test_non_admin_cannot_manage_accounts` |
| **Học viên & người liên hệ:** mã học viên, nhiều người liên hệ (một người chính), kiểm tra định dạng SĐT/email, lưu trữ / khôi phục học viên | `test_subjects_groups_members_contacts`, `test_student_contacts_groups_archive_via_web`, e2e |
| **Trung tâm, cơ sở, địa điểm:** thêm, sửa tên/địa chỉ/liên hệ, lưu trữ, khôi phục. Đổi tên **không** làm thay đổi ca đã ghi. | `test_location_rename_and_archive_keep_history_labels`, `test_catalog_edit_archive_via_web` |
| **Môn học, lớp học, nhóm học viên:** thành viên có ngày vào và ngày rời; lọc học viên theo lớp/nhóm | `test_subjects_groups_members_contacts`, `test_student_contacts_groups_archive_via_web` |
| **Thời khóa biểu cố định & ngoại lệ:** có thêm danh sách ngoại lệ ±60 ngày (nghỉ / dời / bù / sửa riêng / ngoài lịch) | Trang `/lich/lap` (render test + e2e); logic ngoại lệ đã có test từ V1 (`test_flows.py`) |
| **Loại ca & trạng thái:** đổi tên hiển thị, ẩn/hiện, thứ tự, thêm loại ca mới. Loại ca mới **không được tính công** cho tới khi có quy tắc được xác nhận; DB từ chối mã loại ca không tồn tại (`KIND_UNKNOWN`). Tên trạng thái và lý do nghỉ đổi được. | `test_custom_kind_not_counted_until…`, `test_kind_labels_and_new_kind_via_web`, `test_labels_and_export_options` |
| **Đơn giá & ngày hiệu lực:** thêm, xác nhận, kết thúc hiệu lực (giữ lịch sử), lưu trữ đơn giá nhập nhầm (không còn được dùng để tính tiền) | `test_locked_period_is_frozen…`, `test_validation_messages_are_friendly` |
| **Quy tắc chấm công:** mỗi lần lưu là một phiên bản mới **có ngày hiệu lực**; tháng trước ngày hiệu lực vẫn dùng quy tắc cũ; xác nhận từng phiên bản | `test_custom_kind_not_counted_until_rules_include_it_with_effective_date` |
| **Mẫu Excel & ánh xạ:** tùy chọn tên sheet, tiền tố tên file, hàng tiền, các sheet phụ (bố cục A:L luôn giữ nguyên); hồ sơ ánh xạ file trung tâm có kiểm tra hợp lệ, mặc định theo trung tâm, lưu ngay từ màn hình đối soát | `test_labels_and_export_options`, `test_recon_mapping_profiles`, `test_validation_messages…` |
| **Kỳ chấm công:** danh sách kỳ, mọi phiên bản chốt (người chốt, thời điểm, mã băm, lý do mở), tải lại file của từng phiên bản | `test_locked_period_is_frozen…`, `test_locked_month_shows_snapshot_and_exports_locked_file` |
| **Nhật ký:** lọc theo loại dữ liệu, thao tác, ngày, từ khóa; hiển thị trường thay đổi (trước → sau); phân trang | `test_audit_search_shows_readable_changes`, `test_audit_log_page_filters` |
| **Báo cáo:** tổng quan 12 tháng (kỳ đã chốt lấy từ bản chốt), theo học viên, theo cơ sở, dạy riêng & thu tiền; tab nào cũng xuất Excel | `test_reports_render_and_export` (4 tab), e2e |

**Dữ liệu lâu dài**

| Chức năng | Bằng chứng |
|---|---|
| Migration có phiên bản: `schema_migrations`, `migrate.py --status`, mỗi file một transaction, file quyền chạy lại sau cùng | Test dựng CSDL mới và chạy migrate trong mọi phiên kiểm thử; `test_full_server_dump…` xác nhận không có migration nào bị chạy lại |
| Khóa định danh ổn định (UUID), không phụ thuộc tên học viên; mã học viên tùy chọn | Schema; khôi phục giữ nguyên ID |
| Lưu trữ mềm: role ứng dụng **không có quyền DELETE** trên dữ liệu gốc | `test_master_data_cannot_be_hard_deleted_only_archived` |
| Bản chốt bất biến: đổi tên cơ sở, đổi quy tắc, lưu trữ đơn giá sau khi chốt → màn hình và file xuất của kỳ đã chốt **không đổi**; sửa bản chốt bằng SQL bị DB chặn (`SNAPSHOT_IMMUTABLE`) | `test_locked_period_is_frozen_against_later_master_data_changes` |
| Nhật ký trước/sau do trigger ghi; ứng dụng không xóa/sửa được nhật ký | test V1 và test nhật ký mới |
| Sao lưu theo tài khoản (zip + manifest + SHA-256), kiểm tra file, khôi phục vào **CSDL mới**: số dòng từng bảng khớp, bảng công 4 tháng khớp từng ô, mã băm file đã chốt khớp, trigger khóa kỳ vẫn hoạt động; từ chối file hỏng/bị sửa và từ chối ghi đè tài khoản đang có dữ liệu | `test_backup_restore_roundtrip_into_new_database`, `test_backup_download_and_verify_via_web` |
| Sao lưu và khôi phục toàn máy chủ (`pg_dump` / `pg_restore` + migrate): số dòng khớp cho cả 2 tài khoản, RLS vẫn chặn | `test_full_server_dump_and_restore_keeps_data_and_rls` |

**Vận hành**

| Chức năng | Bằng chứng |
|---|---|
| Mất kết nối CSDL → trang 503 thân thiện sau khoảng 5 giây (trước đây treo khoảng 130 giây); `/suc-khoe` cho công cụ giám sát | `test_health_and_database_down_page` |
| Lỗi nhập liệu và lỗi do trigger DB được dịch thành thông báo tiếng Việt | `test_validation_messages_are_friendly`, các test web |
| RLS ở tầng DB giữ nguyên; bảng mới đều có RLS, FORCE RLS, trigger chủ sở hữu và audit | `test_db_security.py` (V1) và các test chặn chéo tài khoản mới |

**Yêu cầu V1 (giữ nguyên, 112 test cũ vẫn đạt)**
- Bảng công đủ mọi ngày trong tháng; ngày không có dữ liệu để trống thật.
- Tự đếm học viên theo tên; không có ô nhập số lượng.
- Tách dạy trung tâm / dạy riêng.
- Lịch lặp sinh ca dự kiến; chỉ ca đã xác nhận được tính công.
- Phát hiện trùng giờ, ca nghỉ, dạy bù.
- Xuất Excel đúng mẫu.
- Đối soát có bằng chứng.
- Không tự áp dụng quy tắc chưa được phê duyệt.

### 2.2 Đã lập trình nhưng chưa có test tự động riêng

- Các nút sửa/xác nhận phiên bản quy tắc trong bảng lịch sử: đã hiển thị và chạy được trên trình duyệt, nhưng chưa có test riêng cho nút “Bỏ xác nhận”.
- `scripts/backup.py accounts` (sao lưu cả hai tài khoản bằng kết nối admin): dùng chung hàm `export_backup` đã có test; bản thân lệnh CLI chưa có test.
- Trang Thông tin hệ thống, các cảnh báo vận hành, khối “Cần chú ý” ở trang Cài đặt: có test render, chưa kiểm từng cảnh báo.
- In trang (nút In, `@media print`): chưa kiểm bằng máy in thật.

### 2.3 Cần cấu hình dịch vụ bên ngoài

- **Triển khai Supabase/PostgreSQL thật, tên miền và HTTPS:** cần tài khoản và quyền của chủ dự án. Mới kiểm thử trên PostgreSQL 16 cục bộ.
- **Sao lưu tự động định kỳ:** cần cron hoặc Task Scheduler trên máy chủ chạy `scripts/backup.py dump`, hoặc bật backup của Supabase, và nơi lưu thứ hai.
- **Giám sát:** trỏ công cụ uptime vào `/suc-khoe`.
- **Danh sách email được phép** (`ANHU_ALLOWED_EMAILS`) và `ANHU_SECRET_KEY` trên máy chủ thật.

### 2.4 Chưa triển khai

- Đồng bộ Google Forms / Sheets / Calendar và OCR. Phụ lục V1 để sang V1.1; trang *Cài đặt → Đồng bộ* nêu rõ trạng thái này.
- Bút toán điều chỉnh sau chốt (hiện dùng quy trình mở kỳ có lý do, rồi chốt lại thành phiên bản mới).
- Gói học phí dạy riêng (hiện tính giá theo buổi/học viên và trạng thái thu tiền từng buổi).
- Gắn hàng loạt tên cũ (import) vào hồ sơ học viên (hiện gắn từng buổi).
- Ca hủy “có tính công”: hiện chỉ ghi nhận quy tắc, chưa tự cộng vào E–H.
- Khôi phục **gộp** vào tài khoản đang có dữ liệu (cố ý không làm để tránh trộn dữ liệu; chỉ khôi phục vào tài khoản trống).
- Giới hạn đăng nhập sai đang lưu trong bộ nhớ tiến trình. Khi chạy nhiều tiến trình WSGI, mỗi tiến trình có bộ đếm riêng.

### 2.5 Vẫn chờ chủ dự án xác nhận nghiệp vụ (từ V1)

1. Quy tắc xếp cột 30 PHÚT / DẠY THỬ / ca thêm giờ. Đề xuất hiện tại bỏ phần cộng hai lần của công thức cũ.
2. Đơn giá thật. Mức 200k/200k/250k/250k chỉ là gợi ý.
3. Ý nghĩa cột “Còn không”.
4. Ca hủy có được tính công hay không.

Cả 4 mục đều xác nhận được trực tiếp trên web.

## 3. Checklist triển khai dài hạn

**Trước khi chạy thật**
- [ ] Tạo dự án PostgreSQL/Supabase, bật backup tự động của nhà cung cấp.
- [ ] Chạy `scripts/migrate.py` với mật khẩu mạnh cho `anhu_app`; lưu mật khẩu vào trình quản lý bí mật, không đưa vào repo.
- [ ] Đặt `ANHU_SECRET_KEY` dài và ngẫu nhiên, `ANHU_COOKIE_SECURE=1`, HTTPS bắt buộc.
- [ ] Tạo tài khoản quản trị bằng `create_user.py`; tạo tài khoản thứ hai trên web.
- [ ] Trên web: xác nhận quy tắc chấm công, nhập và xác nhận đơn giá, xác nhận ý nghĩa “Còn không” và quy tắc ca hủy.
- [ ] Import lịch sử từ file Excel thật (chỉ làm trên máy chủ riêng); kiểm tra tháng 10/2026 so với báo cáo golden-master.
- [ ] Trỏ giám sát vào `/suc-khoe`.

**Hằng tháng**
- [ ] Đối soát → chốt kỳ → tải file đã chốt gửi trung tâm.
- [ ] Tải bản sao lưu tài khoản; lưu ở 2 nơi.
- [ ] Kiểm tra bản sao lưu (*Cài đặt → Sao lưu → Kiểm tra*).

**Hằng quý**
- [ ] Diễn tập khôi phục: khôi phục bản `pg_dump` mới nhất vào một CSDL tạm, chạy `migrate.py`, đăng nhập thử, đối chiếu mã băm của một kỳ đã chốt.
- [ ] Cập nhật thư viện (`pip list --outdated`), chạy `pytest` trên bản sao trước khi cập nhật thật.
- [ ] Xem *Cài đặt → Thông tin hệ thống* và xử lý các cảnh báo.

**Khi cập nhật phiên bản:** làm theo *docs/QUAN_TRI_VAN_HANH.md* mục 5 (sao lưu → `--status` → thử trên bản sao → migrate → triển khai → kiểm tra).

**Khi chuyển máy chủ:** làm theo *docs/QUAN_TRI_VAN_HANH.md* mục 4. Quy trình này được kiểm chứng tự động bằng `test_full_server_dump_and_restore_keeps_data_and_rls`.

**Khi bàn giao cho người khác bảo trì:**
- *README.md*: chạy cục bộ, test, kiến trúc.
- *docs/EXCEL_MAPPING.md*: logic file Excel gốc.
- *docs/QUAN_TRI_VAN_HANH.md*: vận hành.
- Quy ước migration: chỉ thêm, không sửa file đã phát hành.
