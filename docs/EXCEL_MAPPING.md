# Mapping file nguồn `Chấm công(Câu trả lời).xlsx`

Phân tích trên file thật ngày 06/11/2026 (chạy cục bộ; kết quả chi tiết có tên thật nằm ở `private/`, không commit).
Mọi ví dụ dưới đây dùng dữ liệu **ẩn danh** (HVxx, "Cơ sở A") từ `tests/fixtures/anon_source.xlsx`.

## 1. Tổng quan ba sheet

| Sheet | Vai trò | Số dòng dữ liệu | Ghi chú |
|---|---|---|---|
| `Nhập liệu` | Phản hồi Google Form (1 dòng = 1 phản hồi = 1 ngày) | 38 (14/09/2026 → 03/11/2026) | header ở hàng 1, cố định hàng 1 |
| `Dữ liệu` | Bản sao phản hồi làm nguồn cho công thức | 23 (tháng 10/2026, hàng 2–24) + khối 26 dòng (04/09/2025 → 06/10/2025) **dán lặp 8 lần** (hàng 247–861) | header lặp lại ở đầu mỗi khối |
| `Chấm công` | Báo cáo theo mẫu, **toàn bộ là công thức** trỏ sang `Dữ liệu` | hàng 3–39 ↔ `Dữ liệu` hàng 2–38 (lệch 1), hàng 40 `TỔNG`, hàng 41 tiền | một hàng = một phản hồi, **không** phải một ngày lịch |

Kết luận import: 269 phản hồi đọc được → **64 phản hồi duy nhất**, 205 bản trùng
(23 dòng tháng 10/2026 có ở cả `Nhập liệu` và `Dữ liệu`, giống hệt nhau; khối 2025 lặp 8 lần).
Cộng mù sẽ nhân công lên nhiều lần → import khử trùng theo **nội dung** (ngày, cơ sở, giờ, tên, loại), không theo dấu thời gian.

## 2. Nhóm cột phản hồi (Nhập liệu / Dữ liệu)

Mỗi phản hồi có tối đa 13 nhóm ca. **Vị trí cột hai sheet lệch nhau từ cột AH**: `Nhập liệu` thiếu cột `Còn không`
sau `Tên học viên 9`. Vì vậy chương trình nhận diện nhóm theo **header**, không theo vị trí.

| Nhóm | `Dữ liệu` (giờ / tên / còn không / loại) | `Nhập liệu` | Header |
|---|---|---|---|
| 1 | D / E / F / – | D / E / F / – | `Thời gian ca học`, `Tên học viên`, `Còn không` |
| 2 | G / H / I / – | G / H / I / – | … `2` |
| 3 | J / K / L / – | J / K / L / – | … `3` |
| 4 | M / N / O / – | M / N / O / – | … `4` |
| 5 | P / Q / R / – | P / Q / R / – | … `5` |
| 6 | T / U / V / **S** | T / U / V / **S** | `Cái này là` + `Thời gian ca học 6` … |
| 7 | X / Y / Z / **W** | X / Y / Z / **W** | `Cái này là 2` |
| 8 | AB / AC / AD / **AA** | AB / AC / AD / **AA** | `Cái này là 3` |
| 9 | AF / AG / AH / **AE** | AF / AG / **–** / **AE** | `Cái này là 4` |
| 10–13 | AI-AK, AL-AN, AO-AQ, AR-AT | AH-AJ, AK-AM, AN-AP, AQ-AS | `Thời gian học`, `Tên học trò`, `Còn không` |
| khác | A `Timestamp`, B ngày, C cơ sở, AU `Noteee`, AV `Email Address` | A `Dấu thời gian`, B, C, AT `Noteee` | |

* `Cái này là` có giá trị `Dạy thử` hoặc `Dạy thêm giờ` → loại ca `trial` / `extra`.
* Nhóm 6–9 trống `Cái này là` → **Cần kiểm tra** (không đoán loại ca).
* Nhóm 10–13 (`Tên học trò`) chưa có dữ liệu thật; ý nghĩa chưa rõ (có thể là dạy riêng) → **Cần kiểm tra**.
* Biến thể giờ thực tế: `16g45-17g45`, `19:45-20:45`, `18h45-19g45`, `19g00- 20g00`, `16g00 - 16g45`, `9g30-10g30`.
  Parse chính xác đến phút, không làm tròn. Không đọc được → Cần kiểm tra.

### Cột `Còn không`
Giá trị: `Còn`, `Không`, `Dạy thử + Thêm giờ`. Ở `Nhập liệu`, `Còn` luôn đứng trước một ca tiếp theo và `Không`
luôn ở ca cuối (38/38 phản hồi). Nhiều khả năng đây là câu hỏi điều hướng của Form ("còn ca nữa không?"),
**không phải điểm danh**. Hệ thống chỉ lưu nguyên văn, **không** dùng để suy ra có mặt/vắng/đã dạy.
Ý nghĩa chờ người dùng xác nhận ở *Cài đặt → Ý nghĩa cột Còn không*.

## 3. Sheet `Chấm công` (mẫu xuất)

Header hai tầng — giữ nguyên văn (kể cả dấu cách cuối):

| Cột | Hàng 1 | Hàng 2 | Merge | Công thức gốc (hàng r, nguồn `Dữ liệu` hàng r-1) |
|---|---|---|---|---|
| A | `NGÀY ` | | A1:A2 | `='Dữ liệu'!B` — định dạng `dd/mm` |
| B | `CƠ SỞ` | | B1:B2 | `='Dữ liệu'!C` |
| C | | `Thời gian Ca học\n(ghi rõ chi tiết giờ học mỗi ca)` | | `TEXTJOIN(CHAR(10),TRUE, D,G,J,M,P,T,X,AB,AF,AI,AL,AO,AR)` |
| D | | `Tên Học Viên Hiện Diện` | | `TEXTJOIN(CHAR(10),TRUE, E,H,K,N,Q,U,Y,AC,AG,AJ,AM,AP,AS)` |
| E–H | `SỐ GIỜ DẠY` | `1 HV`,`2 HV`,`3HV`,`4HV` | E1:J1 | đếm số ô tên có (dấu phẩy + 1) = 1/2/3/4, trên 9 nhóm đầu |
| I | | `30 PHÚT` | | **không có công thức** (luôn trống, tổng = 0) |
| J | | `DẠY THỬ\nghi rõ tên bé, \nca ghép hay ca gộp vào lớp có sẵn` | | tên của nhóm 6–9 khi `Cái này là` ∈ {Dạy thử, Dạy thêm giờ} |
| K | `GHI CHÚ` | | K1:K2 | — |
| L | `TRUNG TÂM XÁC NHẬN ` | | L1:L2 | — (nền đỏ, chữ vàng) |
| M:O | | | M3:O40 | vùng trống nền đỏ sẫm |
| P | `Lương của bé` | | P1:P2 | — |
| hàng 40 | B=`TỔNG` | | | `=SUM(E3:E39)` … `=SUM(I3:I39)` |
| hàng 41 | | | | `=200000*E40`, `=200000*F40`, `=250000*G40`, `=250000*H40`, K=`E41+F41+G41+H41` |

Đơn giá hàng 41 chỉ được dùng làm **gợi ý chưa xác nhận** trong Cài đặt.

## 4. Golden-master

* `anhu/domain/legacy.py::legacy_formula_row` mô phỏng nguyên văn công thức cũ. Trên file thật:
  **259/259 ô (C, D, E, F, G, H, J × 37 hàng) khớp giá trị cache.** Test `tests/test_legacy_import.py` lặp lại
  kiểm chứng này trên fixture ẩn danh.
* Chạy lại trên file thật: `python scripts/golden_compare.py "<file>.xlsx" 2026 10 --out private/report.md`.

### Lỗi / điểm nghi vấn trong công thức cũ
1. **Cộng đôi ca "Dạy thêm giờ"**: E–H đếm cả nhóm 6–9 trong khi J cũng liệt kê chính ca đó.
   Ví dụ 17/10/2026: ca `15g00-15g45` (1 HV, Dạy thêm giờ) vừa làm E tăng thành 2 vừa nằm ở J.
2. **Cột 30 PHÚT không có công thức** → ca `19g00-19g30` (Dạy thêm giờ, không tên) ngày 19/10/2026 không được tính ở đâu.
   Ngoài ra dòng C có 3 giờ nhưng D chỉ 2 dòng tên (TEXTJOIN bỏ ô trống) → C và D lệch dòng.
3. E–H chỉ xét 9 nhóm đầu, trong khi C/D ghép 13 nhóm.
4. F/G/H không `TRIM` và không loại ô rỗng như E; tên có dấu phẩy thừa ở cuối (`An, Bình,`) sẽ bị đếm sai.
5. G: nhánh giá trị cộng thêm ô `W` (cột `Cái này là 2`) mà nhánh điều kiện không có — không nhất quán.
6. Bảng chỉ có hàng cho ngày có phản hồi (không đủ ngày trong tháng).

### So sánh tháng 10/2026 (engine mới vs sheet cũ)

| | E | F | G | H | I |
|---|---|---|---|---|---|
| Sheet cũ | 29 | 4 | 6 | 16 | 0 |
| Engine mới (quy tắc đề xuất, chưa xác nhận) | **28** | 4 | 6 | 16 | 0 |

Khác biệt theo ngày: 17/10 E 2→1 (lỗi 1); 19/10 ca 30 phút không tên → *Cần kiểm tra* (lỗi 2);
20/10 chỉ khác khoảng trắng (`HV08 , HV11` → `HV08, HV11`). Mọi ngày khác khớp tuyệt đối.
Sau khi người dùng bổ sung tên/xác nhận ca 19/10, ca đó sẽ vào cột I theo quy tắc 30 phút.

## 5. Quy tắc xếp cột của hệ thống mới (cần người dùng xác nhận)
Mỗi ca **đã xác nhận** rơi vào tối đa một cột: (1) thời lượng ≤ 30 phút và loại ∈ {thường, thêm giờ, bù} → I;
(2) loại ∈ {dạy thử, thêm giờ} → J (tên + ghi chú); (3) loại ∈ {thường, bù} → E/F/G/H theo **số mục tên** của chính ca đó;
> 4 HV → báo cáo bổ sung (sheet "Bổ sung"), không đưa vào H; 0 tên → Cần kiểm tra. Cấu hình có phiên bản ở Cài đặt.
