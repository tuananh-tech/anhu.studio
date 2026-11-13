"""Tầng dịch vụ: mọi hàm nhận `conn` đã gắn app.user_id (RLS áp dụng ở DB)."""


class BizError(Exception):
    """Lỗi nghiệp vụ hiển thị được cho người dùng (tiếng Việt)."""


class ConflictError(BizError):
    def __init__(self, conflicts):
        self.conflicts = conflicts
        super().__init__("Trùng khung giờ với: " + "; ".join(conflicts) + ". Nhập lý do ngoại lệ nếu vẫn muốn lưu.")


def db_error_message(e: Exception) -> str | None:
    """Dịch lỗi do trigger DB (P0001) sang thông điệp thân thiện."""
    msg = str(getattr(e, "orig", e))
    for code in ("PERIOD_LOCKED", "UNLOCK_REASON_REQUIRED", "OWNER_IMMUTABLE", "OWNER_MISMATCH", "MAX_USERS", "ADMIN_ONLY",
                 "KIND_UNKNOWN", "SNAPSHOT_IMMUTABLE"):
        if code in msg:
            line = msg.split("\n")[0]
            return line.split(":", 1)[1].strip() if ":" in line else line
    if "row-level security" in msg or "permission denied" in msg:
        return "Bạn không có quyền thực hiện thao tác này (dữ liệu chỉ xem hoặc thuộc người khác)."
    if "duplicate key" in msg:
        return "Dữ liệu bị trùng với một mục đã có (tên hoặc mã đã tồn tại)."
    if "violates check constraint" in msg or "violates not-null" in msg:
        return "Dữ liệu nhập chưa hợp lệ — kiểm tra lại các ô bắt buộc."
    return None
