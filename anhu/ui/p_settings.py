"""Cài đặt & quản trị (ít dùng): tài khoản, danh mục, lớp/môn, loại ca, đơn giá, quy tắc, Excel/ánh xạ, nhật ký, sao lưu, hệ thống."""
from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from ..db import scalar
from ..domain.pay import CATEGORY_LABEL, EXCEL_SUGGESTED
from ..domain.reconcile import FIELDS
from ..domain.timesheet import TimesheetRules
from ..services import BizError, accounts, admin, backup, core, reports
from .common import act, can_write, cfg, db, flash, help_box, kind_label, money, owner, owner_info, read, today, uid, user

SECTIONS = ["Tài khoản", "Trung tâm & cơ sở", "Môn, lớp & nhóm", "Loại ca & trạng thái", "Đơn giá", "Quy tắc chấm công",
            "Mẫu Excel & dữ liệu cũ", "Nhật ký", "Sao lưu & khôi phục", "Hệ thống"]


# ======================= tài khoản =======================
def sec_account():
    u = user()
    me = read(lambda c: admin.me(c, uid()))
    with st.expander("Hồ sơ & mật khẩu của tôi", expanded=False):
        with st.form("me_prof"):
            name = st.text_input("Tên hiển thị", me["display_name"])
            sub = st.text_input("Môn / chuyên môn", me.get("subject") or "")
            go = st.form_submit_button("Lưu hồ sơ")
        if go:
            act(lambda c: admin.update_profile(c, uid(), name, sub), key="me_prof", payload=(name, sub), reason="sửa hồ sơ",
                ok="Đã lưu hồ sơ.")
        with st.form("me_pw", clear_on_submit=True):
            old = st.text_input("Mật khẩu hiện tại", type="password")
            p1 = st.text_input("Mật khẩu mới (≥ 10 ký tự)", type="password")
            p2 = st.text_input("Nhập lại", type="password")
            go = st.form_submit_button("Đổi mật khẩu")
        if go:
            if p1 != p2:
                st.error("Mật khẩu nhập lại không khớp.")
            else:
                act(lambda c: core.change_password(c, uid(), old, p1), key="me_pw", payload=len(p1), reason="đổi mật khẩu",
                    ok="Đã đổi mật khẩu.")
    with st.expander("Thiết bị đang đăng nhập"):
        ss = read(lambda c: accounts.my_sessions(c), [])
        for s in ss:
            cur = str(s["id"]) == u.get("session")
            st.write(f"• {s['client'] or '—'} · đăng nhập {s['created_at']:%d/%m %H:%M}" + (" · **thiết bị này**" if cur else ""))
        if len(ss) > 1 and st.button("Đăng xuất mọi thiết bị khác"):
            act(lambda c: accounts.revoke_other_sessions(c, u["session"]), key="rev_others", payload=1, reason="đăng xuất thiết bị khác",
                    ok="Đã đăng xuất các thiết bị khác.")
    with st.expander("Chia sẻ dữ liệu của tôi với giáo viên kia"):
        st.caption("Mặc định hai tài khoản KHÔNG xem được dữ liệu của nhau (bảo vệ ở tầng cơ sở dữ liệu). Chỉ chia sẻ khi cần.")
        for s in read(lambda c: core.my_shares(c, uid()), []):
            c1, c2 = st.columns([3, 1])
            c1.write(f"{s['display_name']} ({s['email']}) — {'xem và sửa' if s['can_write'] else 'chỉ xem'}")
            if c2.button("Ngừng", key=f"unsh_{s['grantee_id']}"):
                act(lambda c, g=s["grantee_id"]: core.unshare(c, uid(), g), key=f"unsh_{s['grantee_id']}", payload=1,
                    reason="ngừng chia sẻ", ok="Đã ngừng chia sẻ.")
        with st.form("share", clear_on_submit=True):
            em = st.text_input("Email giáo viên kia")
            w = st.checkbox("Cho phép sửa")
            go = st.form_submit_button("Chia sẻ")
        if go:
            act(lambda c: core.share_with(c, uid(), em, w, lambda e: scalar(c, "SELECT app.user_id_by_email(:e)", e=e)),
                key="share", payload=(em, w), reason="chia sẻ dữ liệu", ok="Đã chia sẻ.")
    if not u["is_admin"]:
        st.caption("Quản lý tài khoản chỉ dành cho quản trị viên.")
        return
    st.subheader("Quản lý tài khoản")
    users = read(lambda c: admin.list_users(c), [])
    sc = read(lambda c: accounts.session_counts(c), {})
    st.dataframe(pd.DataFrame([{"Tên": x["display_name"], "Email": x["email"], "Vai trò": "Quản trị" if x["is_admin"] else "Giáo viên",
                                "Trạng thái": "Hoạt động" if x["is_active"] else "Đã khóa",
                                "Phiên đang mở": (sc.get(str(x["id"])) or {}).get("active", 0),
                                "Đăng nhập gần nhất": x["last_login_at"]} for x in users]), hide_index=True, width="stretch",
                 column_config={"Đăng nhập gần nhất": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
    for x in users:
        xid = str(x["id"])
        if xid == uid():
            continue
        with st.expander(f"⚙ {x['display_name']} ({x['email']})"):
            c1, c2, c3 = st.columns(3)
            if c1.button("Khóa tài khoản" if x["is_active"] else "Mở khóa", key=f"lk_{xid}"):
                act(lambda c: admin.set_account_active(c, xid, not x["is_active"]), key=f"lk_{xid}", payload=x["is_active"],
                    reason="khóa tài khoản" if x["is_active"] else "mở khóa tài khoản",
                    ok="Đã khóa tài khoản (dữ liệu giữ nguyên, mọi phiên bị đăng xuất)." if x["is_active"] else "Đã mở khóa.")
            if c2.button("Thu hồi phiên đăng nhập", key=f"rv_{xid}"):
                act(lambda c: accounts.revoke_sessions(c, xid), key=f"rv_{xid}", payload=1, reason="thu hồi phiên",
                    ok="Đã đăng xuất người này khỏi mọi thiết bị.")
            if c3.button("Bỏ quyền quản trị" if x["is_admin"] else "Cấp quyền quản trị", key=f"rl_{xid}"):
                act(lambda c: accounts.set_role(c, xid, not x["is_admin"]), key=f"rl_{xid}", payload=x["is_admin"], reason="đổi vai trò",
                    ok="Đã đổi vai trò.")
            if st.button("Tạo mã đặt lại quyền truy cập (24 giờ)", key=f"rs_{xid}"):
                code = act(lambda c: accounts.create_reset(c, xid, 24), key=f"rs_{xid}", payload=1, reason="tạo mã đặt lại", rerun=False)
                if code:
                    st.session_state["code_once"] = ("reset", x["email"], code)
                    st.rerun()
    once = st.session_state.pop("code_once", None)
    if once:
        kind, em, code = once
        link = (cfg("ANHU_APP_URL", "") or "").rstrip("/")
        st.success(f"**Mã {'kích hoạt' if kind == 'invite' else 'đặt lại'} cho {em}:**")
        st.code(code, language=None)
        if link:
            st.code(f"{link}/?ma={code}&email={em}", language=None)
        st.caption("Mã chỉ hiện **một lần** và chỉ dùng được một lần. Gửi cho người dùng qua kênh riêng (Zalo/tin nhắn). "
                   "Người dùng mở website → “Tôi có mã kích hoạt” → nhập email, mã và tự đặt mật khẩu. Quản trị không biết mật khẩu đó.")
    active_n = sum(1 for x in users if x["is_active"])
    with st.expander("➕ Thêm tài khoản giáo viên", expanded=active_n < 2):
        if active_n >= 2:
            st.info("Đã đủ 2 tài khoản hoạt động. Muốn thêm, khóa bớt một tài khoản trước.")
        else:
            with st.form("invite", clear_on_submit=True):
                em = st.text_input("Email giáo viên")
                nm = st.text_input("Tên giáo viên")
                ad = st.checkbox("Cấp quyền quản trị")
                hrs = st.select_slider("Mã có hạn", [24, 48, 72, 168], value=72, format_func=lambda h: f"{h} giờ")
                go = st.form_submit_button("Tạo lời mời", type="primary")
            if go:
                code = act(lambda c: accounts.create_invite(c, em, nm, ad, hrs), key="invite", payload=(em, nm, ad), reason="tạo lời mời",
                           rerun=False)
                if code:
                    st.session_state["code_once"] = ("invite", em.strip().lower(), code)
                    st.rerun()
    with st.expander("Lời mời & mã đã tạo"):
        inv = read(lambda c: accounts.list_invites(c), [])
        now = pd.Timestamp.now(tz="UTC")
        for i in inv:
            state = ("đã dùng" if i["used_at"] else "đã thu hồi" if i["revoked_at"] else "hết hạn"
                     if pd.Timestamp(i["expires_at"]) < now else "đang chờ")
            c1, c2 = st.columns([4, 1])
            c1.write(f"{'Mời' if i['kind'] == 'invite' else 'Đặt lại'} · {i['email']} · tạo {i['created_at']:%d/%m %H:%M} · **{state}**")
            if state == "đang chờ" and c2.button("Thu hồi", key=f"ri_{i['id']}"):
                act(lambda c, x=i["id"]: accounts.revoke_invite(c, x), key=f"ri_{i['id']}", payload=1, reason="thu hồi lời mời",
                    ok="Đã thu hồi mã.")
        if not inv:
            st.caption("Chưa có.")
    with st.expander("Nhật ký quản trị"):
        log = read(lambda c: accounts.admin_audit(c, 100), [])
        if log:
            st.dataframe(pd.DataFrame([{"Lúc": r["at"], "Người làm": r["actor"], "Việc": r["reason"], "Chi tiết": str(r["after"] or "")}
                                       for r in log]), hide_index=True, width="stretch",
                         column_config={"Lúc": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})


# ======================= danh mục =======================
def sec_catalog():
    w = can_write()
    centers = read(lambda c: core.centers(c, owner(), True), [])
    locs = read(lambda c: core.locations(c, owner(), True), [])
    st.caption("Tên cơ sở là giá trị cột **CƠ SỞ** trong file chấm công. Đổi tên chỉ áp dụng cho ca mới; kỳ đã chốt giữ nguyên. "
               "Không dạy nữa → **Lưu trữ** (không xóa).")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Trung tâm**")
        for ce in centers:
            cid = str(ce["id"])
            with st.expander(ce["name"] + (" · đã lưu trữ" if ce["archived_at"] else "")):
                if not w:
                    continue
                n = st.text_input("Tên", ce["name"], key=f"ce_n_{cid}")
                ct = st.text_input("Liên hệ", ce.get("contact") or "", key=f"ce_c_{cid}")
                a, b = st.columns(2)
                if a.button("Lưu", key=f"ce_s_{cid}"):
                    act(lambda c: admin.update_center(c, cid, n, ct, ce.get("notes")), key=f"ce_s_{cid}", payload=(n, ct),
                        reason="sửa trung tâm", ok="Đã lưu.")
                if b.button("Khôi phục" if ce["archived_at"] else "Lưu trữ", key=f"ce_a_{cid}"):
                    act(lambda c: admin.archive_center(c, cid, not ce["archived_at"]), key=f"ce_a_{cid}", payload=1,
                        reason="lưu trữ trung tâm", ok="Đã cập nhật.")
        if w:
            with st.form("ce_add", clear_on_submit=True):
                n = st.text_input("Trung tâm mới")
                go = st.form_submit_button("Thêm trung tâm")
            if go:
                act(lambda c: core.add_center(c, owner(), n), key="ce_add", payload=n, reason="thêm trung tâm", ok="Đã thêm trung tâm.")
    with c2:
        st.markdown("**Cơ sở / địa điểm dạy**")
        act_centers = [x for x in centers if not x["archived_at"]]
        cmap = {"": "—"} | {str(x["id"]): x["name"] for x in act_centers}
        kinds = {"center_branch": "Cơ sở trung tâm", "private_place": "Địa điểm dạy riêng"}
        for l in locs:
            lid = str(l["id"])
            with st.expander(l["name"] + f" · {l['center_name'] or 'dạy riêng'}" + (" · đã lưu trữ" if l["archived_at"] else "")):
                if not w:
                    continue
                n = st.text_input("Tên (cột CƠ SỞ)", l["name"], key=f"lo_n_{lid}")
                k = st.selectbox("Loại", list(kinds), index=list(kinds).index(l["kind"]), format_func=kinds.get, key=f"lo_k_{lid}")
                ci = st.selectbox("Thuộc trung tâm", list(cmap), index=list(cmap).index(str(l["center_id"])) if str(l["center_id"]) in cmap else 0,
                                  format_func=cmap.get, key=f"lo_c_{lid}")
                ad = st.text_input("Địa chỉ", l.get("address") or "", key=f"lo_a_{lid}")
                a, b = st.columns(2)
                if a.button("Lưu", key=f"lo_s_{lid}"):
                    act(lambda c: admin.update_location(c, lid, n, k, ci or None, ad), key=f"lo_s_{lid}", payload=(n, k, ci, ad),
                        reason="sửa cơ sở", ok="Đã lưu. Tên mới áp dụng cho ca mới.")
                if b.button("Khôi phục" if l["archived_at"] else "Lưu trữ", key=f"lo_ar_{lid}"):
                    act(lambda c: admin.archive_location(c, lid, not l["archived_at"]), key=f"lo_ar_{lid}", payload=1,
                        reason="lưu trữ cơ sở", ok="Đã cập nhật.")
        if w:
            with st.form("lo_add", clear_on_submit=True):
                n = st.text_input("Tên cơ sở / địa điểm mới")
                k = st.selectbox("Loại", list(kinds), format_func=kinds.get)
                ci = st.selectbox("Thuộc trung tâm (với cơ sở trung tâm)", list(cmap), format_func=cmap.get)
                go = st.form_submit_button("Thêm")
            if go:
                act(lambda c: core.add_location(c, owner(), n, k, ci or None), key="lo_add", payload=(n, k, ci), reason="thêm cơ sở",
                    ok="Đã thêm.")


def sec_groups():
    w = can_write()
    subs = read(lambda c: admin.subjects(c, owner(), True), [])
    groups = read(lambda c: admin.groups(c, owner(), True), [])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Môn học**")
        st.write(", ".join(s["name"] + (" (lưu trữ)" if s["archived_at"] else "") for s in subs) or "—")
        if w:
            with st.form("sub_add", clear_on_submit=True):
                n = st.text_input("Môn mới")
                go = st.form_submit_button("Thêm môn")
            if go:
                act(lambda c: admin.save_subject(c, owner(), n), key="sub_add", payload=n, reason="thêm môn", ok="Đã thêm môn.")
    with c2:
        st.markdown("**Lớp / nhóm học viên**")
        if w:
            with st.form("grp_add", clear_on_submit=True):
                n = st.text_input("Tên lớp / nhóm")
                k = st.radio("Loại", ["class", "group"], format_func={"class": "Lớp học", "group": "Nhóm học viên"}.get, horizontal=True)
                go = st.form_submit_button("Thêm")
            if go:
                act(lambda c: admin.save_group(c, owner(), n, k), key="grp_add", payload=(n, k), reason="thêm lớp", ok="Đã thêm.")
    studs = read(lambda c: core.search_students(c, owner(), status="active"), [])
    smap = {str(s["id"]): s["display_name"] for s in studs}
    for g in groups:
        gid = str(g["id"])
        with st.expander(f"{g['name']} · {g['members']} HV" + (" · lưu trữ" if g["archived_at"] else "")):
            gd = read(lambda c: admin.group(c, gid))
            for m in gd["member_list"]:
                a, b = st.columns([4, 1])
                a.write(f"{m['display_name']} · từ {m['joined_on']:%d/%m/%Y}" + (f" → {m['left_on']:%d/%m/%Y}" if m["left_on"] else ""))
                if w and not m["left_on"] and b.button("Rời lớp", key=f"lv_{m['id']}"):
                    act(lambda c, i=m["id"]: admin.leave_member(c, i, today()), key=f"lv_{m['id']}", payload=1, reason="rời lớp",
                        ok="Đã ghi nhận rời lớp.")
            if w:
                add = st.multiselect("Thêm học viên", list(smap), format_func=smap.get, key=f"gm_{gid}")
                a, b = st.columns(2)
                if a.button("Thêm vào lớp", key=f"gm_go_{gid}", disabled=not add):
                    act(lambda c: [admin.add_member(c, owner(), gid, s, today()) for s in add], key=f"gm_{gid}", payload=tuple(add),
                        reason="thêm thành viên", ok="Đã thêm.")
                if b.button("Khôi phục" if g["archived_at"] else "Lưu trữ lớp", key=f"ga_{gid}"):
                    act(lambda c: admin.archive_group(c, gid, not g["archived_at"]), key=f"ga_{gid}", payload=1, reason="lưu trữ lớp",
                        ok="Đã cập nhật.")


def sec_kinds():
    w = can_write()
    ks = st.session_state.get("kind_list", [])
    st.caption("Đổi tên hiển thị, ẩn loại ít dùng, hoặc thêm loại ca mới. Loại ca mới **chưa được tính công** cho tới khi bạn đưa nó vào "
               "Quy tắc chấm công và xác nhận.")
    df = pd.DataFrame([{"Mã": k["code"], "Tên hiển thị": k["label"], "Hiện": k["active"], "Thứ tự": k["sort"]} for k in ks])
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Mã"] if w else True, key="kinds_ed")
    if w and st.button("Lưu loại ca", type="primary"):
        def _save(c):
            for r in ed.itertuples(index=False):
                admin.save_kind(c, owner(), r[0], r[1], bool(r[2]) or r[0] == "regular", int(r[3]))
        act(_save, key="kinds", payload=ed.to_json(), reason="loại ca", ok="Đã lưu loại ca.")
    if w:
        with st.form("kind_add", clear_on_submit=True):
            n = st.text_input("Thêm loại ca mới (VD: Ca ôn thi)")
            go = st.form_submit_button("Thêm")
        if go:
            act(lambda c: admin.add_custom_kind(c, owner(), n), key="kind_add", payload=n, reason="thêm loại ca",
                ok="Đã thêm loại ca. Nhớ cấu hình cột tính công trong Quy tắc chấm công.")
    st.markdown("**Tên trạng thái & lý do nghỉ** (chỉ đổi chữ hiển thị)")
    lb = read(lambda c: admin.labels(c, owner()))
    with st.form("labels"):
        c1, c2 = st.columns(2)
        stv = {k: c1.text_input(k, v, key=f"stl_{k}") for k, v in lb["status"].items()}
        crv = {k: c2.text_input(k, v, key=f"crl_{k}") for k, v in lb["cancel"].items()}
        go = st.form_submit_button("Lưu tên hiển thị", disabled=not w)
    if go:
        act(lambda c: admin.save_labels(c, owner(), stv, crv), key="labels", payload=(repr(stv), repr(crv)), reason="nhãn",
            ok="Đã lưu.")


def sec_rates():
    w = can_write()
    st.caption("Tiền chỉ tính với đơn giá **đã xác nhận**. **Đổi giá tháng sau:** bấm “Đổi giá từ ngày” trên giá cũ — hệ thống tự kết "
               "thúc giá cũ ngày hôm trước và thêm giá mới. Kỳ đã chốt giữ nguyên số tiền lúc chốt.")
    rs = read(lambda c: core.rates(c, owner()), [])
    if rs:
        st.dataframe(pd.DataFrame([{"Loại hình": "Trung tâm" if r["teaching_type"] == "center" else "Dạy riêng",
                                    "Nhóm": CATEGORY_LABEL.get(r["category"], "Buổi dạy riêng (mỗi HV)"),
                                    "Áp cho": r["center_name"] or r["student_name"] or "chung", "Đơn giá": money(r["amount"]),
                                    "Từ": r["valid_from"], "Đến": r["valid_to"],
                                    "Trạng thái": "Lưu trữ" if r["archived_at"] else ("Đã xác nhận" if r["confirmed"] else "CHƯA xác nhận")}
                                   for r in rs]), hide_index=True, width="stretch",
                     column_config={"Từ": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                    "Đến": st.column_config.DateColumn(format="DD/MM/YYYY")})
    if not w:
        return
    centers = read(lambda c: core.centers(c, owner()), [])
    cmap = {"": "Chung"} | {str(x["id"]): x["name"] for x in centers}
    live = [r for r in rs if not r["archived_at"]]
    if live:
        lab = {str(r["id"]): f"{CATEGORY_LABEL.get(r['category'], 'Buổi dạy riêng')} · {r['center_name'] or r['student_name'] or 'chung'} · "
                             f"{money(r['amount'])} từ {r['valid_from']:%d/%m/%Y}" for r in live}
        rid = st.selectbox("Chọn đơn giá để thao tác", list(lab), format_func=lab.get, key="rt_sel")
        r = next(x for x in live if str(x["id"]) == rid)
        a, b, c3 = st.columns(3)
        if a.button("Bỏ xác nhận" if r["confirmed"] else "✓ Xác nhận đơn giá", key="rt_cf", type="secondary" if r["confirmed"] else "primary"):
            act(lambda c: core.confirm_rate(c, rid, not r["confirmed"]), key="rt_cf", payload=(rid, r["confirmed"]), reason="xác nhận đơn giá",
                ok="Đã cập nhật.")
        if b.button("Lưu trữ (nhập nhầm)", key="rt_ar"):
            act(lambda c: core.archive_rate(c, rid, True), key="rt_ar", payload=rid, reason="lưu trữ đơn giá", ok="Đã lưu trữ.")
        with c3.popover("Đổi giá từ ngày"):
            nd = st.date_input("Giá mới áp dụng từ", date(today().year + (today().month // 12), today().month % 12 + 1, 1),
                               key="rt_nd", format="DD/MM/YYYY")
            na = st.number_input("Đơn giá mới (₫)", 0, 100_000_000, int(r["amount"]), 10000, key="rt_na")
            ncf = st.checkbox("Đã xác minh giá mới", key="rt_ncf")
            if st.button("Áp dụng", type="primary", key="rt_ch"):
                from datetime import timedelta

                def _change(c):
                    if nd <= r["valid_from"]:
                        raise BizError("Ngày áp dụng giá mới phải sau ngày bắt đầu của giá cũ.")
                    core.close_rate(c, rid, nd - timedelta(days=1))
                    core.add_rate(c, owner(), r["teaching_type"], r["category"], na, nd, None, r["center_id"], r["student_id"],
                                  confirmed=ncf, note=f"Thay giá từ {nd:%d/%m/%Y}")
                act(_change, key="rt_ch", payload=(rid, nd, na), reason="đổi đơn giá",
                    ok=f"Đã đặt giá mới từ {nd:%d/%m/%Y}" + ("" if ncf else " (CHƯA xác nhận — chưa dùng để tính tiền)") + ".")
    with st.expander("➕ Thêm đơn giá"):
        with st.form("rt_add"):
            c1, c2 = st.columns(2)
            tt = c1.selectbox("Loại hình", ["center", "private"], format_func={"center": "Dạy trung tâm", "private": "Dạy riêng"}.get)
            cat = c2.selectbox("Nhóm", list(CATEGORY_LABEL) + ["private_session"],
                               format_func=lambda k: CATEGORY_LABEL.get(k, "Buổi dạy riêng (mỗi HV)"))
            ce = st.selectbox("Trung tâm", list(cmap), format_func=cmap.get)
            c3, c4 = st.columns(2)
            amt = c3.number_input("Đơn giá (₫)", 0, 100_000_000, 0, 10000)
            vf = c4.date_input("Từ ngày", today().replace(day=1), format="DD/MM/YYYY")
            cf = st.checkbox("Đơn giá đã được xác minh (dùng để tính tiền)")
            note = st.text_input("Ghi chú / nguồn")
            go = st.form_submit_button("Thêm", type="primary")
        if go:
            act(lambda c: core.add_rate(c, owner(), tt, cat, amt, vf, None, ce or None, None, confirmed=cf, note=note or None),
                key="rt_add", payload=(tt, cat, ce, amt, vf, cf), reason="thêm đơn giá",
                ok="Đã thêm đơn giá." + ("" if cf else " Chưa xác nhận nên chưa dùng để tính tiền."))
        if st.button("Thêm 4 đơn giá gợi ý từ file Excel cũ (200k/200k/250k/250k — CHƯA xác nhận)", key="rt_sug"):
            act(lambda c: [core.add_rate(c, owner(), "center", k, v, today().replace(day=1), None, None, confirmed=False,
                                         note="Gợi ý từ hàng 41 file Excel gốc — CHƯA xác nhận") for k, v in EXCEL_SUGGESTED.items()],
                key="rt_sug", payload=1, reason="đơn giá gợi ý", ok="Đã thêm 4 đơn giá gợi ý (chưa xác nhận).")


def sec_rules():
    w = can_write()
    rules = read(lambda c: core.timesheet_rules(c, owner()))
    ks = st.session_state.get("kind_list", [])
    codes = [k["code"] for k in ks]
    st.caption("Mỗi ca đã xác nhận vào **tối đa một** cột: 30 PHÚT → DẠY THỬ (J) → 1–4 HV theo số tên. Mỗi lần lưu tạo phiên bản mới "
               "có ngày hiệu lực; tháng đã chốt không đổi. Công thức gốc cộng đôi ca “Dạy thêm giờ” (E–H và J) — đề xuất bỏ phần cộng đôi.")
    st.markdown(f"Đang dùng: **phiên bản {rules.version}** — " + ("✅ đã xác nhận" if rules.confirmed else "⚠ **chưa xác nhận**"))
    with st.form("rules"):
        mx = st.number_input("Ca ≤ (phút) vào cột 30 PHÚT", 0, 120, rules.thirty_minute_max)
        th = st.multiselect("…áp dụng cho loại ca", codes, [k for k in rules.thirty_kinds if k in codes], format_func=kind_label)
        jk = st.multiselect("Loại ca ghi vào cột J “DẠY THỬ” (không đếm E–H)", codes, [k for k in rules.j_kinds if k in codes],
                            format_func=kind_label)
        hv = st.multiselect("Loại ca đếm vào E–H theo số học viên", codes, [k for k in rules.hv_kinds if k in codes], format_func=kind_label)
        vf = st.date_input("Áp dụng từ ngày (để trống = mọi tháng chưa chốt)", None, format="DD/MM/YYYY")
        note = st.text_input("Ghi chú (VD: theo email trung tâm ngày…)")
        cf = st.checkbox("Tôi xác nhận quy tắc này đúng với cách trung tâm tính công")
        go = st.form_submit_button("Lưu phiên bản mới", type="primary", disabled=not w)
    if go:
        if set(jk) & set(hv):
            st.error("Một loại ca không thể vừa vào cột J vừa vào E–H (sẽ cộng đôi).")
        else:
            act(lambda c: core.save_timesheet_rules(c, owner(), TimesheetRules(int(mx), tuple(th), tuple(jk), tuple(hv), confirmed=cf),
                                                    vf, note or None, uid()),
                key="rules", payload=(mx, th, jk, hv, vf, cf), reason="quy tắc chấm công", ok="Đã tạo phiên bản quy tắc mới.")
    vs = read(lambda c: core.rule_versions(c, owner()), [])
    with st.expander(f"Các phiên bản ({len(vs)})"):
        for v in vs:
            r = v["rules"]
            a, b = st.columns([4, 1])
            a.write(f"**v{v['version']}** · từ {'mọi tháng' if v['valid_from'].year <= 2000 else v['valid_from'].strftime('%d/%m/%Y')} · "
                    f"30′≤{r.get('thirty_minute_max')}: {', '.join(map(kind_label, r.get('thirty_kinds', [])))} · "
                    f"J: {', '.join(map(kind_label, r.get('j_kinds', [])))} · E–H: {', '.join(map(kind_label, r.get('hv_kinds', [])))} · "
                    + ("✅" if v["confirmed"] else "chưa xác nhận") + (f" · {v['note']}" if v["note"] else ""))
            if w and b.button("Bỏ xác nhận" if v["confirmed"] else "Xác nhận", key=f"rv_{v['id']}"):
                act(lambda c, i=v["id"], x=v["confirmed"]: core.confirm_rule_version(c, owner(), i, not x), key=f"rv_{v['id']}",
                    payload=v["confirmed"], reason="xác nhận quy tắc", ok="Đã cập nhật.")
    st_ = read(lambda c: core.settings(c, owner()))
    with st.expander("Ca nghỉ / hủy có được tính công?"):
        from .common import cancel_labels
        cl = cancel_labels()
        with st.form("cancel_rules"):
            vals = {}
            for k, lbl in cl.items():
                cur = st_["cancel_pay_rules"].get(k, {})
                c1, c2 = st.columns([3, 1])
                vals[k] = (c1.selectbox(lbl, [False, True], index=int(bool(cur.get("pay"))),
                                        format_func=lambda x: "Có tính công" if x else "Không tính công", key=f"cp_{k}"),
                           c2.checkbox("đã xác nhận", bool(cur.get("confirmed")), key=f"cc_{k}"))
            go = st.form_submit_button("Lưu", disabled=not w)
        st.caption("Mặc định không tính. Hiện hệ thống chỉ ghi nhận lựa chọn “có tính công”, chưa tự cộng vào E–H.")
        if go:
            act(lambda c: core.save_cancel_rules(c, owner(), {k: {"pay": v[0], "confirmed": v[1]} for k, v in vals.items()}),
                key="cancel_rules", payload=repr(vals), reason="quy tắc ca hủy", ok="Đã lưu.")
    with st.expander("Ý nghĩa cột “Còn không” (Google Form cũ)"):
        st.caption("Dữ liệu cho thấy đây nhiều khả năng là câu hỏi điều hướng “còn ca nữa không?”, không phải điểm danh. "
                   "Hệ thống không dùng cột này để tính công.")
        with st.form("flag"):
            m = st.text_area("Ý nghĩa theo bạn", st_["flag_meaning"] or "Câu hỏi điều hướng của Google Form: Còn = còn ca tiếp theo, "
                             "Không = hết ca trong ngày.")
            cf = st.checkbox("Xác nhận", st_["flag_meaning_confirmed"])
            go = st.form_submit_button("Lưu", disabled=not w)
        if go:
            act(lambda c: core.save_flag_meaning(c, owner(), m, cf), key="flag", payload=(m, cf), reason="ý nghĩa Còn không", ok="Đã lưu.")


def sec_excel():
    w = can_write()
    o = read(lambda c: admin.export_options(c, owner()))
    st.markdown("**File xuất bảng công** — sheet chính luôn giữ đúng bố cục mẫu gốc (A:L, header 2 tầng, ô gộp, dd/mm, hàng TỔNG).")
    with st.form("xopt"):
        c1, c2 = st.columns(2)
        sn = c1.text_input("Tên sheet chính", o["sheet_name"], max_chars=31)
        fp = c2.text_input("Tiền tố tên file", o["file_prefix"], max_chars=40)
        mr = st.checkbox("Hàng tiền dưới TỔNG (chỉ khi đơn giá đã xác nhận đủ)", o["include_money_row"])
        sp = st.checkbox("Sheet “Bổ sung” (ca > 4 HV, ca cần kiểm tra)", o["include_supplement"])
        dt = st.checkbox("Sheet “Chi tiết ca”", o["include_detail"])
        inf = st.checkbox("Sheet “Thông tin”", o["include_info"])
        go = st.form_submit_button("Lưu tùy chọn", disabled=not w)
    if go:
        act(lambda c: admin.save_export_options(c, owner(), {"sheet_name": sn, "file_prefix": fp, "include_money_row": mr,
                                                             "include_supplement": sp, "include_detail": dt, "include_info": inf}),
            key="xopt", payload=(sn, fp, mr, sp, dt, inf), reason="tùy chọn xuất Excel", ok="Đã lưu tùy chọn.")
    from ..domain.excel_export import blank_template_xlsx
    st.download_button("Tải mẫu chấm công trống", blank_template_xlsx(), "MauChamCong_trong.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.markdown("**Hồ sơ ánh xạ file trung tâm** (tạo nhanh nhất từ màn hình Đối soát → “Lưu làm hồ sơ”)")
    for m in read(lambda c: admin.mappings(c, owner(), True), []):
        a, b = st.columns([4, 1])
        cols = ", ".join(f"{FIELDS.get(k, k)}={v}" for k, v in m["mapping"].get("columns", {}).items())
        a.write(f"**{m['name']}** · {m['center_name'] or 'mọi trung tâm'}" + (" · mặc định" if m["is_default"] else "")
                + (" · lưu trữ" if m["archived_at"] else "") + f"  \n<span class='anhu-muted'>{cols}</span>", unsafe_allow_html=True)
        if w and b.button("Khôi phục" if m["archived_at"] else "Lưu trữ", key=f"mp_{m['id']}"):
            act(lambda c, i=m["id"], x=m["archived_at"]: admin.archive_mapping(c, i, not x), key=f"mp_{m['id']}", payload=1,
                reason="hồ sơ ánh xạ", ok="Đã cập nhật.")
    st.divider()
    st.markdown("**Nhập lịch sử từ file “Chấm công (Câu trả lời).xlsx”** — đọc 3 sheet, tự loại phản hồi trùng, xem trước trước khi ghi; "
                "nhập lại cùng file không nhân đôi công.")
    if not w:
        return
    f = st.file_uploader("File .xlsx (tối đa 10 MB)", type=["xlsx"], key="lg_up")
    if f and st.button("Xem trước", key="lg_prev"):
        data = f.getvalue()
        if len(data) > 10 * 1024 * 1024:
            st.error("File tối đa 10 MB.")
        else:
            res = act(lambda c: reports.legacy_preview(c, owner(), f.name, data), key="lg_prev", payload=(f.name, len(data)),
                      reason="xem trước import", rerun=False)
            if res:
                iid, plan, stats, existing = res
                keep = [p.fingerprint for p in plan.sessions if p.day and p.fingerprint not in existing]
                st.session_state["lg"] = {"iid": str(iid), "stats": stats, "keep": keep,
                                          "rows": [{"Ngày": p.day, "Cơ sở": p.location, "Giờ": p.time_raw, "Học viên": ", ".join(p.attendees),
                                                    "Số HV": len(p.attendees), "Loại": kind_label(p.kind), "Vấn đề": "; ".join(p.issues)}
                                                   for p in plan.sessions if p.day and p.fingerprint not in existing]}
    lg = st.session_state.get("lg")
    if lg:
        s = lg["stats"]
        st.info(f"{s['responses']} phản hồi đọc được → {s['unique']} phản hồi duy nhất ({s['duplicates']} bản trùng bị loại). "
                f"{s['new']} ca mới · {s['already_imported']} ca đã có · {s['needs_review']} ca sẽ ở trạng thái Cần kiểm tra.")
        st.dataframe(pd.DataFrame(lg["rows"]), hide_index=True, width="stretch", height=300)
        a, b = st.columns(2)
        if a.button(f"Ghi {len(lg['keep'])} ca", type="primary", key="lg_go", disabled=not lg["keep"]):
            r = act(lambda c: reports.legacy_apply(c, owner(), uid(), lg["iid"], keep=set(lg["keep"])), key="lg_go", payload=lg["iid"],
                    reason="import lịch sử", rerun=False)
            if r:
                st.session_state.pop("lg", None)
                flash(f"Đã nhập {r['inserted']} ca; bỏ qua {r['skipped']} (trùng/đã có).")
                st.rerun()
        if b.button("Hủy", key="lg_cancel"):
            st.session_state.pop("lg", None)
            st.rerun()


def sec_audit():
    a = st.columns(3)
    tbl = a[0].selectbox("Loại dữ liệu", [""] + list(admin.TABLE_LABEL), format_func=lambda k: admin.TABLE_LABEL.get(k, "Tất cả"))
    q = a[1].text_input("Tìm trong nội dung / lý do")
    page = a[2].number_input("Trang", 1, 10000, 1)
    items, total = read(lambda c: admin.audit_search(c, owner(), tbl or None, None, None, None, q or None, int(page)), ([], 0))
    st.caption(f"{total} mục. Nhật ký do cơ sở dữ liệu tự ghi; ứng dụng không sửa/xóa được.")
    if items:
        st.dataframe(pd.DataFrame([{"Lúc": it["at"], "Người": it["actor"] or "—", "Dữ liệu": admin.TABLE_LABEL.get(it["table_name"], it["table_name"]),
                                    "Thao tác": admin.ACTION_LABEL.get(it["action"], it["action"]),
                                    "Thay đổi": "; ".join(f"{k}: {b} → {n}" if it["action"] == "UPDATE" else f"{k}: {n}"
                                                          for k, b, n in it["changes"]), "Lý do": it["reason"] or ""}
                                   for it in items]), hide_index=True, width="stretch",
                     column_config={"Lúc": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm:ss")})


def sec_backup():
    info = read(lambda c: admin.system_info(c, owner()))
    lb = info["last_backup"]
    (st.info if lb else st.warning)(f"Bản sao lưu gần nhất: {lb['created_at']:%d/%m/%Y %H:%M}" if lb else "Chưa có bản sao lưu nào.")
    st.caption("Nên tải bản sao lưu sau mỗi lần chốt kỳ và lưu ở 2 nơi. File chứa dữ liệu thật của học viên — không gửi công khai.")
    if owner_info()["self"]:
        if st.button("Chuẩn bị bản sao lưu", type="primary"):
            r = act(lambda c: backup.export_backup(c, owner(), uid()), key="bk", payload=today().isoformat() + str(pd.Timestamp.now().minute),
                    reason="tải bản sao lưu", rerun=False)
            if r:
                st.session_state["bk_file"] = (r[0], r[1])
        if st.session_state.get("bk_file"):
            d, n = st.session_state["bk_file"]
            st.download_button(f"⬇ Tải {n}", d, n, "application/zip")
    st.divider()
    f = st.file_uploader("Kiểm tra / khôi phục từ file sao lưu (.zip)", type=["zip"], key="bk_up")
    if not f:
        st.session_state.pop("bk_rep", None)
        return
    blob = f.getvalue()
    if st.button("Kiểm tra file (không ghi dữ liệu)", key="bk_vbtn"):
        rep = act(lambda c: backup.verify_backup(c, owner(), uid(), blob, f.name), key="bk_v", payload=(f.name, len(blob)),
                  reason="kiểm tra bản sao lưu", rerun=False)
        st.session_state["bk_rep"] = (f.name, len(blob), rep) if rep else None
    held = st.session_state.get("bk_rep")
    if not held or held[:2] != (f.name, len(blob)):
        return
    rep = held[2]
    mf = rep["manifest"]
    st.success(f"File nguyên vẹn (mã băm và số dòng khớp). Tạo {mf['created_at']} · tài khoản {mf['owner'].get('display_name')} · "
               f"{sum(v for k, v in mf['counts'].items() if k != 'audit_log')} dòng dữ liệu.")
    if rep["newer_migrations"]:
        st.error("Bản sao lưu từ phiên bản CSDL mới hơn — cập nhật ứng dụng trước khi khôi phục.")
    elif not rep["empty_target"]:
        st.caption("Tài khoản này đã có dữ liệu nên không khôi phục được (tránh ghi đè/trộn dữ liệu). "
                   "Khôi phục dùng khi chuyển sang máy chủ mới, trên tài khoản mới tạo.")
    elif owner_info()["self"]:
        t = st.text_input("Gõ KHOI PHUC để xác nhận khôi phục toàn bộ vào tài khoản này")
        if st.button("Khôi phục", type="primary", disabled=t.strip().upper() != "KHOI PHUC"):
            st.session_state.pop("bk_rep", None)
            act(lambda c: backup.restore_backup(c, owner(), uid(), blob, f.name), key="bk_r", payload=f.name,
                reason="khôi phục từ bản sao lưu", ok="Đã khôi phục và đếm lại từng bảng: khớp với bản sao lưu.")


def sec_system():
    info = read(lambda c: admin.system_info(c, owner()))
    st.write(f"**ANHU Teaching Studio {info['version']}** · PostgreSQL {info['pg']} · {len(info['migrations'])} migration đã áp dụng")
    cn = info["counts"]
    st.write(f"Học viên: {cn['students']} · buổi đã xác nhận: {cn['sessions']} · lịch cố định: {cn['templates']} · kỳ đã chốt: {cn['locked']}"
             f" · mục nhật ký: {cn['audit']}")
    for w_ in info["warnings"]:
        st.warning(w_)
    st.caption("Đồng bộ Google Forms/Sheets/Calendar: chưa triển khai trong phiên bản này. Cập nhật phiên bản & chuyển máy chủ: "
               "xem docs/DEPLOY_STREAMLIT.md.")


def render():
    st.title("Cài đặt")
    help_box("Những mục ít dùng. Lần đầu nên làm theo thứ tự: **Trung tâm & cơ sở → Học viên → Lịch cố định → Quy tắc chấm công → Đơn giá**. "
             "Mọi thay đổi quan trọng được ghi nhật ký.")
    sec = st.selectbox("Mục", SECTIONS, key="set_sec", label_visibility="collapsed")
    {"Tài khoản": sec_account, "Trung tâm & cơ sở": sec_catalog, "Môn, lớp & nhóm": sec_groups, "Loại ca & trạng thái": sec_kinds,
     "Đơn giá": sec_rates, "Quy tắc chấm công": sec_rules, "Mẫu Excel & dữ liệu cũ": sec_excel, "Nhật ký": sec_audit,
     "Sao lưu & khôi phục": sec_backup, "Hệ thống": sec_system}[sec]()
    st.divider()
    if st.button("Đăng xuất", icon=":material/logout:"):
        with db() as c:
            accounts.logout(c, user().get("session"))
        st.session_state.clear()
        st.session_state["_dev_clear"] = True
        st.rerun()
