"""Tạo fixture ẩn danh từ file nguồn thật (chạy cục bộ).

- Mỗi tên học viên (theo tên chuẩn hóa) -> mã HVxx nhất quán; tên trùng vẫn trùng (My, My -> HV07, HV07).
- Cơ sở -> 'Cơ sở A'..; email/ghi chú tự do -> bỏ.
- Giữ nguyên: cấu trúc sheet, header, vị trí cột, header lặp, khối trùng, ngày, giờ, cờ Còn/Không, loại ca.
- Sheet 'Chấm công' được ghi bằng GIÁ TRỊ cache (đã ẩn danh) để làm golden-master cho mô phỏng công thức.
Dùng: python scripts/make_anon_fixture.py "<xlsx thật>" tests/fixtures/anon_source.xlsx
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook, load_workbook  # noqa: E402

from anhu.domain.legacy import RE_NAMES, RE_TYPE, RE_FLAG, RE_TIME  # noqa: E402
from anhu.domain.text import clean_ws, fold  # noqa: E402

names_map, loc_map = {}, {}


def pseudo(name: str) -> str:
    k = fold(name)
    if k not in names_map:
        names_map[k] = f"HV{len(names_map) + 1:02d}"
    return names_map[k]


def anon_names(v):
    if v is None or not isinstance(v, str):
        return v
    # giữ nguyên dấu phân cách và khoảng trắng gốc để giữ hành vi công thức đếm dấu phẩy
    out, buf = [], ""
    for ch in v:
        if ch in ",\n":
            out.append(buf); out.append(ch); buf = ""
        else:
            buf += ch
    out.append(buf)
    res = []
    for part in out:
        if part in (",", "\n") or not clean_ws(part):
            res.append(part)
        else:
            lead = part[: len(part) - len(part.lstrip())]
            trail = part[len(part.rstrip()):]
            res.append(lead + pseudo(part) + trail)
    return "".join(res)


def anon_loc(v):
    if not isinstance(v, str) or not clean_ws(v) or v == "Dạy ở đâu đâyy":
        return v
    k = fold(v)
    if k not in loc_map:
        loc_map[k] = f"Cơ sở {chr(65 + len(loc_map))}"
    return loc_map[k]


def main(src, dst):
    wv = load_workbook(src, data_only=True)
    out = Workbook()
    out.remove(out.active)
    for sh in ("Nhập liệu", "Dữ liệu"):
        ws = wv[sh]
        o = out.create_sheet(sh)
        header = [c.value for c in ws[1]]
        kinds = {}
        for i, h in enumerate(header, 1):
            t = clean_ws(h)
            kinds[i] = ("names" if RE_NAMES.match(t) else "time" if RE_TIME.match(t) else
                        "flag" if RE_FLAG.match(t) else "type" if RE_TYPE.match(t) else
                        "loc" if i == 3 else "keep" if i <= 2 else "drop")
        for r in range(1, ws.max_row + 1):
            vals = [c.value for c in ws[r]]
            is_header = r == 1 or vals[1] == "Hôm này là ngàyy"
            for i, v in enumerate(vals, 1):
                if v is None:
                    continue
                k = kinds.get(i, "drop")
                if not is_header:
                    if k == "names":
                        v = anon_names(v)
                    elif k == "loc":
                        v = anon_loc(v)
                    elif k == "drop":
                        v = None
                if v is not None:
                    o.cell(r, i, v)
    cc = wv["Chấm công"]
    o = out.create_sheet("Chấm công")
    for r in range(1, cc.max_row + 1):
        for c in range(1, 13):
            v = cc.cell(r, c).value
            if v is None or v == "":
                continue
            if r >= 3 and c in (4, 10):
                v = anon_names(v)
            elif r >= 3 and c == 2 and v != "TỔNG":
                v = anon_loc(v)
            o.cell(r, c, v)
            if c == 1 and r >= 3:
                o.cell(r, c).number_format = "dd/mm"
    out.save(dst)
    print(f"ok: {len(names_map)} tên -> mã, {len(loc_map)} cơ sở -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
