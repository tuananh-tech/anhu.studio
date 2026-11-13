"""Đọc file nguồn 'Chấm công(Câu trả lời).xlsx' (3 sheet) và mô phỏng công thức cũ.

Xem docs/EXCEL_MAPPING.md để biết mapping cột đầy đủ.

Điểm đã kiểm chứng trên file thật:
- 'Nhập liệu' và 'Dữ liệu' KHÔNG cùng vị trí cột từ cột AH trở đi ('Nhập liệu' thiếu một cột
  'Còn không' sau 'Tên học viên 9') -> nhóm ca được nhận diện theo header, không theo vị trí cố định.
- 'Dữ liệu' có header lặp lại giữa sheet (nhiều khối dán chồng, có khối lặp 8 lần) -> bỏ dòng header,
  khử trùng phản hồi theo nội dung.
- Cột 'Còn không' là câu hỏi điều hướng của Google Form; không dùng để suy ra có mặt/vắng mặt.
"""
from __future__ import annotations

import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from .names import split_names
from .text import clean_ws, fold
from .timeparse import parse_range

RE_TIME = re.compile(r"^th[oờ]i gian (ca )?h[oọ]c", re.I)
RE_NAMES = re.compile(r"^t[eê]n h[oọ]c (vi[eê]n|tr[oò])", re.I)
RE_FLAG = re.compile(r"^c[oò]n kh[oô]ng", re.I)
RE_TYPE = re.compile(r"^c[aá]i n[aà]y l[aà]", re.I)
TYPE_TO_KIND = {"dạy thử": "trial", "dạy thêm giờ": "extra"}
RESPONSE_SHEETS = ("Dữ liệu", "Nhập liệu")
TIMESHEET_SHEET = "Chấm công"


@dataclass
class GroupCols:
    index: int
    time: int
    names: int | None = None
    flag: int | None = None
    type: int | None = None
    names_header: str = ""


def detect_groups(header: list) -> tuple[list[GroupCols], dict]:
    """Nhận diện nhóm (giờ, tên, còn không, loại) từ hàng header. Trả (groups, cột_khác)."""
    groups: list[GroupCols] = []
    pending_type = None
    other = {}
    for i, h in enumerate(header, start=1):
        t = clean_ws(h)
        if not t:
            continue
        if RE_TYPE.match(t):
            pending_type = i
        elif RE_TIME.match(t):
            groups.append(GroupCols(len(groups) + 1, i, type=pending_type))
            pending_type = None
        elif RE_NAMES.match(t) and groups and groups[-1].names is None:
            groups[-1].names, groups[-1].names_header = i, t
        elif RE_FLAG.match(t) and groups and groups[-1].flag is None:
            groups[-1].flag = i
        else:
            other[t] = i
    return groups, other


@dataclass
class ParsedSession:
    day: date
    location: str
    group_index: int
    time_raw: str
    names_raw: str
    flag_raw: str
    type_raw: str
    start: object = None
    end: object = None
    attendees: list = field(default_factory=list)
    kind: str = "regular"
    issues: list = field(default_factory=list)
    fingerprint: str = ""

    @property
    def status(self) -> str:
        return "needs_review" if self.issues else "confirmed"


@dataclass
class ParsedResponse:
    sheet: str
    row: int
    timestamp: object
    day: date | None
    location: str
    sessions: list
    note: str
    raw: dict
    fingerprint: str = ""
    duplicate_of: tuple | None = None
    issues: list = field(default_factory=list)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    return str(v)


def _fp(*parts) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]


def _to_date(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        for f in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(v.strip(), f).date()
            except ValueError:
                pass
    return None


def parse_group(day, location, g: GroupCols, row_vals) -> ParsedSession | None:
    get = lambda c: row_vals[c - 1] if c and c - 1 < len(row_vals) else None
    time_raw, names_raw = clean_ws(_s(get(g.time))), _s(get(g.names)).strip()
    flag_raw, type_raw = clean_ws(_s(get(g.flag))), clean_ws(_s(get(g.type)))
    if not time_raw and not names_raw and not type_raw:
        return None  # chỉ có cờ điều hướng -> không phải ca
    ps = ParsedSession(day, location, g.index, time_raw, names_raw, flag_raw, type_raw)
    tr = parse_range(time_raw)
    if tr:
        ps.start, ps.end = tr.start, tr.end
    else:
        ps.issues.append("thiếu giờ" if not time_raw else f"không đọc được giờ '{time_raw}'")
    ps.attendees = split_names(names_raw)
    if not ps.attendees:
        ps.issues.append("không có tên học viên")
    if type_raw:
        k = TYPE_TO_KIND.get(type_raw.lower())
        if k:
            ps.kind = k
        else:
            ps.issues.append(f"loại ca lạ '{type_raw}'")
    elif g.type is not None:
        ps.issues.append("nhóm thuộc phần 'Dạy thử + Thêm giờ' nhưng trống 'Cái này là' -> chưa rõ loại ca")
    if g.names_header.lower().startswith("tên học trò"):
        ps.issues.append("nhóm cột 'Tên học trò' chưa rõ ý nghĩa (có thể là dạy riêng)")
    return ps


def read_responses(wb, sheet: str) -> list[ParsedResponse]:
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    groups, other = detect_groups(list(rows[0]))
    header_b = clean_ws(rows[0][1]) if len(rows[0]) > 1 else ""
    note_col = next((i for h, i in other.items() if fold(h).startswith("note")), None)
    out = []
    for rn, vals in enumerate(rows[1:], start=2):
        if not any(v not in (None, "") for v in vals):
            continue
        if clean_ws(vals[1] if len(vals) > 1 else None) == header_b:
            continue  # header lặp giữa sheet
        day = _to_date(vals[1])
        location = clean_ws(_s(vals[2]))
        raw = {get_column_letter(i + 1): _s(v) for i, v in enumerate(vals) if v not in (None, "")}
        sessions = [p for g in groups if (p := parse_group(day, location, g, vals))]
        resp = ParsedResponse(sheet, rn, vals[0], day, location, sessions,
                              clean_ws(_s(vals[note_col - 1])) if note_col else "", raw)
        if day is None:
            resp.issues.append("không đọc được ngày")
        # vân tay phản hồi: theo NỘI DUNG (không theo dấu thời gian/vị trí cột)
        resp.fingerprint = _fp(str(day), fold(location), *[
            f"{fold(p.time_raw)}|{'|'.join(fold(a) for a in p.attendees)}|{p.kind}|{fold(p.type_raw)}" for p in sessions])
        seen = Counter()
        for p in sessions:
            key = (str(day), fold(location), p.start.isoformat() if p.start else fold(p.time_raw),
                   p.end.isoformat() if p.end else "", "|".join(fold(a) for a in p.attendees), p.kind)
            seen[key] += 1
            p.fingerprint = _fp(*key, str(seen[key]))
        out.append(resp)
    return out


@dataclass
class LegacyTimesheetRow:
    row: int
    day: date | None
    values: dict   # 'A'..'L' -> giá trị cache


def read_timesheet_values(wb_values) -> list[LegacyTimesheetRow]:
    ws = wb_values[TIMESHEET_SHEET]
    out = []
    for r in range(3, ws.max_row + 1):
        vals = {c: ws[f"{c}{r}"].value for c in "ABCDEFGHIJKL"}
        if all(v in (None, "") for v in vals.values()):
            continue
        if clean_ws(_s(vals["B"])).upper() == "TỔNG" or vals["A"] in (None, ""):
            continue
        out.append(LegacyTimesheetRow(r, _to_date(vals["A"]), vals))
    return out


@dataclass
class LegacyImportPlan:
    responses: list            # mọi phản hồi đọc được
    unique: list               # phản hồi giữ lại
    duplicates: list           # (phản hồi, (sheet,row) gốc)
    conflicts: list            # cùng ngày+cơ sở nhưng nội dung khác giữa các phản hồi
    timesheet_rows: list
    timesheet_unmatched: list  # hàng 'Chấm công' không tìm thấy phản hồi tương ứng
    sessions: list             # ParsedSession từ phản hồi giữ lại
    file_sha256: str = ""


def plan_import(data: bytes) -> LegacyImportPlan:
    wb = load_workbook(io.BytesIO(data), data_only=True)
    responses = []
    for sh in RESPONSE_SHEETS:
        if sh in wb.sheetnames:
            responses += read_responses(wb, sh)
    unique, dups, first = [], [], {}
    for r in responses:
        if r.fingerprint in first:
            r.duplicate_of = first[r.fingerprint]
            dups.append(r)
        else:
            first[r.fingerprint] = (r.sheet, r.row)
            unique.append(r)
    by_key = {}
    conflicts = []
    for r in unique:
        k = (r.day, fold(r.location))
        if k in by_key:
            conflicts.append((by_key[k], r))
        else:
            by_key[k] = r
    ts_rows = read_timesheet_values(wb) if TIMESHEET_SHEET in wb.sheetnames else []
    unmatched = []
    for t in ts_rows:
        c_lines = [clean_ws(x) for x in _s(t.values["C"]).split("\n") if clean_ws(x)]
        match = [r for r in unique if r.day == t.day and
                 [p.time_raw for p in r.sessions if p.time_raw] == c_lines]
        if not match:
            unmatched.append(t)
    sessions = [p for r in unique for p in r.sessions]
    return LegacyImportPlan(responses, unique, dups, conflicts, ts_rows, unmatched, sessions,
                            hashlib.sha256(data).hexdigest())


# ---------------------------------------------------------------------------
# Mô phỏng NGUYÊN VĂN công thức cũ của sheet 'Chấm công' (để golden-master).
# Hàng Chấm công r tham chiếu hàng Dữ liệu r-1.
# ---------------------------------------------------------------------------
_C_COLS = ["D", "G", "J", "M", "P", "T", "X", "AB", "AF", "AI", "AL", "AO", "AR"]
_D_COLS = ["E", "H", "K", "N", "Q", "U", "Y", "AC", "AG", "AJ", "AM", "AP", "AS"]
_COUNT_COLS = ["E", "H", "K", "N", "Q", "U", "Y", "AC", "AG"]   # công thức cũ chỉ xét 9 nhóm đầu
_J_PAIRS = [("S", "U"), ("W", "Y"), ("AA", "AC"), ("AE", "AG")]


def _excel_trim(s: str) -> str:
    return re.sub(r" +", " ", s.strip(" "))


def _commas(s: str) -> int:
    return s.count(",")


def legacy_formula_row(row: dict) -> dict:
    """row: {'A':..,'B':..} giá trị một hàng 'Dữ liệu'. Trả giá trị C,D,E,F,G,H,J như công thức cũ."""
    v = lambda c: "" if row.get(c) is None else _s(row.get(c))
    out = {}
    out["C"] = "\n".join(x for x in (v(c) for c in _C_COLS) if x != "") or ""
    out["D"] = "\n".join(x for x in (v(c) for c in _D_COLS) if x != "") or ""
    e = sum(1 for c in _COUNT_COLS if _commas(_excel_trim(v(c))) + 1 == 1 and _excel_trim(v(c)) != "")
    out["E"] = e or ""
    f = sum(1 for c in _COUNT_COLS if _commas(v(c)) + 1 == 2)
    out["F"] = f or ""
    g_cond = sum(1 for c in _COUNT_COLS if _commas(v(c)) + 1 == 3)
    # lỗi công thức gốc: nhánh giá trị của G có thêm ô W (cột 'Cái này là 2')
    out["G"] = "" if g_cond == 0 else g_cond + (1 if _commas(v("W")) + 1 == 3 else 0)
    h = sum(1 for c in _COUNT_COLS if _commas(v(c)) + 1 == 4)
    out["H"] = h or ""
    out["J"] = "\n".join(v(n) for t, n in _J_PAIRS if v(t) in ("Dạy thử", "Dạy thêm giờ") and v(n) != "")
    return out
