"""Xuất bảng công ra XLSX giữ bố cục sheet 'Chấm công' gốc.

- Hàng 1/2: header hai tầng, merge đúng như gốc (A1:A2, B1:B2, E1:J1, K1:K2, L1:L2, P1:P2).
- Từ hàng 3: mỗi ngày trong tháng ít nhất một hàng; ô không có dữ liệu là ô rỗng thật (không ghi gì).
- Hàng TỔNG: =SUM(...) cho E..I như gốc.
- Hàng tiền: chỉ ghi khi đơn giá đã xác nhận đầy đủ; nếu không, để trống.
- Vùng M:O merge như gốc; cột P giữ header 'Lương của bé'.
- Sheet phụ 'Bổ sung' (ca >4 HV, ca cần kiểm tra) và 'Chi tiết ca' — không phá sheet chính.
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .xlsx_safe import protect_cells
from .timesheet import COLS, COUNT_COLS, HEADER_ROW1, HEADER_ROW2, TimesheetView

THIN = Side(style="thin")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
YELLOW = PatternFill("solid", fgColor="FFFFFF00")
PINK = PatternFill("solid", fgColor="FFEA9999")
RED = PatternFill("solid", fgColor="FFCC4125")
DARKRED = PatternFill("solid", fgColor="FFA61C00")
WHITE = PatternFill("solid", fgColor="FFFFFFFF")
TNR = "Times New Roman"
WIDTHS = {"C": 30.75, "D": 25.25, "J": 34.38, "K": 27.63, "L": 35.0, "P": 14.75}
MONEY_FMT = '#,##0[$ ₫]'
SHEET_NAME = "Chấm công"


def _header(ws):
    for col, text in HEADER_ROW1.items():
        c = ws[f"{col}1"]
        c.value = text
        c.font = Font(name=TNR, bold=col != "L", size=13 if col in "AB" else 14,
                      color="FFFFFF00" if col == "L" else None)
        c.alignment = Alignment(horizontal="center", wrap_text=col == "A")
    ws["L1"].fill = RED
    for col in "CDEFGHIJ":
        ws[f"{col}1"].fill = YELLOW
    for col, text in HEADER_ROW2.items():
        c = ws[f"{col}2"]
        c.value = text
        c.font = Font(name=TNR, bold=True, size=14)
        c.alignment = Alignment(horizontal="center", vertical="center" if col == "J" else None, wrap_text="\n" in text)
        c.fill = PINK if col in "CD" else YELLOW
    for rng in ("A1:A2", "B1:B2", "E1:J1", "K1:K2", "L1:L2", "P1:P2"):
        ws.merge_cells(rng)
    for r in (1, 2):
        for col in "ABCDEFGHIJKLP":
            ws[f"{col}{r}"].border = BOX


def write_timesheet_sheet(ws, view: TimesheetView, money: dict | None = None):
    """money: {'E': rate,...,'complete': bool} — chỉ truyền khi đơn giá đã xác nhận."""
    _header(ws)
    for k, w in WIDTHS.items():
        ws.column_dimensions[k].width = w
    r = 3
    for row in view.rows:
        for col in COLS:
            c = ws[f"{col}{r}"]
            v = row.cells.get(col)
            if v is not None:              # ô rỗng thật: không gán value
                c.value = v
            c.border = BOX
            c.fill = WHITE
            if col == "A":
                c.number_format = "dd/mm"
                c.font = Font(name="Arial", size=11, color="FF3C4043")
                c.alignment = Alignment(horizontal="right", wrap_text=True)
            elif col in "CDJK":
                c.alignment = Alignment(horizontal="center", vertical="center" if col == "J" else None, wrap_text=True)
                c.font = Font(name=TNR if col in "CD" else "Arial")
            elif col in COUNT_COLS:
                c.alignment = Alignment(horizontal="right")
        r += 1
    last = r - 1
    total_row = r
    ws.merge_cells(f"M3:O{total_row}")
    m = ws["M3"]
    m.fill = DARKRED
    m.font = Font(name=TNR, color="FFFFF2CC")
    m.alignment = Alignment(horizontal="center", wrap_text=True)
    ws[f"B{total_row}"] = "TỔNG"
    for col in COUNT_COLS:
        ws[f"{col}{total_row}"] = f"=SUM({col}3:{col}{last})"
    for col in "ABCDEFGHIJKL":
        c = ws[f"{col}{total_row}"]
        c.font = Font(name=TNR, bold=True, size=20)
        c.fill = YELLOW
        c.border = BOX
        c.alignment = Alignment(horizontal="center")
    if money and money.get("complete"):
        mr = total_row + 1
        parts = []
        for col in "EFGHI":
            rate = money.get(col)
            if rate is not None:
                ws[f"{col}{mr}"] = f"={int(rate)}*{col}{total_row}"
                ws[f"{col}{mr}"].number_format = MONEY_FMT
                parts.append(f"{col}{mr}")
        if parts:
            ws[f"K{mr}"] = "=" + "+".join(parts)
            ws[f"K{mr}"].number_format = MONEY_FMT
            ws[f"K{mr}"].alignment = Alignment(horizontal="center")
    return total_row


def export_timesheet_xlsx(view: TimesheetView, money: dict | None = None, meta: dict | None = None,
                          options: dict | None = None) -> bytes:
    """options (Cài đặt → Mẫu Excel): sheet_name, include_money_row, include_supplement, include_detail, include_info.
    Bố cục A:L, header, merge và định dạng của sheet chính KHÔNG thay đổi theo tùy chọn."""
    o = {"sheet_name": SHEET_NAME, "include_money_row": True, "include_supplement": True, "include_detail": True,
         "include_info": True, **(options or {})}
    wb = Workbook()
    ws = wb.active
    ws.title = o["sheet_name"] or SHEET_NAME
    total_row = write_timesheet_sheet(ws, view, money if o["include_money_row"] else None)
    protect_cells(ws, 3, total_row - 1)  # chỉ vùng dữ liệu; hàng TỔNG/tiền giữ công thức =SUM
    if o["include_supplement"]:
        _supplement(wb, view)
    if o["include_detail"]:
        _detail(wb, view)
    if meta and o["include_info"]:
        info = wb.create_sheet("Thông tin")
        for k, v in meta.items():
            info.append([k, v])
    for extra in wb.worksheets[1:]:
        protect_cells(extra)
    wb.active = 0
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _supplement(wb, view):
    sup = wb.create_sheet("Bổ sung")
    sup.append(["Báo cáo bổ sung — không thuộc cột A:L của mẫu gốc"])
    sup.append([])
    sup.append(["Ca trên 4 học viên (không đưa vào cột 4HV)"])
    sup.append(["Ngày", "Cơ sở", "Giờ", "Số HV", "Học viên"])
    for s in view.supplemental:
        sup.append([s["day"], s["location"], s["time"], s["n"], ", ".join(s["attendees"])])
        sup.cell(sup.max_row, 1).number_format = "dd/mm/yyyy"
    sup.append([])
    sup.append(["Ca đã xác nhận nhưng chưa tính công (cần kiểm tra)"])
    sup.append(["Ngày", "Giờ", "Lý do", "Học viên"])
    for s in view.review:
        sup.append([s["day"], s["time"], s["reason"], ", ".join(s["attendees"])])
        sup.cell(sup.max_row, 1).number_format = "dd/mm/yyyy"
    sup.column_dimensions["A"].width = 14
    sup.column_dimensions["E"].width = 40


def _detail(wb, view):
    det = wb.create_sheet("Chi tiết ca")
    det.append(["Ngày", "Giáo viên", "Cơ sở", "Bắt đầu", "Kết thúc", "Học viên", "Số HV", "Phút",
                "Loại ca", "Loại hình", "Trạng thái", "Cột bảng công", "Nguồn", "Ghi chú"])
    for d in view.detail:
        det.append([d["day"], d["teacher"], d["location"],
                    d["start"].strftime("%H:%M") if d["start"] else None,
                    d["end"].strftime("%H:%M") if d["end"] else None,
                    ", ".join(d["attendees"]), d["n"], d["minutes"], d["kind_label"],
                    "Trung tâm" if d["teaching_type"] == "center" else "Dạy riêng",
                    d["status"], d["column"] or (d["not_counted_reason"] or ""), d["source"], d["notes"]])
        det.cell(det.max_row, 1).number_format = "dd/mm/yyyy"


def blank_template_xlsx() -> bytes:
    """Mẫu trống (chỉ header) để gửi trung tâm."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    _header(ws)
    for k, w in WIDTHS.items():
        ws.column_dimensions[k].width = w
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
