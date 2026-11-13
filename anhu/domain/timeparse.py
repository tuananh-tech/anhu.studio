"""Parse khung giờ ca học dạng nhập tay trong file nguồn.

Biến thể thực tế đã gặp: '16g45-17g45', '19:45-20:45', '18h45-19g45', '19g00- 20g00',
'16g00 - 16g45', '9g30-10g30'. Không làm tròn phút.
"""
import re
from dataclasses import dataclass
from datetime import time

_PART = r"(\d{1,2})\s*(?:[gGhH:.]\s*(\d{1,2})?)?\s*(?:p|ph|phút)?"
_RANGE = re.compile(r"^\s*" + _PART + r"\s*[-–—~]\s*" + _PART + r"\s*$")


@dataclass(frozen=True)
class TimeRange:
    start: time
    end: time

    @property
    def minutes(self) -> int:
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)

    def label(self) -> str:
        """Định dạng như file gốc: 16g45-17g45."""
        return f"{fmt_g(self.start)}-{fmt_g(self.end)}"


def fmt_g(t: time) -> str:
    return f"{t.hour}g{t.minute:02d}" if t.hour >= 10 else f"{t.hour}g{t.minute:02d}"


def _mk(h: str, m: str | None) -> time | None:
    hh, mm = int(h), int(m) if m else 0
    if hh > 23 or mm > 59:
        return None
    return time(hh, mm)


def parse_range(raw) -> TimeRange | None:
    """Trả None nếu không parse được hoặc giờ kết thúc <= giờ bắt đầu (qua nửa đêm không hỗ trợ)."""
    if raw is None:
        return None
    m = _RANGE.match(str(raw))
    if not m:
        return None
    a, b = _mk(m.group(1), m.group(2)), _mk(m.group(3), m.group(4))
    if a is None or b is None or b <= a:
        return None
    return TimeRange(a, b)


def parse_hhmm(raw: str) -> time | None:
    m = re.match(r"^\s*" + _PART + r"\s*$", raw or "")
    return _mk(m.group(1), m.group(2)) if m else None
