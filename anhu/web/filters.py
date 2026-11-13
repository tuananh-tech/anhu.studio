from datetime import date, time

from flask import g

from ..domain.recurrence import WEEKDAY_LABEL
from ..domain.timesheet import KIND_LABEL, KINDS
from ..services.schedule import CANCEL_REASONS, STATUS_LABEL


def register(app):
    @app.template_filter("dmy")
    def dmy(d):
        return d.strftime("%d/%m/%Y") if isinstance(d, date) else (d or "")

    @app.template_filter("dm")
    def dm(d):
        return d.strftime("%d/%m") if isinstance(d, date) else (d or "")

    @app.template_filter("hm")
    def hm(t):
        return t.strftime("%H:%M") if isinstance(t, time) else (t or "")

    @app.template_filter("g")
    def gfmt(t):
        return f"{t.hour}g{t.minute:02d}" if isinstance(t, time) else ""

    @app.template_filter("money")
    def money(v):
        if v is None:
            return "—"
        return f"{int(v):,}".replace(",", ".") + " ₫"

    @app.template_filter("weekday")
    def weekday(d):
        return WEEKDAY_LABEL[d.weekday()] if isinstance(d, date) else WEEKDAY_LABEL[int(d)]

    @app.template_filter("kind")
    def kind(code):
        return (g.get("kind_label") or KIND_LABEL).get(code, code or "")

    @app.template_filter("vnd")
    def vnd(v):
        return "" if v is None else f"{int(v):,}".replace(",", ".")

    @app.context_processor
    def ctx():
        return {"KIND_LABEL": g.get("kind_label") or KIND_LABEL, "STATUS_LABEL": g.get("status_label") or STATUS_LABEL,
                "CANCEL_REASONS": g.get("cancel_label") or CANCEL_REASONS, "WEEKDAY_LABEL": WEEKDAY_LABEL,
                "KINDS": g.get("kinds_active") or list(KINDS)}
