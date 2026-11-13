"""Đối soát bảng công do trung tâm gửi với dữ liệu thực tế đã xác nhận.

File trung tâm là nguồn ĐỘC LẬP: chỉ đọc, không bao giờ ghi đè dữ liệu cá nhân.
Kết quả là danh sách mục chênh lệch có chứng cứ (số hàng + giá trị ô gốc) để người dùng duyệt.
"""
from __future__ import annotations

import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from .names import split_names
from .text import clean_ws, fold
from .timeparse import parse_range
from .timesheet import COUNT_COLS, KIND_LABEL

FIELDS = {  # khóa mapping -> nhãn
    "date": "Ngày (bắt buộc)", "location": "Cơ sở", "times": "Thời gian ca học", "names": "Tên học viên",
    "E": "1 HV", "F": "2 HV", "G": "3HV", "H": "4HV", "I": "30 PHÚT", "J": "Dạy thử", "K": "Ghi chú",
}
TEMPLATE_MAPPING = {"date": "A", "location": "B", "times": "C", "names": "D", "E": "E", "F": "F", "G": "G",
                    "H": "H", "I": "I", "J": "J", "K": "K"}
CATEGORY_LABEL = {
    "match": "Khớp", "missing_in_center": "Thiếu ở bảng trung tâm", "extra_in_center": "Có ở trung tâm, không có ở bảng cá nhân",
    "time_mismatch": "Lệch giờ", "attendee_mismatch": "Lệch số học viên", "kind_mismatch": "Lệch loại ca",
    "location_mismatch": "Sai cơ sở", "count_mismatch": "Lệch số ca theo nhóm HV", "money_mismatch": "Lệch tiền",
    "unreadable": "Không đọc được",
}


def preview(data: bytes, max_rows: int = 12) -> dict:
    wb = load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = []
        for i, r in enumerate(ws.iter_rows(values_only=True)):
            if i >= max_rows:
                break
            rows.append([_cell_text(v) for v in r[:16]])
        out[ws.title] = rows
    wb.close()
    return out


def suggest_mapping(pv: dict) -> dict:
    """Nếu sheet giống mẫu 'Chấm công' (A1 'NGÀY', E2 '1 HV') -> gợi ý mapping A..K, dữ liệu từ hàng 3."""
    for sheet, rows in pv.items():
        if len(rows) >= 2 and rows[0] and fold(rows[0][0]).startswith("ngay") and len(rows[1]) > 4 and fold(rows[1][4]) == "1 hv":
            return {"sheet": sheet, "start_row": 3, "columns": dict(TEMPLATE_MAPPING)}
    first = next(iter(pv), None)
    return {"sheet": first, "start_row": 2, "columns": {}}


def _cell_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _to_day(v, year: int, month: int) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)):
        return None
    s = clean_ws(v)
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    try:  # 'dd/mm' -> năm của kỳ đối soát
        d, m = s.split("/")[:2]
        return date(year, int(m), int(d))
    except (ValueError, TypeError):
        return None


def _int(v) -> int:
    if v in (None, ""):
        return 0
    try:
        return int(float(str(v).strip()))
    except ValueError:
        return 0


@dataclass
class TheirSession:
    row: int
    line: int
    time_raw: str
    start: object
    end: object
    names: list | None      # None = không ghép được dòng tên với dòng giờ
    is_trial: bool = False


@dataclass
class TheirRow:
    row: int
    day: date | None
    location: str
    sessions: list
    counts: dict
    raw: dict
    j: str = ""


def parse_center_file(data: bytes, mapping: dict, year: int, month: int) -> tuple[list[TheirRow], list[dict]]:
    wb = load_workbook(io.BytesIO(data), data_only=True)
    ws = wb[mapping["sheet"]]
    cols = {k: column_index_from_string(v) for k, v in mapping.get("columns", {}).items() if v}
    if "date" not in cols:
        raise ValueError("Chưa chọn cột Ngày")
    rows, problems = [], []
    for r in range(int(mapping.get("start_row", 2)), ws.max_row + 1):
        get = lambda k: ws.cell(r, cols[k]).value if k in cols else None
        raw = {get_column_letter(c): _cell_text(ws.cell(r, c).value) for c in range(1, min(ws.max_column, 16) + 1)
               if ws.cell(r, c).value not in (None, "")}
        if not raw:
            continue
        dv = get("date")
        if fold(_cell_text(get("location"))) == "tong" or (dv in (None, "") and fold(_cell_text(ws.cell(r, 2).value)) == "tong"):
            continue  # hàng TỔNG
        day = _to_day(dv, year, month)
        if day is None:
            problems.append({"row": r, "raw": raw, "reason": "không đọc được ngày"})
            continue
        if (day.year, day.month) != (year, month):
            continue
        tlines = [clean_ws(x) for x in _cell_text(get("times")).split("\n") if clean_ws(x)]
        nlines = [x for x in _cell_text(get("names")).split("\n") if clean_ws(x)]
        jtext = _cell_text(get("J"))
        jnames = {fold(n) for line in jtext.split("\n") for n in split_names(line)}
        paired = len(tlines) == len(nlines)
        sess = []
        for i, t in enumerate(tlines):
            tr = parse_range(t)
            names = split_names(nlines[i]) if paired else None
            is_trial = bool(names) and all(fold(n) in jnames for n in names)
            sess.append(TheirSession(r, i + 1, t, tr.start if tr else None, tr.end if tr else None, names, is_trial))
        counts = {c: _int(get(c)) for c in COUNT_COLS if c in cols}
        rows.append(TheirRow(r, day, clean_ws(_cell_text(get("location"))), sess, counts, raw, jtext))
    return rows, problems


@dataclass
class Item:
    day: date
    category: str
    detail: str
    our: dict = field(default_factory=dict)
    their: dict = field(default_factory=dict)
    amount_diff: Decimal | None = None

    def to_dict(self):
        return {"day": self.day.isoformat(), "category": self.category, "category_label": CATEGORY_LABEL[self.category],
                "detail": self.detail, "our": self.our, "their": self.their,
                "amount_diff": str(self.amount_diff) if self.amount_diff is not None else None}


def _mins(t):
    return t.hour * 60 + t.minute


def compare(view, our_sessions, their_rows: list[TheirRow], rates_by_col: dict | None = None,
            problems: list | None = None) -> list[Item]:
    """view: TimesheetView của phía cá nhân (đã lọc đúng trung tâm); our_sessions: SessionRecord đã xác nhận."""
    items: list[Item] = []
    ours_by_day = defaultdict(list)
    for s in our_sessions:
        if s.status == "confirmed" and s.day.year == view.year and s.day.month == view.month:
            ours_by_day[s.day].append(s)
    theirs_by_day = defaultdict(list)
    for tr in their_rows:
        theirs_by_day[tr.day].append(tr)
    for p in problems or []:
        items.append(Item(date(view.year, view.month, 1), "unreadable", p["reason"], their={"row": p["row"], "raw": p["raw"]}))

    def our_ref(s):
        return {"id": str(s.id), "time": s.time_label(), "n": s.n, "attendees": s.attendees,
                "kind": KIND_LABEL.get(s.kind, s.kind), "location": s.location_label}

    def their_ref(ts, tr):
        return {"row": ts.row, "line": ts.line, "time": ts.time_raw, "attendees": ts.names, "raw": tr.raw}

    for day in sorted(set(ours_by_day) | set(theirs_by_day)):
        ours = sorted(ours_by_day.get(day, []), key=lambda s: (s.start or datetime.min.time()))
        trs = theirs_by_day.get(day, [])
        theirs = [(ts, tr) for tr in trs for ts in tr.sessions]
        used_o, used_t, pairs = set(), set(), []
        # 1) khớp đúng giờ
        for i, s in enumerate(ours):
            for j, (ts, tr) in enumerate(theirs):
                if j in used_t or s.start is None:
                    continue
                if ts.start == s.start and ts.end == s.end:
                    used_o.add(i); used_t.add(j); pairs.append((s, ts, tr, False)); break
        # 2) giao nhau về thời gian -> lệch giờ
        for i, s in enumerate(ours):
            if i in used_o or s.start is None:
                continue
            for j, (ts, tr) in enumerate(theirs):
                if j in used_t or ts.start is None:
                    continue
                if _mins(ts.start) < _mins(s.end) and _mins(s.start) < _mins(ts.end):
                    used_o.add(i); used_t.add(j); pairs.append((s, ts, tr, True)); break
        for s, ts, tr, shifted in pairs:
            o, t = our_ref(s), their_ref(ts, tr)
            if shifted:
                items.append(Item(day, "time_mismatch", f"Cá nhân {s.time_label()} ≠ trung tâm {ts.time_raw}", o, t))
            ok = not shifted
            if ts.names is not None and len(ts.names) != s.n:
                ok = False
                items.append(Item(day, "attendee_mismatch",
                                  f"Ca {s.time_label()}: cá nhân {s.n} HV ≠ trung tâm {len(ts.names)} HV", o, t))
            our_trial = s.kind in view.rules.j_kinds
            if ts.names is not None and our_trial != ts.is_trial:
                ok = False
                items.append(Item(day, "kind_mismatch",
                                  f"Ca {s.time_label()}: cá nhân '{KIND_LABEL.get(s.kind)}' ≠ trung tâm "
                                  f"'{'Dạy thử/thêm giờ' if ts.is_trial else 'Ca thường'}'", o, t))
            if tr.location and s.location_label and fold(tr.location) != fold(s.location_label):
                ok = False
                items.append(Item(day, "location_mismatch",
                                  f"Ca {s.time_label()}: cá nhân '{s.location_label}' ≠ trung tâm '{tr.location}'", o, t))
            if ok:
                items.append(Item(day, "match", f"Ca {s.time_label()} khớp", o, t))
        for i, s in enumerate(ours):
            if i not in used_o:
                items.append(Item(day, "missing_in_center", f"Ca {s.time_label()} ({s.n} HV) không có trong bảng trung tâm", our_ref(s)))
        seen_t = defaultdict(int)
        for j, (ts, tr) in enumerate(theirs):
            seen_t[ts.time_raw] += 1
            if j not in used_t:
                dup = " — dòng giờ bị lặp trong bảng trung tâm" if seen_t[ts.time_raw] > 1 else ""
                items.append(Item(day, "extra_in_center", f"Ca {ts.time_raw} có ở trung tâm, không có ca đã xác nhận{dup}",
                                  their=their_ref(ts, tr)))
        # 3) so số ca theo nhóm HV (cấp ngày) và tiền
        our_counts = defaultdict(int)
        for r in view.rows:
            if r.day == day:
                for c in COUNT_COLS:
                    our_counts[c] += r.cells.get(c) or 0
        their_counts = defaultdict(int)
        mapped = set()
        for tr in trs:
            for c, v in tr.counts.items():
                their_counts[c] += v
                mapped.add(c)
        diffs = {c: our_counts[c] - their_counts[c] for c in mapped if our_counts[c] != their_counts[c]}
        if diffs:
            txt = ", ".join(f"{c}: cá nhân {our_counts[c]} / trung tâm {their_counts[c]}" for c in sorted(diffs))
            items.append(Item(day, "count_mismatch", txt, {"counts": dict(our_counts)},
                              {"counts": dict(their_counts), "rows": [t.row for t in trs]}))
            if rates_by_col and all(c in rates_by_col for c in diffs):
                amt = sum(Decimal(diffs[c]) * Decimal(rates_by_col[c]) for c in diffs)
                items.append(Item(day, "money_mismatch", f"Chênh {amt:,.0f} ₫ (cá nhân − trung tâm) theo đơn giá đã xác nhận",
                                  amount_diff=amt))
    return items
