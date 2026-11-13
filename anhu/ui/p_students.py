"""Học viên: danh sách + lọc, hồ sơ (sửa/lưu trữ/liên hệ/lớp/lịch/lịch sử), thêm nhanh (mã tự sinh),
nhập/xuất Excel, gắn tên cũ vào hồ sơ."""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from ..db import rows
from ..services import admin, core, students_io
from .common import act, can_write, help_box, hm, kind_label, owner, read, seg, status_label, today

ST_LABEL = {"active": "Đang học", "paused": "Tạm nghỉ", "stopped": "Ngừng học"}
WEEK = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _add_form():
    with st.form("hv_add", clear_on_submit=True):
        name = st.text_input("Tên học viên *")
        c1, c2 = st.columns(2)
        code = c1.text_input("Mã (để trống = tự sinh)")
        subject = c2.text_input("Môn (không bắt buộc)")
        locs = read(lambda c: core.locations(c, owner()), [])
        loc_ids = [""] + [str(l["id"]) for l in locs]
        lab = {str(l["id"]): l["name"] for l in locs}
        li = st.selectbox("Học tại (không bắt buộc)", loc_ids, format_func=lambda i: lab.get(i, "—"))
        go = st.form_submit_button("Thêm học viên", type="primary")
    if go:
        def _do(c):
            sid = students_io.add_with_code(c, owner(), name, code=code, subject=subject or None)
            if li:
                core.enroll(c, owner(), sid, li)
            return sid
        sid = act(_do, key="hv_add", payload=(name, code, li), reason="thêm học viên", rerun=False)
        if sid:
            st.session_state["hv_sel"] = sid
            st.session_state["hv_flash_new"] = name
            st.rerun()


def _list():
    with st.expander("🔎 Tìm & lọc", expanded=False):
        q = st.text_input("Tên (không cần dấu)", key="hv_q")
        c1, c2, c3 = st.columns(3)
        stt = c1.selectbox("Trạng thái", ["", "active", "paused", "stopped", "archived", "all"], key="hv_st",
                           format_func=lambda s: {"": "Tất cả (trừ lưu trữ)", "archived": "Đã lưu trữ", "all": "Tất cả, kể cả lưu trữ"}
                           .get(s, ST_LABEL.get(s, s)))
        locs = read(lambda c: core.locations(c, owner()), [])
        lmap = {"": "Tất cả"} | {str(l["id"]): l["name"] for l in locs}
        li = c2.selectbox("Cơ sở", list(lmap), format_func=lmap.get, key="hv_loc")
        tt = c3.selectbox("Loại hình", ["", "center", "private"], key="hv_tt",
                          format_func=lambda x: {"": "Tất cả", "center": "Trung tâm", "private": "Dạy riêng"}[x])
        groups = read(lambda c: admin.groups(c, owner()), [])
        gmap = {"": "Tất cả"} | {str(g["id"]): g["name"] for g in groups}
        gi = st.selectbox("Lớp / nhóm", list(gmap), format_func=gmap.get, key="hv_grp")
    lst = read(lambda c: core.search_students(c, owner(), q or "", stt or None, li or None, None, tt or None), [])
    if gi:
        ids = {str(r["student_id"]) for r in read(lambda c: rows(c, "SELECT student_id FROM class_group_members WHERE group_id = :g "
                                                                    "AND left_on IS NULL", g=gi), [])}
        lst = [s for s in lst if str(s["id"]) in ids]
    if not lst:
        st.info("Chưa có học viên phù hợp." if (q or stt or li or tt or gi) else "Chưa có học viên. Bấm “Thêm học viên” để bắt đầu.")
        return
    df = pd.DataFrame([{"Mã": s.get("code") or "", "Tên": s["display_name"], "Nơi học": s["places"] or "",
                        "Trạng thái": ST_LABEL.get(s["status"], s["status"]) + (" · lưu trữ" if s.get("archived_at") else ""),
                        "id": str(s["id"])} for s in lst])
    st.caption(f"{len(df)} học viên — chạm vào một dòng để mở hồ sơ.")
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      column_config={"id": None}, key="hv_table")
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        st.session_state["hv_sel"] = df.iloc[sel[0]]["id"]


def _detail(sid):
    s = read(lambda c: core.student_stats(c, sid))
    if not s:
        st.session_state.pop("hv_sel", None)
        return
    stu = s["student"]
    w = can_write()
    with st.container(border=True):
        h1, h2 = st.columns([4, 1])
        h1.subheader(f"{stu['display_name']}" + (f" · {stu['code']}" if stu.get("code") else ""))
        if h2.button("Đóng", key="hv_close", width="stretch"):
            st.session_state.pop("hv_sel", None)
            st.rerun()
        if stu.get("archived_at"):
            st.caption(f"Đã lưu trữ từ {stu['archived_at']:%d/%m/%Y}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Buổi đã học", s["total_sessions"])
        m2.metric("Tổng giờ", f"{s['total_minutes'] / 60:.1f}")
        m3.metric("Dạy riêng còn thu", f"{int(s['unpaid']):,}".replace(",", ".") + " ₫" if s["unpaid"] else "0")
        t1, t2, t3, t4 = st.tabs(["Hồ sơ", "Liên hệ", "Lớp & lịch", "Lịch sử học"])
        with t1:
            if w:
                with st.form(f"hv_edit_{sid}"):
                    c1, c2 = st.columns(2)
                    name = c1.text_input("Tên", stu["display_name"])
                    code = c2.text_input("Mã", stu.get("code") or "")
                    c3, c4 = st.columns(2)
                    cg = c3.text_input("Lớp / nhóm (ghi chú)", stu.get("class_group") or "")
                    sub = c4.text_input("Môn", stu.get("subject") or "")
                    c5, c6 = st.columns(2)
                    sd = c5.date_input("Ngày bắt đầu", stu.get("start_date"), format="DD/MM/YYYY")
                    stt = c6.selectbox("Trạng thái", list(ST_LABEL), index=list(ST_LABEL).index(stu["status"]), format_func=ST_LABEL.get)
                    notes = st.text_area("Ghi chú", stu.get("notes") or "", height=70)
                    go = st.form_submit_button("Lưu hồ sơ", type="primary")
                if go:
                    act(lambda c: core.update_student(c, sid, display_name=name, code=code, class_group=cg, subject=sub, start_date=sd,
                                                      status=stt, notes=notes), key=f"hv_edit_{sid}",
                        payload=(name, code, cg, sub, sd, stt, notes), reason="sửa hồ sơ học viên",
                        ok="Đã lưu. Tên mới áp dụng cho ca mới; kỳ đã chốt giữ nguyên.")
                arch = bool(stu.get("archived_at"))
                if st.button("Khôi phục hồ sơ" if arch else "Lưu trữ (học viên đã nghỉ hẳn)", key=f"hv_arch_{sid}"):
                    act(lambda c: core.archive_student(c, sid, not arch), key=f"hv_arch_{sid}", payload=arch,
                        reason="lưu trữ học viên" if not arch else "khôi phục học viên",
                        ok="Đã lưu trữ — hồ sơ ẩn khỏi danh sách chọn, lịch sử giữ nguyên." if not arch else "Đã khôi phục.")
            st.markdown("**Nơi học:** " + (", ".join(f"{e['location_name']} ({'trung tâm' if e['teaching_type'] == 'center' else 'dạy riêng'})"
                                                     for e in s["enrollments"]) or "chưa gắn"))
            if w:
                locs = read(lambda c: core.locations(c, owner()), [])
                lab = {str(l["id"]): l["name"] for l in locs}
                c1, c2 = st.columns([3, 1])
                li = c1.selectbox("Thêm nơi học", list(lab), format_func=lab.get, key=f"hv_enr_{sid}", index=None,
                                  placeholder="Chọn cơ sở")
                if c2.button("Thêm", key=f"hv_enr_go_{sid}", disabled=not li):
                    act(lambda c: core.enroll(c, owner(), sid, li), key=f"hv_enr_{sid}", payload=li, reason="thêm nơi học",
                        ok="Đã thêm nơi học.")
        with t2:
            cts = read(lambda c: admin.contacts(c, sid), [])
            for ct in cts:
                st.markdown(f"**{ct['name']}**" + (f" · {ct['relation']}" if ct.get("relation") else "")
                            + (" · ⭐ chính" if ct["is_primary"] else "") + f"  \n{ct.get('phone') or ''} {ct.get('email') or ''}")
                if w and st.button("Ẩn liên hệ này", key=f"ct_x_{ct['id']}", type="tertiary"):
                    act(lambda c, i=ct["id"]: admin.archive_contact(c, i, True), key=f"ct_x_{ct['id']}", payload=1,
                        reason="ẩn liên hệ", ok="Đã ẩn.")
            if not cts:
                st.caption("Chưa có người liên hệ (không bắt buộc).")
            if w:
                with st.form(f"ct_add_{sid}", clear_on_submit=True):
                    c1, c2 = st.columns(2)
                    n = c1.text_input("Tên người liên hệ")
                    r = c2.text_input("Quan hệ (mẹ, bố…)")
                    c3, c4 = st.columns(2)
                    ph = c3.text_input("Điện thoại")
                    em = c4.text_input("Email")
                    pr = st.checkbox("Liên hệ chính")
                    go = st.form_submit_button("Thêm liên hệ")
                if go:
                    act(lambda c: admin.save_contact(c, owner(), sid, n, r, ph, em, None, pr), key=f"ct_add_{sid}",
                        payload=(n, ph, em), reason="thêm liên hệ", ok="Đã lưu người liên hệ.")
        with t3:
            gs = read(lambda c: admin.student_groups(c, sid), [])
            st.markdown("**Lớp / nhóm:** " + (", ".join(g["name"] + ("" if not g["left_on"] else f" (rời {g['left_on']:%d/%m/%Y})")
                                                        for g in gs) or "chưa có"))
            if w:
                allg = read(lambda c: admin.groups(c, owner()), [])
                gl = {str(g["id"]): g["name"] for g in allg}
                c1, c2 = st.columns([3, 1])
                gi = c1.selectbox("Thêm vào lớp/nhóm", list(gl), format_func=gl.get, key=f"hv_g_{sid}", index=None, placeholder="Chọn")
                if c2.button("Thêm", key=f"hv_g_go_{sid}", disabled=not gi):
                    act(lambda c: admin.add_member(c, owner(), gi, sid, today()), key=f"hv_g_{sid}", payload=gi,
                        reason="thêm vào lớp", ok="Đã thêm vào lớp/nhóm.")
            tpls = read(lambda c: rows(c, """SELECT t.*, l.name AS location_name FROM schedule_templates t
                                             LEFT JOIN locations l ON l.id = t.location_id WHERE t.owner_id = :o
                                             AND t.expected_students @> CAST(:j AS jsonb) AND (t.valid_to IS NULL OR t.valid_to >= :d)
                                             ORDER BY t.weekday, t.start_time""",
                                           o=owner(), j=json.dumps([{"student_id": str(sid)}]), d=today()), [])
            st.markdown("**Lịch học cố định:**")
            if tpls:
                for t in tpls:
                    st.write(f"• {WEEK[t['weekday']]} {hm(t['start_time'])}–{hm(t['end_time'])} · {t['location_name']}")
            else:
                st.caption("Chưa có. Đổi giờ học: Lịch dạy → Lịch cố định → chọn lịch → Đổi cả chuỗi.")
        with t4:
            if s["history"]:
                df = pd.DataFrame([{"Ngày": h["session_date"], "Giờ": f"{hm(h['start_time'])}–{hm(h['end_time'])}",
                                    "Cơ sở": h["location_label"], "Loại ca": kind_label(h["session_kind"]),
                                    "Trạng thái": status_label(h["status"])} for h in s["history"]])
                st.dataframe(df, hide_index=True, width="stretch",
                             column_config={"Ngày": st.column_config.DateColumn(format="DD/MM/YYYY")})
            else:
                st.caption("Chưa có buổi học nào gắn với hồ sơ này. Buổi cũ chỉ có tên: dùng “Gắn tên cũ”.")


def _io():
    st.markdown("**Xuất danh sách học viên**")
    if st.button("Chuẩn bị file Excel", key="hv_exp"):
        st.session_state["hv_xlsx"] = read(lambda c: students_io.export_xlsx(c, owner()))
    if st.session_state.get("hv_xlsx"):
        st.download_button("⬇ Tải hoc-vien.xlsx", st.session_state["hv_xlsx"], f"hoc-vien-{today():%Y%m%d}.xlsx", XLSX)
    st.divider()
    st.markdown("**Nhập học viên từ Excel**")
    st.caption("Hàng 1 là tiêu đề. Cột bắt buộc: “Tên học viên”. Mã để trống sẽ tự sinh. Học viên trùng mã hoặc trùng tên sẽ được báo trước.")
    st.download_button("Tải file mẫu", students_io.template_xlsx(), "mau-hoc-vien.xlsx", XLSX, key="hv_tpl")
    if not can_write():
        return
    f = st.file_uploader("Chọn file .xlsx (tối đa 10 MB)", type=["xlsx"], key="hv_up")
    if f:
        data = f.getvalue()
        if len(data) > 10 * 1024 * 1024:
            st.error("File tối đa 10 MB.")
            return
        items = read(lambda c: students_io.preview_import(c, owner(), data), [])
        if not items:
            st.warning("Không đọc được dòng nào.")
            return
        lab = {"new": "✅ thêm mới", "dup_code": "⏭ trùng mã", "dup_name": "⚠ trùng tên", "error": "❌ lỗi"}
        st.dataframe(pd.DataFrame([{"Hàng": i["row"], "Tên": i["name"], "Mã": i.get("code") or "(tự sinh)", "Kết quả": lab[i["status"]],
                                    "Ghi chú": i["problem"]} for i in items]), hide_index=True, width="stretch")
        inc = st.checkbox("Nhập cả học viên trùng tên (là người khác cùng tên)", key="hv_dupname")
        n = sum(1 for i in items if i["status"] == "new" or (inc and i["status"] == "dup_name"))
        if st.button(f"Nhập {n} học viên", type="primary", disabled=not n, key="hv_imp"):
            act(lambda c: students_io.apply_import(c, owner(), items, inc), key="hv_imp", payload=(f.name, len(data), inc),
                reason="nhập học viên từ Excel", ok=f"Đã nhập {n} học viên.")


def _link_names():
    st.caption("Các buổi nhập từ file Excel cũ chỉ có **tên**. Gắn tên với hồ sơ để xem lịch sử theo học viên. "
               "Buổi thuộc kỳ đã chốt được giữ nguyên.")
    names = read(lambda c: students_io.unlinked_names(c, owner()), [])
    if not names:
        st.success("Mọi tên trong buổi dạy đều đã gắn hồ sơ.")
        return
    studs = read(lambda c: core.search_students(c, owner()), [])
    sm = {str(s["id"]): s["display_name"] + (f" · {s['code']}" if s.get("code") else "") for s in studs}
    st.write(f"**{len(names)} tên** chưa gắn hồ sơ.")
    for i, n in enumerate(names[:60]):
        with st.container(border=True):
            st.markdown(f"**{n['name']}** — {n['n']} lần ({n['first_day']:%d/%m/%Y} → {n['last_day']:%d/%m/%Y})")
            if not can_write():
                continue
            opts = ["__new__"] + list(sm)
            default = opts.index(n["suggest"]) if n.get("suggest") in opts else 0
            c1, c2 = st.columns([3, 1])
            pick = c1.selectbox("Gắn với", opts, index=default, key=f"ln_{i}",
                                format_func=lambda x: "➕ Tạo hồ sơ mới với tên này" if x == "__new__" else sm[x],
                                label_visibility="collapsed")
            if c2.button("Gắn", key=f"ln_go_{i}", width="stretch"):
                r = act(lambda c: students_io.link_name(c, owner(), n["name"], None if pick == "__new__" else pick,
                                                        create_new=pick == "__new__"),
                        key=f"ln_{n['name']}", payload=pick, reason="gắn tên cũ vào hồ sơ", rerun=False)
                if r:
                    from .common import flash
                    flash(f"Đã gắn {r['linked']} lần xuất hiện của “{n['name']}”."
                          + (f" {r['locked_left']} lần thuộc kỳ đã chốt được giữ nguyên." if r["locked_left"] else ""))
                    st.rerun()


def render():
    st.title("Học viên")
    help_box("""
- Chạm vào một học viên để mở hồ sơ: sửa thông tin, người liên hệ, lớp, lịch học, lịch sử.
- **Mã học viên** tự sinh (HV0001…) nếu để trống. Hai bé trùng tên vẫn là hai hồ sơ khác nhau.
- Học viên nghỉ hẳn: mở hồ sơ → **Lưu trữ** (không xóa; lịch sử giữ nguyên).
- Đổi giờ học hằng tuần của học viên: Lịch dạy → Lịch cố định.""")
    view = seg(["Danh sách", "Gắn tên cũ", "Nhập / xuất Excel"], "hv_view", "Danh sách")
    if view == "Danh sách":
        if st.session_state.get("hv_flash_new"):
            st.success(f"Đã thêm học viên {st.session_state.pop('hv_flash_new')}.")
        if can_write():
            with st.popover("➕ Thêm học viên"):
                _add_form()
        if st.session_state.get("hv_sel"):
            _detail(st.session_state["hv_sel"])
        _list()
    elif view == "Gắn tên cũ":
        _link_names()
    else:
        _io()
