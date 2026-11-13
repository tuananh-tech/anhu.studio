"""Cài đặt & quản trị: mọi cấu hình vận hành thường ngày làm trên web (không sửa code/SQL/JSON)."""
from __future__ import annotations

import io

from flask import abort, flash, g, redirect, render_template, request, send_file, session, url_for

from ..db import rows, scalar
from ..domain.pay import CATEGORY_LABEL, EXCEL_SUGGESTED
from ..domain.reconcile import FIELDS
from ..domain.timesheet import TimesheetRules
from ..services import BizError
from ..services import admin, backup, core, reports, schedule
from . import db, handle_errors, login_required, owner_ctx, parse_date, today, ym_from_args

SECTIONS = [
    ("tai-khoan", "Tài khoản & quyền truy cập", "Hồ sơ giáo viên, mật khẩu, tài khoản thứ hai, chia sẻ dữ liệu."),
    ("danh-muc", "Trung tâm, cơ sở & địa điểm", "Thêm, đổi tên, lưu trữ trung tâm và nơi dạy (giá trị cột CƠ SỞ)."),
    ("lop-hoc", "Môn học, lớp học & nhóm học viên", "Danh mục môn, lớp, nhóm và thành viên."),
    ("hoc-vien", "Học viên & người liên hệ", "Hồ sơ, phụ huynh/người liên hệ, lưu trữ học viên đã nghỉ."),
    ("lich", "Thời khóa biểu cố định & ngoại lệ", "Lịch lặp hằng tuần, ca nghỉ/dời/bù/sửa riêng."),
    ("loai-ca", "Loại ca dạy & trạng thái buổi học", "Đổi tên hiển thị, ẩn/hiện, thêm loại ca mới."),
    ("don-gia", "Đơn giá & ngày hiệu lực", "Giá theo trung tâm/nhóm ca/học viên; chỉ giá đã xác nhận mới tính tiền."),
    ("quy-tac", "Quy tắc chấm công", "Xếp cột 30 PHÚT / DẠY THỬ / 1–4 HV theo phiên bản có ngày hiệu lực; ca hủy; cột Còn không."),
    ("excel", "Mẫu Excel & ánh xạ dữ liệu", "Tùy chọn file xuất, hồ sơ ánh xạ file trung tâm, import lịch sử."),
    ("ky", "Kỳ chấm công", "Danh sách kỳ, chốt / mở kỳ, các phiên bản đã chốt."),
    ("nhat-ky", "Lịch sử chỉnh sửa & nhật ký", "Ai đổi gì, lúc nào, trước/sau, lý do."),
    ("sao-luu", "Sao lưu & khôi phục", "Tải bản sao lưu, kiểm tra file sao lưu, khôi phục khi chuyển máy chủ."),
    ("dong-bo", "Đồng bộ dữ liệu", "Google Forms / Sheets / Calendar — chưa triển khai trong phiên bản này."),
    ("he-thong", "Thông tin hệ thống", "Phiên bản, migration, cảnh báo vận hành."),
]
SEC_TITLE = {k: t for k, t, _ in SECTIONS}

# action POST -> mục để quay lại
ACTION_SECTION = {
    "quy-tac": "quy-tac", "xac-nhan-quy-tac": "quy-tac", "huy-ca": "quy-tac", "con-khong": "quy-tac",
    "don-gia": "don-gia", "don-gia-goi-y": "don-gia", "xac-nhan-don-gia": "don-gia", "ket-thuc-don-gia": "don-gia",
    "luu-tru-don-gia": "don-gia",
    "trung-tam": "danh-muc", "sua-trung-tam": "danh-muc", "luu-tru-trung-tam": "danh-muc", "co-so": "danh-muc",
    "sua-co-so": "danh-muc", "luu-tru-co-so": "danh-muc",
    "chia-se": "tai-khoan", "bo-chia-se": "tai-khoan", "mat-khau": "tai-khoan", "ho-so": "tai-khoan",
    "tao-tai-khoan": "tai-khoan", "khoa-tai-khoan": "tai-khoan", "dat-lai-mat-khau": "tai-khoan",
    "mon-hoc": "lop-hoc", "luu-tru-mon-hoc": "lop-hoc", "lop": "lop-hoc", "luu-tru-lop": "lop-hoc", "thanh-vien": "lop-hoc",
    "roi-lop": "lop-hoc",
    "loai-ca": "loai-ca", "them-loai-ca": "loai-ca", "nhan": "loai-ca",
    "xuat-excel": "excel", "anh-xa": "excel", "luu-tru-anh-xa": "excel",
}


def _flag(v) -> bool:
    return v in ("1", "on", "true", "yes")


def register(app):
    @app.get("/cai-dat")
    @login_required
    def settings_page():
        with db() as c:
            owner = owner_ctx(c)
            info = admin.system_info(c, owner)
        return render_template("settings_hub.html", sections=SECTIONS, info=info)

    @app.get("/cai-dat/<sec>")
    @login_required
    def settings_section(sec):
        if sec == "lich":
            return redirect(url_for("templates_page"))
        if sec == "hoc-vien":
            return redirect(url_for("students_page"))
        if sec == "ky":
            return redirect(url_for("timesheet_periods"))
        if sec not in SEC_TITLE:
            abort(404)
        ctx = {"sec": sec, "title": SEC_TITLE[sec], "today": today()}
        with db() as c:
            owner = owner_ctx(c)
            if sec == "tai-khoan":
                ctx.update(me=admin.me(c, g.uid), shares=core.my_shares(c, g.uid),
                           users=admin.list_users(c) if session.get("is_admin") else None,
                           temp=session.pop("temp_pw", None))
            elif sec == "danh-muc":
                ctx.update(centers=core.centers(c, owner, True), locs=core.locations(c, owner, True),
                           usage={str(r["location_id"]): r["n"] for r in rows(
                               c, "SELECT location_id, count(*) AS n FROM actual_sessions WHERE owner_id = :o GROUP BY location_id",
                               o=owner)})
            elif sec == "lop-hoc":
                gid = request.args.get("nhom")
                ctx.update(subjects=admin.subjects(c, owner, True), groups=admin.groups(c, owner, True),
                           locs=core.locations(c, owner), group=admin.group(c, gid) if gid else None,
                           students=core.search_students(c, owner, status="active"))
            elif sec == "loai-ca":
                ctx.update(kinds=g.kind_list, labels=admin.labels(c, owner),
                           usage={r["k"]: r["n"] for r in rows(c, "SELECT session_kind AS k, count(*) AS n FROM actual_sessions "
                                                                  "WHERE owner_id = :o GROUP BY 1", o=owner)})
            elif sec == "don-gia":
                ctx.update(rates=core.rates(c, owner), centers=core.centers(c, owner), students=core.search_students(c, owner),
                           CATEGORY_LABEL=CATEGORY_LABEL, EXCEL_SUGGESTED=EXCEL_SUGGESTED)
            elif sec == "quy-tac":
                ctx.update(st=core.settings(c, owner), rules=core.timesheet_rules(c, owner), versions=core.rule_versions(c, owner),
                           all_kinds=g.kind_list)
            elif sec == "excel":
                ctx.update(opts=admin.export_options(c, owner), maps=admin.mappings(c, owner, True), centers=core.centers(c, owner),
                           FIELDS=FIELDS, imports=rows(c, "SELECT id, filename, status, stats, created_at FROM legacy_imports "
                                                          "WHERE owner_id=:o ORDER BY created_at DESC LIMIT 10", o=owner),
                           edit=next((m for m in admin.mappings(c, owner, True) if str(m["id"]) == request.args.get("sua")), None))
            elif sec == "nhat-ky":
                a = request.args
                page = max(int(a.get("trang") or 1), 1)
                items, total = admin.audit_search(c, owner, a.get("bang") or None, a.get("hanh_dong") or None,
                                                  parse_date(a.get("tu")), parse_date(a.get("den")), a.get("q") or None, page)
                ctx.update(items=items, total=total, page=page, pages=max((total + 49) // 50, 1), args=a,
                           TABLE_LABEL=admin.TABLE_LABEL, ACTION_LABEL=admin.ACTION_LABEL)
            elif sec == "sao-luu":
                ctx.update(history=backup.history(c, owner), empty=backup.is_empty(c, owner), info=admin.system_info(c, owner))
            elif sec == "he-thong":
                ctx.update(info=admin.system_info(c, owner))
        return render_template(f"settings_{sec.replace('-', '_')}.html", **ctx)

    @app.post("/cai-dat/<action>")
    @login_required
    @handle_errors("settings_page")
    def settings_action(action):
        f = request.form
        sec = ACTION_SECTION.get(action)
        if not sec:
            abort(404)
        anchor = ""
        msg = "Đã lưu."
        with db(f.get("reason") or f"cài đặt: {action}") as c:
            owner = owner_ctx(c)
            # ---- tài khoản
            if action == "mat-khau":
                if f.get("new") != f.get("new2"):
                    raise BizError("Mật khẩu nhập lại không khớp.")
                core.change_password(c, g.uid, f.get("old", ""), f.get("new", ""))
                session["must_change"] = False
                msg = "Đã đổi mật khẩu."
            elif action == "ho-so":
                admin.update_profile(c, g.uid, f.get("display_name"), f.get("subject"))
                session["name"] = f.get("display_name").strip()
            elif action == "tao-tai-khoan":
                temp = admin.create_account(c, f.get("email"), f.get("display_name"), app.config["ALLOWED_EMAILS"])
                session["temp_pw"] = {"email": f.get("email").strip().lower(), "pw": temp}
                msg = "Đã tạo tài khoản. Gửi mật khẩu tạm cho giáo viên qua kênh riêng — hệ thống sẽ bắt đổi ở lần đăng nhập đầu."
            elif action == "khoa-tai-khoan":
                admin.set_account_active(c, f.get("user_id"), f.get("active") == "1")
                msg = "Đã mở khóa tài khoản." if f.get("active") == "1" else "Đã khóa tài khoản (dữ liệu được giữ nguyên)."
            elif action == "dat-lai-mat-khau":
                temp = admin.reset_password(c, f.get("user_id"))
                email = scalar(c, "SELECT email FROM app.admin_list_users() WHERE id = :i", i=f.get("user_id"))
                session["temp_pw"] = {"email": email, "pw": temp}
                msg = "Đã đặt lại mật khẩu tạm."
            elif action == "chia-se":
                core.share_with(c, g.uid, f.get("email", ""), _flag(f.get("can_write")),
                                lambda e: scalar(c, "SELECT app.user_id_by_email(:e)", e=e))
                msg = "Đã chia sẻ."
            elif action == "bo-chia-se":
                core.unshare(c, g.uid, f.get("grantee_id"))
                msg = "Đã ngừng chia sẻ."
            # ---- danh mục
            elif action == "trung-tam":
                core.add_center(c, owner, f.get("name"))
                msg = "Đã thêm trung tâm."
            elif action == "sua-trung-tam":
                admin.update_center(c, f.get("id"), f.get("name"), f.get("contact"), f.get("notes"))
            elif action == "luu-tru-trung-tam":
                admin.archive_center(c, f.get("id"), f.get("archived") == "1")
                msg = "Đã lưu trữ trung tâm (lịch sử giữ nguyên)." if f.get("archived") == "1" else "Đã khôi phục trung tâm."
            elif action == "co-so":
                core.add_location(c, owner, f.get("name"), f.get("kind"), f.get("center_id") or None)
                msg = "Đã thêm cơ sở / địa điểm."
            elif action == "sua-co-so":
                admin.update_location(c, f.get("id"), f.get("name"), f.get("kind"), f.get("center_id") or None, f.get("address"))
                msg = "Đã lưu. Tên mới áp dụng cho ca mới; ca đã ghi giữ nhãn cũ."
            elif action == "luu-tru-co-so":
                admin.archive_location(c, f.get("id"), f.get("archived") == "1")
                msg = "Đã lưu trữ cơ sở." if f.get("archived") == "1" else "Đã khôi phục cơ sở."
            # ---- môn / lớp / nhóm
            elif action == "mon-hoc":
                admin.save_subject(c, owner, f.get("name"), f.get("notes"), f.get("id") or None)
            elif action == "luu-tru-mon-hoc":
                admin.archive_subject(c, f.get("id"), f.get("archived") == "1")
            elif action == "lop":
                gid = admin.save_group(c, owner, f.get("name"), f.get("kind") or "class", f.get("subject_id"), f.get("location_id"),
                                       f.get("notes"), f.get("id") or None)
                anchor = f"?nhom={gid}"
            elif action == "luu-tru-lop":
                admin.archive_group(c, f.get("id"), f.get("archived") == "1")
            elif action == "thanh-vien":
                for sid in f.getlist("student_id"):
                    admin.add_member(c, owner, f.get("group_id"), sid, parse_date(f.get("joined_on")))
                anchor = f"?nhom={f.get('group_id')}"
                msg = "Đã thêm thành viên."
            elif action == "roi-lop":
                admin.leave_member(c, f.get("member_id"), parse_date(f.get("left_on")))
                anchor = f"?nhom={f.get('group_id')}"
                msg = "Đã ghi nhận rời lớp/nhóm."
            # ---- loại ca & nhãn
            elif action == "loai-ca":
                for k in g.kind_list:
                    code = k["code"]
                    if f.get(f"label_{code}") is not None:
                        admin.save_kind(c, owner, code, f.get(f"label_{code}"), bool(f.get(f"active_{code}")),
                                        f.get(f"sort_{code}") or k["sort"])
            elif action == "them-loai-ca":
                code = admin.add_custom_kind(c, owner, f.get("label"))
                msg = (f"Đã thêm loại ca (mã {code}). Loại ca mới CHƯA được tính công cho tới khi bạn đưa nó vào "
                       "một cột trong Quy tắc chấm công.")
            elif action == "nhan":
                admin.save_labels(c, owner, {k[3:]: v for k, v in f.items() if k.startswith("st_")},
                                  {k[3:]: v for k, v in f.items() if k.startswith("cr_")})
            # ---- đơn giá
            elif action == "don-gia":
                amt = f.get("amount")
                try:
                    if amt is None or float(amt) < 0:
                        raise ValueError
                except ValueError:
                    raise BizError("Số tiền phải là số không âm.")
                core.add_rate(c, owner, f.get("teaching_type", "center"), f.get("category"), amt,
                              parse_date(f.get("valid_from")), parse_date(f.get("valid_to")), f.get("center_id") or None,
                              f.get("student_id") or None, confirmed=_flag(f.get("confirmed")), note=f.get("note") or None)
                msg = "Đã thêm đơn giá." + ("" if _flag(f.get("confirmed")) else " Đơn giá CHƯA xác nhận sẽ không được dùng để tính tiền.")
            elif action == "don-gia-goi-y":
                for cat, amt in EXCEL_SUGGESTED.items():
                    core.add_rate(c, owner, "center", cat, amt, parse_date(f.get("valid_from")), None, f.get("center_id") or None,
                                  confirmed=False, note="Gợi ý từ hàng 41 file Excel gốc — CHƯA xác nhận")
            elif action == "xac-nhan-don-gia":
                core.confirm_rate(c, f.get("rate_id"), f.get("confirmed") == "1")
            elif action == "ket-thuc-don-gia":
                core.close_rate(c, f.get("rate_id"), parse_date(f.get("valid_to")))
                msg = "Đã đặt ngày kết thúc hiệu lực."
            elif action == "luu-tru-don-gia":
                core.archive_rate(c, f.get("rate_id"), f.get("archived") == "1")
            # ---- quy tắc
            elif action == "quy-tac":
                r = TimesheetRules(thirty_minute_max=int(f.get("thirty_minute_max") or 30),
                                   thirty_kinds=tuple(f.getlist("thirty_kinds")), j_kinds=tuple(f.getlist("j_kinds")),
                                   hv_kinds=tuple(f.getlist("hv_kinds")), confirmed=_flag(f.get("confirmed")))
                if not 0 <= r.thirty_minute_max <= 120:
                    raise BizError("Ngưỡng phút phải trong khoảng 0–120.")
                if set(r.j_kinds) & set(r.hv_kinds):
                    raise BizError("Một loại ca không thể vừa vào cột J vừa vào cột E–H (sẽ cộng đôi).")
                vf = parse_date(f.get("valid_from"))
                nv = core.save_timesheet_rules(c, owner, r, vf, f.get("note") or None, g.uid)
                msg = (f"Đã tạo phiên bản quy tắc {nv}" + (f", áp dụng từ {vf:%d/%m/%Y}" if vf else ", áp dụng cho mọi tháng chưa chốt")
                       + ". Tháng đã chốt không thay đổi.")
            elif action == "xac-nhan-quy-tac":
                core.confirm_rule_version(c, owner, f.get("version_id"), f.get("confirmed") == "1")
            elif action == "huy-ca":
                core.save_cancel_rules(c, owner, {k: {"pay": f.get(f"pay_{k}") == "1", "confirmed": bool(f.get(f"cf_{k}"))}
                                                  for k in schedule.CANCEL_REASONS})
            elif action == "con-khong":
                core.save_flag_meaning(c, owner, f.get("meaning"), _flag(f.get("confirmed")))
            # ---- excel
            elif action == "xuat-excel":
                admin.save_export_options(c, owner, {k: f.get(k) for k in ("sheet_name", "file_prefix", "include_money_row",
                                                                            "include_supplement", "include_detail", "include_info")})
            elif action == "anh-xa":
                mapping = {"sheet": f.get("sheet"), "start_row": f.get("start_row"),
                           "columns": {k: f.get(f"col_{k}") for k in FIELDS}}
                admin.save_mapping(c, owner, f.get("name"), f.get("center_id") or None, mapping, _flag(f.get("is_default")),
                                   f.get("id") or None)
                msg = "Đã lưu hồ sơ ánh xạ — lần đối soát sau của trung tâm này sẽ tự dùng."
            elif action == "luu-tru-anh-xa":
                admin.archive_mapping(c, f.get("id"), f.get("archived") == "1")
        flash(msg, "ok")
        return redirect(url_for("settings_section", sec=sec) + anchor)

    # ---------------- import lịch sử ----------------
    @app.route("/cai-dat/import", methods=["GET", "POST"])
    @login_required
    @handle_errors("settings_page")
    def legacy_import_page():
        with db("import lịch sử") as c:
            owner = owner_ctx(c)
            if request.method == "POST" and request.form.get("import_id"):
                res = reports.legacy_apply(c, owner, g.uid, request.form["import_id"], keep=set(request.form.getlist("keep")))
                flash(f"Đã import {res['inserted']} ca; bỏ qua {res['skipped']} (trùng/đã có/không chọn).", "ok")
                return redirect(url_for("settings_section", sec="excel") + "#import")
            if request.method == "POST":
                f = request.files.get("file")
                if not f or not f.filename.lower().endswith(".xlsx"):
                    raise BizError("Chọn file .xlsx.")
                data = f.read()
                if len(data) > 10 * 1024 * 1024:
                    raise BizError("File Excel tối đa 10 MB.")
                iid, plan, stats, existing = reports.legacy_preview(c, owner, f.filename, data)
                return render_template("legacy_preview.html", iid=iid, plan=plan, stats=stats, existing=existing)
        return redirect(url_for("settings_section", sec="excel") + "#import")

    # ---------------- sao lưu / khôi phục ----------------
    @app.post("/cai-dat/sao-luu/tai")
    @login_required
    @handle_errors("settings_page")
    def backup_download():
        with db("tải bản sao lưu") as c:
            owner = owner_ctx(c)
            if owner != g.uid:
                raise BizError("Chỉ chủ dữ liệu mới tải được bản sao lưu đầy đủ.")
            data, name, _ = backup.export_backup(c, owner, g.uid)
        return send_file(io.BytesIO(data), mimetype="application/zip", as_attachment=True, download_name=name)

    @app.post("/cai-dat/sao-luu/kiem-tra")
    @login_required
    @handle_errors("settings_page")
    def backup_verify():
        f = request.files.get("file")
        if not f:
            raise BizError("Chọn file sao lưu (.zip).")
        blob = f.read()
        with db("kiểm tra bản sao lưu") as c:
            owner = owner_ctx(c)
            rep = backup.verify_backup(c, owner, g.uid, blob, f.filename)
        return render_template("backup_verify.html", rep=rep, filename=f.filename, title="Kiểm tra bản sao lưu")

    @app.post("/cai-dat/sao-luu/khoi-phuc")
    @login_required
    @handle_errors("settings_page")
    def backup_restore():
        f = request.files.get("file")
        if not f:
            raise BizError("Chọn file sao lưu (.zip).")
        if (request.form.get("confirm_text") or "").strip().upper() != "KHOI PHUC":
            raise BizError("Gõ đúng KHOI PHUC để xác nhận khôi phục.")
        blob = f.read()
        with db("khôi phục từ bản sao lưu") as c:
            owner = owner_ctx(c)
            if owner != g.uid:
                raise BizError("Chỉ khôi phục vào chính tài khoản của bạn.")
            st = backup.restore_backup(c, owner, g.uid, blob, f.filename)
        n = sum(st["restored"].values())
        flash(f"Đã khôi phục {n} dòng dữ liệu và {st['audit']} mục nhật ký. Đã đếm lại từng bảng: khớp với bản sao lưu.", "ok")
        return redirect(url_for("settings_section", sec="sao-luu"))

    # ---------------- kỳ chấm công ----------------
    @app.get("/cham-cong/ky")
    @login_required
    def timesheet_periods():
        y, m = ym_from_args(today())
        with db() as c:
            owner = owner_ctx(c)
            pers = admin.periods(c, owner)
            vers = admin.period_versions(c, owner, y, m) if request.args.get("thang") else []
        return render_template("periods.html", pers=pers, vers=vers, y=y, m=m, sel=bool(request.args.get("thang")),
                               today=today())

    @app.get("/cham-cong/ky/phien-ban/<uuid:vid>")
    @login_required
    def timesheet_period_file(vid):
        with db() as c:
            owner_ctx(c)
            r = admin.period_version_file(c, vid)
        if not r:
            abort(404)
        return send_file(io.BytesIO(bytes(r["export_bytes"])),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", as_attachment=True,
                         download_name=f"ChamCong_{r['year']}-{r['month']:02d}_ban-chot-v{r['version']}_{r['export_sha256'][:8]}.xlsx")
