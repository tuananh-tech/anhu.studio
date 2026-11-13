"""Chuẩn hóa văn bản tiếng Việt."""
import re
import unicodedata

_WS = re.compile(r"\s+", re.UNICODE)


def clean_ws(s: str | None) -> str:
    """Gộp mọi khoảng trắng (kể cả NBSP, tab) thành một dấu cách và cắt hai đầu."""
    if s is None:
        return ""
    return _WS.sub(" ", str(s).replace(" ", " ")).strip()


def fold(s: str | None) -> str:
    """Chuỗi tìm kiếm không dấu, chữ thường: 'Quỳnh' -> 'quynh', 'Đô' -> 'do'."""
    s = clean_ws(s).replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return s.lower()
