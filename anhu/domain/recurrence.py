"""Sinh ca dự kiến từ lịch lặp (thuần). Ca dự kiến KHÔNG bao giờ là công."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from .calendar_month import daterange

WEEKDAY_LABEL = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


@dataclass
class TemplateVersion:
    id: object
    series_id: object
    weekday: int          # 0 = thứ Hai
    start: time
    end: time
    valid_from: date
    valid_to: date | None


def occurrences(tpl: TemplateVersion, a: date, b: date):
    lo = max(a, tpl.valid_from)
    hi = b if tpl.valid_to is None else min(b, tpl.valid_to)
    for d in daterange(lo, hi):
        if d.weekday() == tpl.weekday:
            yield d


def overlaps(s1: time, e1: time, s2: time, e2: time) -> bool:
    return s1 < e2 and s2 < e1
