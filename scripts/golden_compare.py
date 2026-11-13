"""Golden-master trên file nguồn (chạy CỤC BỘ, kết quả ghi vào private/ — không commit).

1) Mô phỏng công thức cũ (legacy_formula_row) phải khớp giá trị cache của sheet 'Chấm công'.
2) So bảng công của engine mới (từ dữ liệu import) với sheet cũ theo từng ngày; liệt kê khác biệt.

Dùng: python scripts/golden_compare.py "<đường dẫn xlsx>" 2026 10 [--out private/golden_report.md]
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _s in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")
from openpyxl import load_workbook  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

from anhu.domain.legacy import legacy_formula_row, plan_import, read_timesheet_values  # noqa: E402
from anhu.domain.timesheet import SessionRecord, TimesheetRules, build_timesheet  # noqa: E402


def norm(v):
    if v is None or v == "":
        return ""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def main():
    path, year, month = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    out = Path(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[4] == "--out" else None
    data = Path(path).read_bytes()
    wbv = load_workbook(path, data_only=True)
    dl, cc = wbv["Dữ liệu"], wbv["Chấm công"]
    lines = ["# Golden-master report", ""]
    # 1) công thức cũ
    mism = 0
    checked = 0
    for r in range(3, 40):
        src = {get_column_letter(c): dl.cell(r - 1, c).value for c in range(1, dl.max_column + 1)}
        got = legacy_formula_row(src)
        for col in "CDEFGHJ":
            checked += 1
            exp = norm(cc[f"{col}{r}"].value)
            if norm(got[col]) != exp:
                mism += 1
                lines.append(f"- MISMATCH công thức cũ hàng {r} cột {col}: mô phỏng={got[col]!r} cache={exp!r}")
    lines.append(f"Mô phỏng công thức cũ: {checked} ô kiểm tra, {mism} ô lệch.")
    print(lines[-1])

    # 2) engine mới vs sheet cũ
    plan = plan_import(data)
    lines.append(f"\nImport: {len(plan.responses)} phản hồi, {len(plan.unique)} duy nhất, "
                 f"{len(plan.duplicates)} trùng, {len(plan.conflicts)} xung đột, "
                 f"{len(plan.timesheet_unmatched)} hàng Chấm công không khớp phản hồi.")
    print(lines[-1])
    recs = [SessionRecord(id=p.fingerprint, day=p.day, start=p.start, end=p.end, location_label=p.location,
                          kind=p.kind, status=p.status, attendees=p.attendees, source="import")
            for p in plan.sessions if p.day]
    view = build_timesheet(recs, year, month, TimesheetRules())
    old = {t.day: t.values for t in read_timesheet_values(wbv)}
    diffs = defaultdict(list)
    for row in view.rows:
        o = old.get(row.day)
        for col in "BCDEFGHIJ":
            a = norm(row.cells.get(col))
            b = norm(o.get(col)) if o else ""
            if col in "C D".split() and isinstance(b, str):
                b = "\n".join(x.strip() for x in b.split("\n"))
            if a != b:
                diffs[row.day].append((col, b, a))
    lines.append(f"\n## So sánh {month:02d}/{year}: engine mới vs sheet cũ — {len(diffs)} ngày khác\n")
    for d, items in sorted(diffs.items()):
        lines.append(f"### {d:%d/%m/%Y}")
        for col, b, a in items:
            lines.append(f"- {col}: cũ={b!r} → mới={a!r}")
        for s in view.detail:
            if s["day"] == d:
                lines.append(f"  - ca {s['time'] or '(không giờ)'} [{s['kind_label']}] {s['n']} HV → "
                             f"{s['column'] or s['not_counted_reason']}")
    lines.append(f"\nTổng mới: {view.totals}")
    print(f"Tổng mới {view.totals}; ngày khác: {len(diffs)}")
    if view.review:
        lines.append("Cần kiểm tra: " + "; ".join(f"{x['day']} {x['time']} ({x['reason']})" for x in view.review))
    text = "\n".join(lines)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print("Report ->", out)
    else:
        print(text)
    return 1 if mism else 0


if __name__ == "__main__":
    sys.exit(main())
