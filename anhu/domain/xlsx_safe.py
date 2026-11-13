"""Chống chèn công thức (formula injection) khi xuất Excel.

openpyxl tự coi mọi chuỗi bắt đầu bằng '=' là công thức. Dữ liệu người dùng (tên học viên, ghi chú, file trung tâm…)
như "=HYPERLINK(...)" hoặc "=CMD|..." sẽ thành công thức chạy khi mở file. Hàm dưới ép các ô đó về kiểu chuỗi:
Excel hiển thị đúng nguyên văn nhưng không thực thi.
"""


def protect_cells(ws, min_row=1, max_row=None, cols=None):
    """Ép kiểu chuỗi cho mọi ô công thức trong vùng (dùng cho vùng CHỈ chứa dữ liệu người dùng)."""
    for row in ws.iter_rows(min_row=min_row, max_row=max_row or ws.max_row):
        for c in row:
            if cols and c.column_letter not in cols:
                continue
            if c.data_type == "f" and isinstance(c.value, str):
                c.data_type = "s"


def protect_workbook(wb):
    for ws in wb.worksheets:
        protect_cells(ws)
