"""Lập bảng công tháng theo mẫu sheet 'Chấm công' (A:L) + chế độ chi tiết từng ca.

Nguyên tắc (phụ lục V1):
- Hàng ngày được sinh từ date dimension (đủ 28/29/30/31 ngày) rồi LEFT JOIN các ca thực tế.
- Ngày không có ca đã xác nhận: chỉ cột A có ngày, B:L để None (ô rỗng thật).
- Chỉ ca thực tế có status='confirmed' mới vào bảng công; ca dự kiến không bao giờ vào.
- E/F/G/H = số CA có đúng 1/2/3/4 học viên (đếm từ danh sách tên của từng ca).
- Ca > 4 HV: không nhét vào H, đưa vào báo cáo bổ sung.
- Mỗi ca chỉ rơi vào tối đa MỘT cột đếm (E..I) hoặc cột J → không cộng đôi.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

from .calendar_month import month_days

KINDS = ("regular", "trial", "extra", "makeup")
KIND_LABEL = {"regular": "Ca thường", "trial": "Dạy thử", "extra": "Dạy thêm giờ", "makeup": "Dạy bù"}

# Header đúng nguyên văn file gốc (kể cả dấu cách cuối).
HEADER_ROW1 = {"A": "NGÀY ", "B": "CƠ SỞ", "E": "SỐ GIỜ DẠY", "K": "GHI CHÚ", "L": "TRUNG TÂM XÁC NHẬN ", "P": "Lương của bé"}
HEADER_ROW2 = {
    "C": "Thời gian Ca học\n(ghi rõ chi tiết giờ học mỗi ca)",
    "D": "Tên Học Viên Hiện Diện",
    "E": "1 HV", "F": "2 HV", "G": "3HV", "H": "4HV", "I": "30 PHÚT",
    "J": "DẠY THỬ\nghi rõ tên bé, \nca ghép hay ca gộp vào lớp có sẵn",
}
COLS = "ABCDEFGHIJKL"
COUNT_COLS = ("E", "F", "G", "H", "I")


@dataclass
class TimesheetRules:
    """Quy tắc xếp cột — có phiên bản, phải được người dùng xác nhận trong Cài đặt.

    Mặc định là ĐỀ XUẤT dựa trên công thức gốc nhưng bỏ phần cộng đôi; khi confirmed=False
    giao diện luôn hiện cảnh báo 'quy tắc chưa xác nhận'.
    """
    thirty_minute_max: int = 30                       # ca có thời lượng <= ngưỡng -> cột I
    thirty_kinds: tuple = ("regular", "extra", "makeup")
    j_kinds: tuple = ("trial", "extra")               # công thức gốc cột J: 'Dạy thử' hoặc 'Dạy thêm giờ'
    hv_kinds: tuple = ("regular", "makeup")           # đếm vào E..H theo số học viên
    version: int = 1
    confirmed: bool = False

    @classmethod
    def from_dict(cls, d: dict | None) -> "TimesheetRules":
        d = dict(d or {})
        for k in ("thirty_kinds", "j_kinds", "hv_kinds"):
            if k in d:
                d[k] = tuple(d[k])
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return {"thirty_minute_max": self.thirty_minute_max, "thirty_kinds": list(self.thirty_kinds),
                "j_kinds": list(self.j_kinds), "hv_kinds": list(self.hv_kinds),
                "version": self.version, "confirmed": self.confirmed}


@dataclass
class SessionRecord:
    id: Any
    day: date
    start: time | None
    end: time | None
    location_label: str = ""
    teaching_type: str = "center"     # center | private
    kind: str = "regular"
    status: str = "confirmed"         # confirmed | needs_review | void
    attendees: list[str] = field(default_factory=list)  # từng mục, KHÔNG khử trùng
    notes: str = ""
    teacher: str = ""
    center_id: Any = None
    source: str = ""

    @property
    def minutes(self) -> int | None:
        if self.start is None or self.end is None:
            return None
        return (self.end.hour * 60 + self.end.minute) - (self.start.hour * 60 + self.start.minute)

    @property
    def n(self) -> int:
        return len([a for a in self.attendees if a and a.strip()])

    def time_label(self) -> str:
        if self.start is None or self.end is None:
            return ""
        return f"{self.start.hour}g{self.start.minute:02d}-{self.end.hour}g{self.end.minute:02d}"


# ---- xếp cột cho từng ca -------------------------------------------------
PLACE_HV = {1: "E", 2: "F", 3: "G", 4: "H"}


def place(s: SessionRecord, rules: TimesheetRules) -> tuple[str | None, str | None]:
    """Trả (cột, lý_do_không_tính). Cột ∈ E..I, 'J', 'SUPP' (>4 HV) hoặc None."""
    if s.status != "confirmed":
        return None, "chưa xác nhận"
    mins = s.minutes
    if mins is None:
        return None, "thiếu/không đọc được giờ"
    if s.kind in rules.thirty_kinds and mins <= rules.thirty_minute_max:
        return "I", None
    if s.kind in rules.j_kinds:
        return "J", None
    if s.kind in rules.hv_kinds:
        if s.n == 0:
            return None, "ca chưa có học viên hiện diện"
        if s.n > 4:
            return "SUPP", None
        return PLACE_HV[s.n], None
    return None, f"loại ca '{s.kind}' không thuộc quy tắc nào"


@dataclass
class ExcelRow:
    day: date
    cells: dict          # 'A'..'L' -> giá trị hoặc None
    session_ids: list


@dataclass
class TimesheetView:
    year: int
    month: int
    rows: list[ExcelRow]
    totals: dict                 # E..I -> int
    supplemental: list[dict]     # ca > 4 HV
    review: list[dict]           # ca đã xác nhận nhưng không tính được (thiếu HV/giờ...)
    detail: list[dict]           # chi tiết từng ca (mọi ca truyền vào trong tháng)
    placements: dict             # session_id -> cột
    rules: TimesheetRules


def build_timesheet(sessions: list[SessionRecord], year: int, month: int,
                    rules: TimesheetRules | None = None) -> TimesheetView:
    rules = rules or TimesheetRules()
    days = month_days(year, month)
    in_month = [s for s in sessions if s.day.year == year and s.day.month == month]
    by_day: dict[date, list[SessionRecord]] = defaultdict(list)
    placements, review, supplemental, detail = {}, [], [], []

    for s in sorted(in_month, key=lambda x: (x.day, x.start or time(0), str(x.id))):
        col, why = place(s, rules)
        placements[s.id] = col
        detail.append({
            "id": s.id, "day": s.day, "teacher": s.teacher, "location": s.location_label,
            "start": s.start, "end": s.end, "time": s.time_label(), "attendees": list(s.attendees),
            "n": s.n, "minutes": s.minutes, "kind": s.kind, "kind_label": KIND_LABEL.get(s.kind, s.kind),
            "status": s.status, "source": s.source, "teaching_type": s.teaching_type,
            "column": col, "not_counted_reason": why, "notes": s.notes,
        })
        if s.status == "needs_review":
            review.append({"id": s.id, "day": s.day, "time": s.time_label(), "reason": "trạng thái Cần kiểm tra",
                           "attendees": list(s.attendees)})
        if s.status != "confirmed":
            continue
        by_day[s.day].append(s)
        if col == "SUPP":
            supplemental.append({"id": s.id, "day": s.day, "time": s.time_label(), "n": s.n,
                                 "attendees": list(s.attendees), "location": s.location_label})
        elif col is None:
            review.append({"id": s.id, "day": s.day, "time": s.time_label(), "reason": why,
                           "attendees": list(s.attendees)})

    rows: list[ExcelRow] = []
    totals = {c: 0 for c in COUNT_COLS}
    for d in days:
        ss = by_day.get(d, [])
        if not ss:
            rows.append(ExcelRow(d, {c: (d if c == "A" else None) for c in COLS}, []))
            continue
        # Một ngày = một hàng như file gốc; nếu cùng ngày có nhiều cơ sở thì mỗi cơ sở một hàng.
        groups: dict[str, list[SessionRecord]] = defaultdict(list)
        for s in ss:
            groups[s.location_label or ""].append(s)
        for loc in sorted(groups, key=lambda k: min((x.start or time(0)) for x in groups[k])):
            g = groups[loc]
            cells = {c: None for c in COLS}
            cells["A"] = d
            cells["B"] = loc or None
            times = [x.time_label() for x in g if x.time_label()]
            names = [", ".join(x.attendees) for x in g if x.attendees]
            cells["C"] = "\n".join(times) or None
            cells["D"] = "\n".join(names) or None
            counts = defaultdict(int)
            jl, notes = [], []
            for x in g:
                col = placements[x.id]
                if col in COUNT_COLS:
                    counts[col] += 1
                elif col == "J":
                    # Cột J yêu cầu 'ghi rõ tên bé, ca ghép hay ca gộp' -> tên + ghi chú của chính ca đó.
                    line = ", ".join(x.attendees) if x.attendees else f"({KIND_LABEL.get(x.kind)} {x.time_label()})"
                    jl.append(f"{line} ({x.notes})" if x.notes else line)
                    continue
                if x.notes:
                    notes.append(x.notes)
            for c in COUNT_COLS:
                if counts[c]:
                    cells[c] = counts[c]
                    totals[c] += counts[c]
            cells["J"] = "\n".join(jl) or None
            cells["K"] = "\n".join(notes) or None
            rows.append(ExcelRow(d, cells, [x.id for x in g]))
    return TimesheetView(year, month, rows, totals, supplemental, review, detail, placements, rules)
