"""Các trang: Tổng quan, Lịch dạy, Buổi dạy, Học viên, Chấm công, Đối soát, Cài đặt."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from flask import abort, flash, g, redirect, render_template, request, send_file, url_for

from ..db import rows, scalar
from ..domain.calendar_month import month_bounds, week_start
from ..domain.excel_export import blank_template_xlsx
from ..domain.pay import CATEGORY_LABEL
from ..domain.reconcile import CATEGORY_LABEL as RECON_LABEL
from ..domain.reconcile import FIELDS, preview, suggest_mapping
from ..services import BizError
from ..services import admin, core, reports, schedule
from . import (attendees_from_form, db, handle_errors, login_required, owner_ctx, parse_date, parse_time, today,
               ym_from_args)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(data: bytes, name: str):
    return send_file(io.BytesIO(data), mimetype=XLSX, as_attachment=True, download_name=name)


def _get_or_404(fn, *a):
    try:
        return fn(*a)
    except BizError:
        abort(404)


def register(app):
    # ======================= TỔNG QUAN =======================
    @app.get("/")
    @login_required
    def dashboard():
        t = today()
        y, m = ym_from_args(t)
        with db() as c:
            owner = owner_ctx(c)
            a, b = month_bounds(y, m)
            if g.owner["can_write"]:
                schedule.ensure_planned(c, owner, a, max(b, t + timedelta(days=7)))
            k = reports.dashboard(c, owner, y, m, t)
            todays = schedule.planned_between(c, owner, t, t)
            pending = [p for p in schedule.planned_between(c, owner, a, t) if p["status"] in ("planned", "needs_review")]
            view, recs, pay = reports.timesheet(c, owner, y, m, "center")
            recon = rows(c, """SELECT i.*, m.filename FROM reconciliation_items i JOIN reconciliation_imports m ON m.id = i.import_id
                               WHERE i.owner_id = :o AND i.resolution = 'pending' AND m.year = :y AND m.month = :m
                               ORDER BY i.day LIMIT 8""", o=owner, y=y, m=m)
        return render_template("dashboard.html", k=k, todays=todays, pending=pending[-10:][::-1], pending_n=len(pending),
                               pay=pay, rules=view.rules, y=y, m=m, today=t, recon=recon, RECON_LABEL=RECON_LABEL)

    # ======================= LỊCH DẠY =======================
    @app.get("/lich")
    @login_required
    def calendar():
        mode = request.args.get("che_do", "tuan")
        d = parse_date(request.args.get("ngay"), today())
        tt = request.args.get("loai") or None
        if mode == "ngay":
            a = b = d
        elif mode == "thang":
            a, b = month_bounds(d.year, d.month)
            a, b = week_start(a), week_start(b) + timedelta(days=6)
        else:
            mode, a = "tuan", week_start(d)
            b = a + timedelta(days=6)
        with db() as c:
            owner = owner_ctx(c)
            if g.owner["can_write"]:
                schedule.ensure_planned(c, owner, a, b)
            items = schedule.planned_between(c, owner, a, b, tt)
            locs = core.locations(c, owner)
        t = today()
        for p in items:
            p["disp"] = schedule.display_status(p, t)
        days = [a + timedelta(days=i) for i in range((b - a).days + 1)]
        by_day = {x: [p for p in items if p["session_date"] == x] for x in days}
        step = {"ngay": 1, "tuan": 7}.get(mode)
        prev_d = d - timedelta(days=step) if step else (d.replace(day=1) - timedelta(days=1)).replace(day=1)
        next_d = d + timedelta(days=step) if step else (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        return render_template("calendar.html", mode=mode, d=d, days=days, by_day=by_day, tt=tt, prev_d=prev_d, next_d=next_d,
                               today=t, locs=locs, week_a=week_start(d))

    @app.post("/lich/sao-chep-tuan")
    @login_required
    @handle_errors("calendar")
    def copy_week():
        d = parse_date(request.form.get("tuan"))
        with db("sao chép tuần") as c:
            n = schedule.copy_week(c, owner_ctx(c), week_start(d))
        flash(f"Đã sao chép {n} ca ngoài lịch lặp sang tuần sau (ca theo lịch lặp tự sinh sẵn).", "ok")
        return redirect(url_for("calendar", ngay=(week_start(d) + timedelta(days=7)).isoformat()))

    @app.get("/ca/<uuid:pid>")
    @login_required
    def planned_detail(pid):
        with db() as c:
            owner = owner_ctx(c)
            p = _get_or_404(schedule.planned, c, pid)
            p["disp"] = schedule.display_status(p, today())
            students = core.search_students(c, owner, status="active")
            locs = core.locations(c, owner)
            log = core.audit_for(c, "planned_sessions", pid, 20)
            linked = rows(c, "SELECT id, session_date, start_time, status FROM planned_sessions WHERE rescheduled_from_id = :i "
                             "OR makeup_for_id = :i OR id = :r OR id = :mk",
                          i=pid, r=p["rescheduled_from_id"], mk=p["makeup_for_id"])
        return render_template("planned.html", p=p, students=students, locs=locs, log=log, linked=linked)

    @app.post("/ca/<uuid:pid>/<action>")
    @login_required
    @handle_errors("calendar")
    def planned_action(pid, action):
        f = request.form
        reason = f.get("reason") or {"xac-nhan": "xác nhận buổi dạy", "huy": "nghỉ/hủy", "doi": "dời lịch", "sua": "sửa riêng ca",
                                     "bu": "tạo dạy bù", "khoi-phuc": "khôi phục ca"}.get(action)
        with db(reason) as c:
            owner = owner_ctx(c)
            if action == "xac-nhan":
                aid = schedule.confirm(c, owner, g.uid, pid, attendees_from_form(c, owner), start=parse_time(f.get("start")),
                                       end=parse_time(f.get("end")), session_kind=f.get("kind") or None,
                                       notes=f.get("notes") or None, override_reason=f.get("override_reason"))
                st = schedule.actual(c, aid)["status"]
                flash("Đã xác nhận buổi dạy." if st == "confirmed" else
                      "Đã lưu nhưng ở trạng thái Cần kiểm tra (chưa có học viên hiện diện) — chưa tính công.",
                      "ok" if st == "confirmed" else "warn")
                return redirect(url_for("actual_detail", aid=aid))
            if action == "huy":
                schedule.cancel(c, pid, f.get("cancel_reason"), f.get("cancel_note"))
                flash("Đã đánh dấu nghỉ/hủy. Ca này không được tính công.", "ok")
            elif action == "doi":
                nid = schedule.reschedule(c, owner, pid, day=parse_date(f.get("day")), start=parse_time(f.get("start")),
                                          end=parse_time(f.get("end")), location_id=f.get("location_id") or None,
                                          override_reason=f.get("override_reason"))
                flash("Đã dời lịch. Ca mới cần được xác nhận khi dạy xong.", "ok")
                return redirect(url_for("planned_detail", pid=nid))
            elif action == "sua":
                exp = None
                if f.get("edit_students"):
                    exp = schedule.expected_from_form(c, owner, f.getlist("student_ids"), f.get("extra_names", ""))
                schedule.edit_single(c, owner, pid, start=parse_time(f.get("start")), end=parse_time(f.get("end")),
                                     location_id=f.get("location_id") or None, expected=exp, notes=f.get("notes"),
                                     override_reason=f.get("override_reason"), session_kind=f.get("kind") or None)
                flash("Đã sửa riêng ca này (các tuần khác không đổi).", "ok")
            elif action == "bu":
                nid = schedule.makeup(c, owner, pid, day=parse_date(f.get("day")), start=parse_time(f.get("start")),
                                      end=parse_time(f.get("end")), location_id=f.get("location_id") or None,
                                      override_reason=f.get("override_reason"))
                flash("Đã tạo ca dạy bù.", "ok")
                return redirect(url_for("planned_detail", pid=nid))
            elif action == "khoi-phuc":
                schedule.restore(c, pid)
                flash("Đã khôi phục ca.", "ok")
            else:
                abort(404)
        return redirect(url_for("planned_detail", pid=pid))

    @app.route("/lich/lap", methods=["GET", "POST"])
    @login_required
    @handle_errors("templates_page")
    def templates_page():
        with db("tạo lịch lặp" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            if request.method == "POST":
                f = request.form
                exp = schedule.expected_from_form(c, owner, f.getlist("student_ids"), f.get("extra_names", ""))
                days = f.getlist("weekday")
                if not days:
                    raise BizError("Chọn ít nhất một thứ trong tuần.")
                for wd in days:
                    schedule.create_template(c, owner, weekday=int(wd), start=parse_time(f.get("start")), end=parse_time(f.get("end")),
                                             valid_from=parse_date(f.get("valid_from"), today()), valid_to=parse_date(f.get("valid_to")),
                                             location_id=f.get("location_id"), class_group=f.get("class_group") or None,
                                             session_kind=f.get("kind") or "regular", expected=exp, notes=f.get("notes") or None,
                                             override_reason=f.get("override_reason"))
                flash(f"Đã tạo {len(days)} lịch lặp. Ca dự kiến sẽ tự sinh trên lịch (chưa phải công).", "ok")
                return redirect(url_for("templates_page"))
            tpls = schedule.templates(c, owner)
            locs = core.locations(c, owner)
            students = core.search_students(c, owner, status="active")
            t = today()
            exceptions = rows(c, """SELECT p.id, p.session_date, p.start_time, p.end_time, p.status, p.is_exception, p.cancel_reason,
                                    p.cancel_note, p.rescheduled_from_id, p.makeup_for_id, p.template_id, l.name AS location_name
                                    FROM planned_sessions p LEFT JOIN locations l ON l.id = p.location_id
                                    WHERE p.owner_id = :o AND p.session_date BETWEEN :a AND :b
                                    AND (p.status IN ('cancelled','rescheduled') OR p.is_exception OR p.makeup_for_id IS NOT NULL
                                         OR p.rescheduled_from_id IS NOT NULL OR p.template_id IS NULL)
                                    ORDER BY p.session_date DESC, p.start_time LIMIT 80""",
                              o=owner, a=t - timedelta(days=60), b=t + timedelta(days=60))
            groups = admin.groups(c, owner)
        return render_template("templates.html", tpls=tpls, locs=locs, students=students, today=t, exceptions=exceptions,
                               groups=groups)

    @app.post("/lich/lap/<uuid:tid>/<action>")
    @login_required
    @handle_errors("templates_page")
    def template_action(tid, action):
        f = request.form
        with db(f.get("reason") or ("đổi cả chuỗi" if action == "doi" else "kết thúc chuỗi")) as c:
            owner = owner_ctx(c)
            if action == "doi":
                schedule.change_series(c, owner, tid, parse_date(f.get("effective_from")),
                                       start=parse_time(f.get("start")), end=parse_time(f.get("end")),
                                       location_id=f.get("location_id") or None,
                                       weekday=int(f["weekday"]) if f.get("weekday") else None)
                flash("Đã đổi chuỗi từ ngày hiệu lực; các ca trước đó giữ nguyên.", "ok")
            elif action == "ket-thuc":
                schedule.end_series(c, tid, parse_date(f.get("last_day")))
                flash("Đã đặt ngày kết thúc cho chuỗi.", "ok")
            else:
                abort(404)
        return redirect(url_for("templates_page"))

    # ======================= BUỔI DẠY =======================
    def _filters():
        a = request.args
        t = today()
        y, m = ym_from_args(t)
        fa, fb = month_bounds(y, m)
        return {"a": parse_date(a.get("tu"), fa), "b": parse_date(a.get("den"), fb), "teaching_type": a.get("loai") or None,
                "location_id": a.get("co_so") or None, "kind": a.get("loai_ca") or None, "status": a.get("trang_thai") or None,
                "payment": a.get("thu_tien") or None, "student_id": a.get("hoc_vien") or None, "q": a.get("q") or None,
                "class_group": a.get("lop") or None, "recon": a.get("doi_soat") or None}

    @app.get("/buoi-day")
    @login_required
    def sessions_page():
        flt = _filters()
        t = today()
        with db() as c:
            owner = owner_ctx(c)
            if g.owner["can_write"]:
                schedule.ensure_planned(c, owner, flt["a"], min(flt["b"], t))
            pending = [p for p in schedule.planned_between(c, owner, flt["a"], min(flt["b"], t), flt["teaching_type"])
                       if p["status"] in ("planned", "needs_review")]
            acts = schedule.search_actual(c, owner, **flt)
            locs = core.locations(c, owner)
            students = core.search_students(c, owner)
        return render_template("sessions.html", pending=pending, acts=acts, flt=flt, locs=locs, students=students, today=t,
                               args=request.args)

    @app.post("/buoi-day/xem-truoc")
    @login_required
    @handle_errors("sessions_page")
    def bulk_preview():
        ids = request.form.getlist("pid")
        if not ids:
            raise BizError("Chưa chọn ca nào.")
        with db() as c:
            owner_ctx(c)
            items = [schedule.planned(c, i) for i in ids]
        return render_template("bulk_confirm.html", items=items)

    @app.post("/buoi-day/xac-nhan-nhieu")
    @login_required
    @handle_errors("sessions_page")
    def bulk_confirm():
        ids = request.form.getlist("pid")
        with db("xác nhận nhiều ca theo kế hoạch") as c:
            owner = owner_ctx(c)
            res = schedule.confirm_as_planned(c, owner, g.uid, ids)
            review = sum(1 for aid in res if schedule.actual(c, aid)["status"] != "confirmed")
        flash(f"Đã xác nhận {len(res) - review} ca theo kế hoạch" + (f"; {review} ca thiếu học viên -> Cần kiểm tra." if review else "."),
              "ok" if not review else "warn")
        return redirect(url_for("sessions_page"))

    @app.post("/buoi-day/them")
    @login_required
    @handle_errors("sessions_page")
    def add_extra():
        f = request.form
        with db("thêm ca phát sinh") as c:
            owner = owner_ctx(c)
            kw = dict(day=parse_date(f.get("day")), start=parse_time(f.get("start")), end=parse_time(f.get("end")),
                      location_id=f.get("location_id"), session_kind=f.get("kind") or "regular",
                      notes=f.get("notes") or None, override_reason=f.get("override_reason"))
            att = attendees_from_form(c, owner)
            if f.get("confirm_now"):
                aid = schedule.add_and_confirm_extra(c, owner, g.uid, attendees=att, **kw)
                flash("Đã thêm và xác nhận ca phát sinh.", "ok")
                return redirect(url_for("actual_detail", aid=aid))
            kw.pop("session_kind")
            pid = schedule.add_one_off(c, owner, session_kind=f.get("kind") or "regular", expected=att, **kw)
            flash("Đã thêm ca ngoài lịch (chưa tính công cho tới khi xác nhận).", "ok")
            return redirect(url_for("planned_detail", pid=pid))

    @app.route("/buoi/<uuid:aid>", methods=["GET", "POST"])
    @login_required
    @handle_errors("sessions_page")
    def actual_detail(aid):
        f = request.form
        with db(f.get("reason") or "sửa buổi dạy" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            a = _get_or_404(schedule.actual, c, aid)
            if request.method == "POST":
                if not (f.get("reason") or "").strip():
                    raise BizError("Nhập lý do chỉnh sửa (ghi vào nhật ký).")
                schedule.update_actual(c, owner, aid, attendees=attendees_from_form(c, owner), start=parse_time(f.get("start")),
                                       end=parse_time(f.get("end")), session_kind=f.get("kind"), notes=f.get("notes") or None,
                                       status=f.get("status") or None, override_reason=f.get("override_reason"),
                                       payment_status=f.get("payment_status") or None,
                                       paid_amount=f.get("paid_amount") or None)
                flash("Đã lưu thay đổi (có ghi nhật ký).", "ok")
                return redirect(url_for("actual_detail", aid=aid))
            students = core.search_students(c, owner)
            log = core.audit_for(c, "actual_sessions", aid, 30)
            att_log = rows(c, """SELECT a.*, u.display_name AS actor FROM audit_log a LEFT JOIN app_users u ON u.id = a.actor_id
                                 WHERE a.table_name = 'session_attendees' AND (a.after ->> 'session_id' = :s OR a.before ->> 'session_id' = :s)
                                 ORDER BY a.id DESC LIMIT 40""", s=str(aid))
            locked = reports.period(c, owner, a["session_date"].year, a["session_date"].month)
        return render_template("actual.html", a=a, students=students, log=log, att_log=att_log,
                               locked=locked and locked["status"] == "locked")

    @app.post("/buoi/<uuid:aid>/huy-xac-nhan")
    @login_required
    @handle_errors("sessions_page")
    def actual_unconfirm(aid):
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            raise BizError("Nhập lý do hủy xác nhận.")
        with db(reason) as c:
            owner_ctx(c)
            schedule.unconfirm(c, aid)
        flash("Đã hủy xác nhận; ca dự kiến quay về trạng thái Dự kiến.", "ok")
        return redirect(url_for("sessions_page"))

    # ======================= HỌC VIÊN =======================
    @app.route("/hoc-vien", methods=["GET", "POST"])
    @login_required
    @handle_errors("students_page")
    def students_page():
        a = request.args
        with db("thêm học viên" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            if request.method == "POST":
                f = request.form
                sid = core.add_student(c, owner, f.get("display_name"), class_group=f.get("class_group"), subject=f.get("subject"),
                                       start_date=parse_date(f.get("start_date")), notes=f.get("notes"), code=f.get("code"))
                if f.get("location_id"):
                    core.enroll(c, owner, sid, f.get("location_id"), f.get("class_group") or None)
                if f.get("group_id"):
                    admin.add_member(c, owner, f.get("group_id"), sid, parse_date(f.get("start_date")))
                flash("Đã thêm học viên.", "ok")
                return redirect(url_for("student_detail", sid=sid))
            lst = core.search_students(c, owner, a.get("q", ""), a.get("trang_thai") or None, a.get("co_so") or None,
                                       a.get("lop") or None, a.get("loai") or None)
            if a.get("nhom"):
                ids = {str(r["student_id"]) for r in rows(c, "SELECT student_id FROM class_group_members WHERE group_id = :g "
                                                             "AND left_on IS NULL", g=a.get("nhom"))}
                lst = [s for s in lst if str(s["id"]) in ids]
            locs = core.locations(c, owner)
            grp = admin.groups(c, owner)
            subs = admin.subjects(c, owner)
        if a.get("xuat") == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["Tên", "Lớp/nhóm", "Môn", "Trạng thái", "Cơ sở", "Ngày bắt đầu"])
            for s in lst:
                w.writerow([s["display_name"], s["class_group"] or "", s["subject"] or "", s["status"], s["places"] or "",
                            s["start_date"] or ""])
            return send_file(io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8")), mimetype="text/csv",
                             as_attachment=True, download_name="hoc-vien.csv")
        return render_template("students.html", lst=lst, locs=locs, args=a, groups=grp, subjects=subs)

    @app.route("/hoc-vien/<uuid:sid>", methods=["GET", "POST"])
    @login_required
    @handle_errors("students_page")
    def student_detail(sid):
        with db("sửa hồ sơ học viên" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            if request.method == "POST":
                f = request.form
                act = f.get("act") or ("enroll" if f.get("location_id") else "profile")
                if act == "enroll":
                    core.enroll(c, owner, sid, f.get("location_id"), f.get("class_group") or None)
                    msg = "Đã thêm nơi học."
                elif act == "profile":
                    core.update_student(c, sid, display_name=f.get("display_name"), class_group=f.get("class_group"),
                                        subject=f.get("subject"), start_date=parse_date(f.get("start_date")),
                                        status=f.get("status"), notes=f.get("notes"), code=f.get("code"))
                    msg = "Đã lưu hồ sơ. Tên mới áp dụng cho ca mới; ca đã chốt giữ nguyên."
                elif act in ("archive", "unarchive"):
                    core.archive_student(c, sid, act == "archive")
                    msg = ("Đã lưu trữ học viên (ẩn khỏi danh sách chọn, lịch sử buổi dạy giữ nguyên)."
                           if act == "archive" else "Đã khôi phục học viên.")
                elif act == "contact":
                    admin.save_contact(c, owner, sid, f.get("name"), f.get("relation"), f.get("phone"), f.get("email"),
                                       f.get("notes"), bool(f.get("is_primary")), f.get("contact_id") or None)
                    msg = "Đã lưu người liên hệ."
                elif act == "contact-archive":
                    admin.archive_contact(c, f.get("contact_id"), True)
                    msg = "Đã ẩn người liên hệ."
                elif act == "group":
                    admin.add_member(c, owner, f.get("group_id"), sid, parse_date(f.get("joined_on")))
                    msg = "Đã thêm vào lớp/nhóm."
                elif act == "leave":
                    admin.leave_member(c, f.get("member_id"), parse_date(f.get("left_on")))
                    msg = "Đã ghi nhận rời lớp/nhóm."
                else:
                    abort(404)
                flash(msg, "ok")
                return redirect(url_for("student_detail", sid=sid))
            st = _get_or_404(core.student_stats, c, sid)
            locs = core.locations(c, owner)
            extra = dict(contacts=admin.contacts(c, sid), groups=admin.student_groups(c, sid), all_groups=admin.groups(c, owner),
                         subjects=admin.subjects(c, owner))
        return render_template("student.html", s=st, locs=locs, **extra)

    # ======================= CHẤM CÔNG =======================
    @app.get("/cham-cong")
    @login_required
    def timesheet_page():
        y, m = ym_from_args(today())
        tab = request.args.get("tab", "excel")
        tt = request.args.get("loai", "center")
        with db() as c:
            owner = owner_ctx(c)
            per = reports.period(c, owner, y, m)
            snap = reports.locked_snapshot(c, owner, y, m) if tt == "center" else None
            if snap:   # kỳ đã chốt: hiển thị đúng bản chốt, không tính lại
                view, pay = reports.snapshot_view(snap)
            else:
                view, recs, pay = reports.timesheet(c, owner, y, m, None if tt == "all" else tt)
                view.from_snapshot = False
            unconfirmed = scalar(c, """SELECT count(*) FROM planned_sessions WHERE owner_id=:o AND status IN ('planned','needs_review')
                                       AND session_date BETWEEN :a AND :b AND session_date <= :t""",
                                 o=owner, a=month_bounds(y, m)[0], b=month_bounds(y, m)[1], t=today())
        return render_template("timesheet.html", view=view, pay=pay, y=y, m=m, tab=tab, tt=tt, per=per,
                               unconfirmed=unconfirmed, CATEGORY_LABEL=CATEGORY_LABEL)

    @app.get("/cham-cong/xuat")
    @login_required
    def timesheet_export():
        y, m = ym_from_args(today())
        with db() as c:
            owner = owner_ctx(c)
            prefix = admin.export_options(c, owner)["file_prefix"]
            locked = reports.locked_export(c, owner, y, m) if not request.args.get("trung_tam") else None
            if locked:  # kỳ đã chốt: xuất lại đúng file đã chốt (cùng mã băm)
                return _xlsx(bytes(locked["export_bytes"]), f"{prefix}_{y}-{m:02d}_DA_CHOT_{locked['export_sha256'][:8]}.xlsx")
            data = reports.export_timesheet(c, owner, y, m, request.args.get("trung_tam") or None)
        return _xlsx(data, f"{prefix}_{y}-{m:02d}.xlsx")

    @app.get("/cham-cong/ban-chot")
    @login_required
    def timesheet_locked_file():
        y, m = ym_from_args(today())
        with db() as c:
            r = reports.locked_export(c, owner_ctx(c), y, m)
        if not r:
            abort(404)
        return _xlsx(bytes(r["export_bytes"]), f"ChamCong_{y}-{m:02d}_DA_CHOT_{r['export_sha256'][:8]}.xlsx")

    @app.post("/cham-cong/<action>")
    @login_required
    @handle_errors("timesheet_page")
    def timesheet_action(action):
        y, m = int(request.form["y"]), int(request.form["m"])
        reason = (request.form.get("reason") or "").strip()
        with db(reason or "chốt kỳ") as c:
            owner = owner_ctx(c)
            if action == "chot":
                sha, review = reports.lock_period(c, owner, g.uid, y, m)
                flash(f"Đã chốt kỳ {m:02d}/{y}. Mã băm file xuất: {sha[:16]}…" +
                      (f" Lưu ý: {review} ca Cần kiểm tra không được tính." if review else ""), "ok")
            elif action == "mo":
                reports.unlock_period(c, owner, y, m, reason)
                flash("Đã mở kỳ (lý do được ghi nhật ký).", "ok")
            else:
                abort(404)
        return redirect(url_for("timesheet_page", thang=f"{y}-{m:02d}"))

    # ======================= ĐỐI SOÁT =======================
    @app.route("/doi-soat", methods=["GET", "POST"])
    @login_required
    @handle_errors("recon_page")
    def recon_page():
        with db("tải file đối soát" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            if request.method == "POST":
                f = request.files.get("file")
                if not f or not f.filename.lower().endswith(".xlsx"):
                    raise BizError("Chọn file .xlsx của trung tâm.")
                y, m = map(int, request.form["thang"].split("-"))
                iid = reports.recon_upload(c, owner, request.form.get("center_id") or None, y, m, f.filename, f.read())
                return redirect(url_for("recon_mapping", iid=iid))
            imps = rows(c, """SELECT i.id, i.filename, i.year, i.month, i.status, i.created_at, c.name AS center_name,
                              (SELECT count(*) FROM reconciliation_items x WHERE x.import_id = i.id AND x.resolution = 'pending') AS pending
                              FROM reconciliation_imports i LEFT JOIN centers c ON c.id = i.center_id
                              WHERE i.owner_id = :o ORDER BY i.created_at DESC""", o=owner)
            centers = core.centers(c, owner)
        return render_template("recon_list.html", imps=imps, centers=centers, today=today())

    @app.route("/doi-soat/<uuid:iid>/mapping", methods=["GET", "POST"])
    @login_required
    @handle_errors("recon_page")
    def recon_mapping(iid):
        with db("chạy đối soát" if request.method == "POST" else None) as c:
            owner = owner_ctx(c)
            imp = _get_or_404(reports.recon_import, c, iid)
            pv = preview(bytes(imp["file_bytes"]), 14)
            if request.method == "POST":
                f = request.form
                mapping = {"sheet": f.get("sheet"), "start_row": int(f.get("start_row") or 2),
                           "columns": {k: f.get(f"col_{k}") for k in FIELDS if f.get(f"col_{k}")}}
                if f.get("save_profile") and f.get("profile_name"):
                    admin.save_mapping(c, owner, f.get("profile_name"), imp["center_id"], mapping, is_default=True)
                items = reports.recon_run(c, owner, iid, mapping)
                bad = sum(1 for i in items if i.category != "match")
                flash(f"Đã đối soát: {len(items) - bad} mục khớp, {bad} mục chênh lệch cần duyệt.", "ok" if not bad else "warn")
                return redirect(url_for("recon_detail", iid=iid))
            mapping = (imp["mapping"] or admin.default_mapping(c, owner, imp["center_id"])
                       or reports.last_mapping(c, owner, imp["center_id"]) or suggest_mapping(pv))
            profiles = admin.mappings(c, owner)
        sheet = request.args.get("sheet") or mapping.get("sheet") or next(iter(pv))
        return render_template("recon_mapping.html", imp=imp, pv=pv, sheet=sheet, mapping=mapping, FIELDS=FIELDS,
                               letters=[chr(65 + i) for i in range(16)], profiles=profiles)

    @app.get("/doi-soat/<uuid:iid>")
    @login_required
    def recon_detail(iid):
        with db() as c:
            owner_ctx(c)
            imp = _get_or_404(reports.recon_import, c, iid)
            items = reports.recon_items(c, iid, request.args.get("loai") or None, request.args.get("duyet") or None)
            counts = rows(c, "SELECT category, resolution, count(*) AS n FROM reconciliation_items WHERE import_id = :i "
                             "GROUP BY category, resolution", i=iid)
        return render_template("recon_detail.html", imp=imp, items=items, counts=counts, RECON_LABEL=RECON_LABEL,
                               RES_LABEL=reports.RES_LABEL, args=request.args)

    @app.post("/doi-soat/muc/<uuid:item_id>")
    @login_required
    @handle_errors("recon_page")
    def recon_resolve(item_id):
        f = request.form
        with db(f"duyệt chênh lệch: {f.get('resolution')}") as c:
            owner_ctx(c)
            reports.recon_resolve(c, g.uid, item_id, f.get("resolution"), f.get("note"))
        flash("Đã ghi quyết định. Dữ liệu cá nhân không bị thay đổi — nếu cần sửa, mở buổi dạy và sửa có lý do.", "ok")
        return redirect(request.referrer or url_for("recon_page"))

    @app.get("/doi-soat/<uuid:iid>/bien-ban")
    @login_required
    def recon_report(iid):
        with db() as c:
            owner_ctx(c)
            imp = _get_or_404(reports.recon_import, c, iid)
            data = reports.recon_report_xlsx(c, iid)
        return _xlsx(data, f"BienBanDoiSoat_{imp['year']}-{imp['month']:02d}.xlsx")

    @app.get("/doi-soat/<uuid:iid>/file-goc")
    @login_required
    def recon_original(iid):
        with db() as c:
            owner_ctx(c)
            imp = _get_or_404(reports.recon_import, c, iid)
        return _xlsx(bytes(imp["file_bytes"]), imp["filename"])

    @app.get("/mau-trong")
    @login_required
    def blank_template():
        return _xlsx(blank_template_xlsx(), "MauChamCong_trong.xlsx")

    from . import admin_pages, report_pages
    admin_pages.register(app)
    report_pages.register(app)
