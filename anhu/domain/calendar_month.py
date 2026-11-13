"""Chiều thời gian (date dimension) cho bảng công: luôn đủ mọi ngày trong tháng."""
import calendar
from datetime import date, timedelta


def month_days(year: int, month: int) -> list[date]:
    n = calendar.monthrange(year, month)[1]  # xét năm nhuận
    return [date(year, month, d) for d in range(1, n + 1)]


def month_bounds(year: int, month: int) -> tuple[date, date]:
    days = month_days(year, month)
    return days[0], days[-1]


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # thứ Hai


def daterange(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)
