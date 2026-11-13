"""Báo cáo tổng hợp (chỉ đọc). Chỉ tính buổi dạy ĐÃ XÁC NHẬN; kỳ đã chốt lấy số liệu từ bản chốt."""
from __future__ import annotations

import io
from datetime import date

from ..domain.xlsx_safe import protect_workbook
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..db import rows
from ..domain.calendar_month import month_bounds
from . import reports


def year_overview(conn, owner, year: int) -> list[dict]:
    out = []
    for m in range(1, 13):
        a, b = month_bounds(year, m)
        per = reports.period(conn, owner, year, m)
        snap = reports.locked_snapshot(conn, owner, year, m)
        if snap:
            totals, pay_total, review = snap.get("totals", {}), snap.get("pay_total"), snap.get("needs_review_count", 0)
            j = sum(1 for d in snap.get("detail", []) if d.get("column") == "J")
        else:
            view, recs, pay = reports.timesheet(conn, owner, year, m, "center")
            totals = view.totals
            pay_total = str(pay.total) if pay and pay.complete and pay.by_category else None
            review = len(view.review)
            j = sum(1 for c in view.placements.values() if c == "J")
        s = rows(conn, """SELECT count(*) FILTER (WHERE status='confirmed' AND teaching_type='center') AS center_n,
                                 count(*) FILTER (WHERE status='confirmed' AND teaching_type='private') AS private_n,
                                 COALESCE(sum(duration_min) FILTER (WHERE status='confirmed'), 0) AS minutes,
                                 COALESCE(sum(paid_amount) FILTER (WHERE status='confirmed' AND teaching_type='private'), 0) AS paid,
                                 COALESCE(sum(price_amount - paid_amount) FILTER (WHERE status='confirmed' AND teaching_type='private'
                                          AND payment_status IN ('unpaid','partial')), 0) AS due
                          FROM actual_sessions WHERE owner_id = :o AND session_date BETWEEN :a AND :b""", o=owner, a=a, b=b)[0]
        rp = rows(conn, """SELECT count(*) AS n FROM reconciliation_items i JOIN reconciliation_imports r ON r.id = i.import_id
                           WHERE i.owner_id = :o AND r.year = :y AND r.month = :m AND i.resolution = 'pending'""",
                  o=owner, y=year, m=m)[0]["n"]
        out.append({"month": m, "locked": bool(per and per.get("status") == "locked"), "totals": totals, "j": j,
                    "pay_total": pay_total, "review": review, "recon_pending": rp, **s})
    return out


def by_student(conn, owner, a: date, b: date, teaching_type=None) -> list[dict]:
    return rows(conn, """SELECT COALESCE(st.display_name, sa.name) AS name, sa.student_id, st.code,
                                count(DISTINCT a.id) AS sessions, COALESCE(sum(a.duration_min), 0) AS minutes,
                                count(DISTINCT a.id) FILTER (WHERE a.teaching_type = 'center') AS center_n,
                                count(DISTINCT a.id) FILTER (WHERE a.teaching_type = 'private') AS private_n,
                                count(DISTINCT a.id) FILTER (WHERE a.session_kind = 'makeup') AS makeup_n,
                                min(a.session_date) AS first_day, max(a.session_date) AS last_day
                         FROM session_attendees sa JOIN actual_sessions a ON a.id = sa.session_id
                         LEFT JOIN students st ON st.id = sa.student_id
                         WHERE a.owner_id = :o AND a.status = 'confirmed' AND a.session_date BETWEEN :a AND :b
                           AND (CAST(:tt AS text) IS NULL OR a.teaching_type = :tt)
                         GROUP BY 1, 2, 3 ORDER BY sessions DESC, name""", o=owner, a=a, b=b, tt=teaching_type)


def by_location(conn, owner, a: date, b: date) -> list[dict]:
    return rows(conn, """SELECT a.location_label AS location, a.teaching_type, count(*) AS sessions,
                                COALESCE(sum(a.duration_min), 0) AS minutes,
                                COALESCE(sum((SELECT count(*) FROM session_attendees sa WHERE sa.session_id = a.id)), 0) AS attendances,
                                count(*) FILTER (WHERE a.session_kind IN ('trial')) AS trial_n,
                                count(*) FILTER (WHERE a.session_kind IN ('makeup')) AS makeup_n
                         FROM actual_sessions a WHERE a.owner_id = :o AND a.status = 'confirmed' AND a.session_date BETWEEN :a AND :b
                         GROUP BY 1, 2 ORDER BY 2, 3 DESC""", o=owner, a=a, b=b)


def private_payments(conn, owner, a: date, b: date) -> list[dict]:
    return rows(conn, """SELECT a.id, a.session_date, a.start_time, a.end_time, a.location_label, a.price_amount, a.paid_amount,
                                a.payment_status,
                                (SELECT string_agg(sa.name, ', ' ORDER BY sa.position) FROM session_attendees sa WHERE sa.session_id = a.id) AS names
                         FROM actual_sessions a WHERE a.owner_id = :o AND a.status = 'confirmed' AND a.teaching_type = 'private'
                           AND a.session_date BETWEEN :a AND :b ORDER BY a.session_date, a.start_time""", o=owner, a=a, b=b)


HEAD = Font(bold=True)
FILL = PatternFill("solid", fgColor="FFF3E9CF")


def to_xlsx(title: str, header: list[str], data: list[list]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    ws.append(header)
    for c in ws[1]:
        c.font, c.fill = HEAD, FILL
    for r in data:
        ws.append(r)
        for c in ws[ws.max_row]:
            if isinstance(c.value, date):
                c.number_format = "dd/mm/yyyy"
    for i, h in enumerate(header, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = max(10, min(40, len(h) + 4))
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    protect_workbook(wb)
    wb.save(buf)
    return buf.getvalue()
