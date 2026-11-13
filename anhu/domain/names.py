"""Tách danh sách tên học viên của MỘT ca.

Quy tắc (phụ lục V1, mục B):
- tách theo dấu phẩy, chấm phẩy hoặc xuống dòng;
- chuẩn hóa khoảng trắng, bỏ mục rỗng;
- KHÔNG loại tên trùng ('Phúc, My, My, Quỳnh' -> 4 mục);
- không đếm theo số từ, không đếm theo số dấu phẩy.
"""
import re

from .text import clean_ws

_SEP = re.compile(r"[,;\n\r]+")


def split_names(raw: str | None) -> list[str]:
    if raw is None:
        return []
    return [n for n in (clean_ws(p) for p in _SEP.split(str(raw))) if n]


def count_names(raw: str | None) -> int:
    return len(split_names(raw))
