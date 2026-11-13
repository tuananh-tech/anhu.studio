"""Tính tiền công — CHỈ dùng đơn giá đã được người dùng xác nhận.

Danh mục đơn giá khớp cột bảng công: hv1..hv4 (E..H), hv5plus (ca >4 HV, báo cáo bổ sung),
min30 (I), trial_extra (J). Đơn giá có thời kỳ hiệu lực theo trung tâm.
Nếu thiếu đơn giá đã xác nhận cho bất kỳ ca nào, tổng tiền được đánh dấu 'chưa đầy đủ'.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

CATEGORY_BY_COL = {"E": "hv1", "F": "hv2", "G": "hv3", "H": "hv4", "I": "min30", "J": "trial_extra", "SUPP": "hv5plus"}
CATEGORY_LABEL = {"hv1": "Ca 1 HV", "hv2": "Ca 2 HV", "hv3": "Ca 3 HV", "hv4": "Ca 4 HV", "hv5plus": "Ca trên 4 HV",
                  "min30": "Ca 30 phút", "trial_extra": "Dạy thử / thêm giờ"}

# Giá trị xuất hiện trong hàng 41 của file gốc — chỉ là GỢI Ý, không bao giờ dùng khi chưa xác nhận.
EXCEL_SUGGESTED = {"hv1": 200000, "hv2": 200000, "hv3": 250000, "hv4": 250000}


@dataclass
class Rate:
    category: str
    amount: Decimal
    valid_from: date
    valid_to: date | None
    confirmed: bool
    center_id: object = None

    def applies(self, d: date, center_id) -> bool:
        return (self.confirmed and self.valid_from <= d and (self.valid_to is None or d <= self.valid_to)
                and (self.center_id is None or self.center_id == center_id))


@dataclass
class PayResult:
    by_category: dict          # category -> {"count": n, "amount": Decimal}
    total: Decimal
    missing: list              # [{"id","day","category"}] ca không có đơn giá đã xác nhận
    complete: bool


def compute_pay(view, sessions, rates: list[Rate]) -> PayResult:
    by_id = {s.id: s for s in sessions}
    out, missing, total = {}, [], Decimal(0)
    for sid, col in view.placements.items():
        s = by_id.get(sid)
        if s is None or col is None or s.status != "confirmed":
            continue
        cat = CATEGORY_BY_COL[col]
        # đơn giá riêng theo trung tâm ưu tiên hơn đơn giá chung
        cands = [r for r in rates if r.category == cat and r.applies(s.day, s.center_id)]
        cands.sort(key=lambda r: (r.center_id is None, -r.valid_from.toordinal()))
        slot = out.setdefault(cat, {"count": 0, "amount": Decimal(0), "label": CATEGORY_LABEL[cat]})
        slot["count"] += 1
        if not cands:
            missing.append({"id": sid, "day": s.day, "category": cat})
            continue
        slot["amount"] += cands[0].amount
        total += cands[0].amount
    order = list(CATEGORY_LABEL)
    out = {k: out[k] for k in sorted(out, key=order.index)}
    return PayResult(out, total, missing, complete=not missing)
