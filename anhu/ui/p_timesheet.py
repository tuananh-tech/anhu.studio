"""Chấm công: bảng giống mẫu Excel gốc (đủ ngày), chi tiết ca, tiền công, xuất Excel, chốt/mở kỳ, đối soát, kỳ chấm công."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ..db import rows, scalar
from ..domain.calendar_month import month_bounds
from ..domain.reconcile import CATEGORY_LABEL as RECON_LABEL
from ..domain.reconcile import FIELDS, preview, suggest_mapping
from ..services import admin, core, reports
from .common import (act, can_write, db, esc, flash, help_box, hm, kind_label, money, month_picker, owner, read, seg,
                     status_label, today, uid, wd)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COLS = "ABCDEFGHIJKL"


def excel_table_html(view) -> str:
    """Bảng giống sheet 'Chấm công' gốc: header 2 tầng, mỗi ngày ≥ 1 hàng, ngày trống chỉ có cột NGÀY."""
    h = ["<div class='anhu-xl-wrap'><table class='anhu-xl'><thead>",
         "<tr><th class='d' rowspan='2'>NGÀY</th><th rowspan='2'>CƠ SỞ</th><th></th><th></th><th colspan='6'>SỐ GIỜ DẠY</th>"
         "<th rowspan='2'>GHI CHÚ</th><th rowspan='2'>TRUNG TÂM XÁC NHẬN</th></tr>",
         "<tr><th>Thời gian Ca học</th><th>Tên Học Viên Hiện Diện</th><th>1 HV</th><th>2 HV</th><th>3HV</th><th>4HV</th>"
         "<th>30 PHÚT</th><th>DẠY THỬ</th></tr></thead><tbody>"]
    for r in view.rows:
        c = r.cells
        cls = ("e " if not r.session_ids else "") + ("w" if r.day.weekday() >= 5 else "")
        tds = [f"<td class='d' title='{wd(r.day)}'>{r.day:%d/%m}</td>", f"<td>{esc(c.get('B') or '')}</td>",
               f"<td class='pre'>{esc(c.get('C') or '')}</td>", f"<td class='pre'>{esc(c.get('D') or '')}</td>"]
        tds += [f"<td class='c'>{'' if c.get(k) is None else esc(c.get(k))}</td>" for k in "EFGHI"]
        tds += [f"<td class='pre'>{esc(c.get(k) or '')}</td>" for k in "JKL"]
        h.append(f"<tr class='{cls}'>" + "".join(tds) + "</tr>")
    h.append("<tr class='t'><td class='d'></td><td>TỔNG</td><td></td><td></td>"
             + "".join(f"<td class='c'>{view.totals.get(k, 0)}</td>" for k in "EFGHI") + "<td></td><td></td><td></td></tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def _load(y, m, tt):
    with db() as c:
        per = reports.period(c, owner(), y, m)
        snap = reports.locked_snapshot(c, owner(), y, m) if tt == "center" else None
        if snap:
            view, pay = reports.snapshot_view(snap)
        else:
            view, recs, pay = reports.timesheet(c, owner(), y, m, None if tt == "all" else tt)
            view.from_snapshot = False
        a, b = month_bounds(y, m)
        unconf = scalar(c, """SELECT count(*) FROM planned_sessions WHERE owner_id=:o AND status IN ('planned','needs_review')
                              AND session_date BETWEEN :a AND :b AND session_date <= :t""", o=owner(), a=a, b=b, t=today())
    return per, view, pay, unconf


def view_sheet(detail=False):
    c1, c2 = st.columns(2)
    with c1:
        y, m = month_picker("cc_month")
    tt = c2.selectbox("Loại hình", ["center", "private", "all"], key="cc_tt",
                      format_func={"center": "Dạy trung tâm", "private": "Dạy riêng", "all": "Tổng hợp chung"}.get)
    per, view, pay, unconf = _load(y, m, tt)
    locked = bool(per and per["status"] == "locked")
    if view.from_snapshot:
        st.info(f"Đang hiển thị **bản đã chốt** (phiên bản {per['version']}, {per['locked_at']:%d/%m/%Y %H:%M}). "
                "Đổi danh mục, quy tắc hay đơn giá sau khi chốt không làm thay đổi bảng này.")
    elif not view.rules.confirmed:
        st.warning(f"Quy tắc xếp cột (30 PHÚT, DẠY THỬ, ca thêm giờ) là **đề xuất chưa xác nhận** (phiên bản {view.rules.version}). "
                   "Xác nhận ở Cài đặt → Quy tắc chấm công.")
    if unconf and not locked:
        st.warning(f"Còn **{unconf}** ca đã tới ngày nhưng chưa xác nhận — chưa được tính.")
    if tt != "center" and not view.from_snapshot:
        st.caption("File “Excel mẫu trung tâm” luôn chỉ gồm ca dạy trung tâm.")
    if not detail:
        st.html(excel_table_html(view))
        st.caption("Mỗi ngày trong tháng đều có một hàng; ngày không có ca để trống (không ghi 0, không ghi “Nghỉ”). "
                   "E–H là **số ca** có đúng 1/2/3/4 học viên, tự đếm từ danh sách tên.")
    else:
        if view.detail:
            df = pd.DataFrame([{"Ngày": d["day"], "Cơ sở": d["location"], "Giờ": f"{hm(d['start'])}–{hm(d['end'])}",
                                "Học viên": ", ".join(d["attendees"]), "Số HV": d["n"], "Phút": d["minutes"],
                                "Loại ca": kind_label(d["kind"]), "Trạng thái": status_label(d["status"]),
                                "Cột": {"SUPP": "Bổ sung (>4 HV)"}.get(d["column"], d["column"]) or f"— {d['not_counted_reason']}"}
                               for d in view.detail])
            st.dataframe(df, hide_index=True, width="stretch",
                         column_config={"Ngày": st.column_config.DateColumn(format="DD/MM/YYYY")})
        else:
            st.caption("Chưa có buổi dạy nào trong tháng.")
    if view.supplemental:
        with st.expander(f"Ca trên 4 học viên — báo cáo bổ sung ({len(view.supplemental)})"):
            for s in view.supplemental:
                st.write(f"• {s['day']:%d/%m} {s['time']} — {s['n']} HV: {', '.join(s['attendees'])}")
    if view.review:
        with st.expander(f"⚠ Cần kiểm tra — chưa tính công ({len(view.review)})", expanded=True):
            from .p_schedule import edit_actual_dialog
            for s in view.review:
                c1, c2 = st.columns([4, 1])
                c1.write(f"{s['day']:%d/%m} {s['time']} — {s['reason']} · {', '.join(s['attendees'])}")
                if can_write() and not locked and c2.button("Xử lý", key=f"rv_{s['id']}"):
                    edit_actual_dialog(s["id"])

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**Tiền công dạy trung tâm**")
        if pay and pay.by_category:
            st.dataframe(pd.DataFrame([{"Nhóm": v["label"], "Số ca": v["count"], "Thành tiền": money(v["amount"]) if v["amount"] else "—"}
                                       for v in pay.by_category.values()]), hide_index=True, width="stretch")
            if pay.complete:
                st.markdown(f"Tổng: **{money(pay.total)}** " + ("(đã chốt)" if locked else "(ước tính)"))
            else:
                st.caption(f"Không hiển thị tổng: {len(pay.missing)} ca chưa có đơn giá đã xác nhận (Cài đặt → Đơn giá).")
        else:
            st.caption("Chưa có ca dạy trung tâm được tính trong tháng." if tt == "center" else "Chọn “Dạy trung tâm” để xem tiền công.")
    with c2.container(border=True):
        st.markdown("**File Excel & chốt kỳ**")
        if st.button("Chuẩn bị file Excel mẫu trung tâm", key="cc_prep", width="stretch"):
            with db() as c:
                lk = reports.locked_export(c, owner(), y, m)
                prefix = admin.export_options(c, owner())["file_prefix"]
                if lk:
                    st.session_state["cc_file"] = (bytes(lk["export_bytes"]), f"{prefix}_{y}-{m:02d}_DA_CHOT_{lk['export_sha256'][:8]}.xlsx")
                else:
                    st.session_state["cc_file"] = (reports.export_timesheet(c, owner(), y, m), f"{prefix}_{y}-{m:02d}.xlsx")
        f = st.session_state.get("cc_file")
        if f and f"{y}-{m:02d}" in f[1]:
            st.download_button(f"⬇ Tải {f[1]}", f[0], f[1], XLSX, width="stretch", type="primary")
        if locked:
            st.caption(f"Đã chốt {per['locked_at']:%d/%m/%Y %H:%M} · phiên bản {per['version']} · SHA-256 {per['export_sha256'][:16]}…")
            if can_write():
                reason = st.text_input("Lý do mở kỳ (bắt buộc)", key="cc_unlock_r")
                if st.button("Mở kỳ để điều chỉnh", key="cc_unlock", disabled=not reason.strip()):
                    act(lambda c: reports.unlock_period(c, owner(), y, m, reason), key="cc_unlock", payload=(y, m, reason),
                        reason=reason, ok="Đã mở kỳ (lý do được ghi nhật ký).")
        elif can_write():
            st.caption("Chốt kỳ lưu ảnh chụp bảng công + file Excel kèm mã băm; sau đó CSDL khóa mọi sửa đổi buổi dạy trong tháng.")
            ok = st.checkbox(f"Tôi đã kiểm tra xong bảng công {m:02d}/{y}", key="cc_lock_ok")
            if st.button(f"Chốt kỳ {m:02d}/{y}", key="cc_lock", disabled=not ok):
                r = act(lambda c: reports.lock_period(c, owner(), uid(), y, m), key="cc_lock", payload=(y, m), reason="chốt kỳ",
                        rerun=False)
                if r:
                    flash(f"Đã chốt kỳ {m:02d}/{y}. Mã băm file: {r[0][:16]}…" + (f" Lưu ý: {r[1]} ca Cần kiểm tra không được tính." if r[1] else ""))
                    st.session_state.pop("cc_file", None)
                    st.rerun()


# ---------------- đối soát ----------------
def view_recon():
    st.caption("Tải file Excel của trung tâm → ghép cột → hệ thống so với bảng công của bạn và chỉ ra từng chênh lệch kèm bằng chứng. "
               "File trung tâm **không** bao giờ tự sửa dữ liệu của bạn.")
    imps = read(lambda c: rows(c, """SELECT i.id, i.filename, i.year, i.month, i.status, i.created_at, c.name AS center_name,
                                     (SELECT count(*) FROM reconciliation_items x WHERE x.import_id = i.id AND x.resolution = 'pending') AS pending
                                     FROM reconciliation_imports i LEFT JOIN centers c ON c.id = i.center_id
                                     WHERE i.owner_id = :o ORDER BY i.created_at DESC LIMIT 30""", o=owner()), [])
    sel = st.session_state.get("rc_sel")
    if can_write():
        with st.expander("📤 Tải file trung tâm mới", expanded=not imps):
            centers = read(lambda c: core.centers(c, owner()), [])
            cmap = {"": "—"} | {str(c["id"]): c["name"] for c in centers}
            c1, c2 = st.columns(2)
            ci = c1.selectbox("Trung tâm", list(cmap), format_func=cmap.get, key="rc_center")
            with c2:
                y, m = month_picker("rc_month", "Kỳ")
            f = st.file_uploader("File .xlsx của trung tâm (tối đa 10 MB)", type=["xlsx"], key="rc_up")
            if f and st.button("Tải lên & ghép cột", type="primary", key="rc_go"):
                data = f.getvalue()
                if len(data) > 10 * 1024 * 1024:
                    st.error("File tối đa 10 MB.")
                else:
                    iid = act(lambda c: reports.recon_upload(c, owner(), ci or None, y, m, f.name, data), key="rc_up",
                              payload=(f.name, len(data), y, m), reason="tải file đối soát", rerun=False)
                    if iid:
                        st.session_state["rc_sel"] = str(iid)
                        st.session_state["rc_step"] = "map"
                        st.rerun()
    if imps:
        lab = {str(i["id"]): f"{i['month']:02d}/{i['year']} · {i['filename']} · {i['center_name'] or '—'}"
                              + (f" · {i['pending']} chờ duyệt" if i["pending"] else "") for i in imps}
        keys = list(lab)
        sel = st.selectbox("Phiên đối soát", keys, index=keys.index(sel) if sel in keys else 0, format_func=lab.get, key="rc_pick")
        if sel != st.session_state.get("rc_sel"):
            st.session_state["rc_sel"] = sel
            st.session_state["rc_step"] = None
        imp = read(lambda c: reports.recon_import(c, sel))
        if not imp:
            return
        step = st.session_state.get("rc_step") or ("map" if imp["status"] == "uploaded" else "result")
        if step == "map" and can_write():
            _mapping(imp)
        else:
            _result(imp)


def _mapping(imp):
    iid = str(imp["id"])
    pv = preview(bytes(imp["file_bytes"]), 12)
    with db() as c:
        mp = (imp["mapping"] or admin.default_mapping(c, owner(), imp["center_id"]) or reports.last_mapping(c, owner(), imp["center_id"])
              or suggest_mapping(pv))
    sheets = list(pv)
    sheet = st.selectbox("Sheet", sheets, index=sheets.index(mp.get("sheet")) if mp.get("sheet") in sheets else 0, key=f"rm_sh_{iid}")
    rows_ = pv[sheet]
    width = max((len(r) for r in rows_), default=0)
    letters = [chr(65 + i) for i in range(min(max(width, 12), 26))]
    st.dataframe(pd.DataFrame([list(r) + [None] * (width - len(r)) for r in rows_], columns=letters[:width] or None,
                              index=range(1, len(rows_) + 1)), width="stretch", height=260)
    start = st.number_input("Dữ liệu bắt đầu từ hàng", 1, 1000, int(mp.get("start_row", 3)), key=f"rm_sr_{iid}")
    cols = mp.get("columns", {})
    picked = {}
    cc = st.columns(3)
    for i, (k, label) in enumerate(FIELDS.items()):
        opts = [""] + letters
        cur = cols.get(k, "")
        picked[k] = cc[i % 3].selectbox(label, opts, index=opts.index(cur) if cur in opts else 0, key=f"rm_{k}_{iid}",
                                        format_func=lambda x: x or "— không có —")
    save = st.checkbox("Lưu làm hồ sơ ánh xạ mặc định cho trung tâm này", value=True, key=f"rm_save_{iid}")
    if st.button("Chạy đối chiếu", type="primary", key=f"rm_go_{iid}", disabled=not picked.get("date")):
        mapping = {"sheet": sheet, "start_row": int(start), "columns": {k: v for k, v in picked.items() if v}}

        def _run(c):
            if save:
                admin.save_mapping(c, owner(), f"Mẫu {imp['filename'].rsplit('.', 1)[0]}"[:80], imp["center_id"], mapping, is_default=True)
            return reports.recon_run(c, owner(), iid, mapping)
        items = act(_run, key=f"rm_{iid}", payload=repr(mapping), reason="chạy đối soát", rerun=False)
        if items is not None:
            bad = sum(1 for i in items if i.category != "match")
            flash(f"Đã đối soát: {len(items) - bad} mục khớp, {bad} mục chênh lệch cần duyệt.", "success" if not bad else "warning")
            st.session_state["rc_step"] = "result"
            st.rerun()


def _result(imp):
    iid = str(imp["id"])
    st.caption(f"Kỳ {imp['month']:02d}/{imp['year']} · SHA-256 file gốc {imp['file_sha256'][:16]}… (file gốc giữ nguyên)")
    counts = read(lambda c: rows(c, "SELECT category, resolution, count(*) AS n FROM reconciliation_items WHERE import_id = :i "
                                    "GROUP BY 1, 2", i=iid), [])
    by_cat = {}
    for r in counts:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + r["n"]
    if by_cat:
        st.write(" · ".join(f"{RECON_LABEL.get(k, k)}: **{v}**" for k, v in by_cat.items()))
    c1, c2, c3 = st.columns(3)
    only = c1.selectbox("Hiển thị", ["pending", "", "accept_ours", "accept_theirs", "ignored", "auto_match"], key=f"rr_f_{iid}",
                        format_func=lambda x: {"": "Tất cả"}.get(x, reports.RES_LABEL.get(x, x)))
    if c2.button("Ghép cột lại", key=f"rr_remap_{iid}", disabled=not can_write()):
        st.session_state["rc_step"] = "map"
        st.rerun()
    if c3.button("Chuẩn bị biên bản", key=f"rr_rep_{iid}"):
        st.session_state["rc_rep"] = (iid, read(lambda c: reports.recon_report_xlsx(c, iid)))
    rep = st.session_state.get("rc_rep")
    if rep and rep[0] == iid:
        st.download_button("⬇ Tải biên bản đối soát", rep[1], f"BienBanDoiSoat_{imp['year']}-{imp['month']:02d}.xlsx", XLSX)
    items = read(lambda c: reports.recon_items(c, iid, None, only or None), [])
    if not items:
        st.success("Không còn chênh lệch chờ duyệt." if only == "pending" else "Không có mục nào.")
    for it in items[:200]:
        with st.container(border=True):
            st.markdown(f"**{it['day']:%d/%m}** · {RECON_LABEL.get(it['category'], it['category'])} · "
                        f"_{reports.RES_LABEL.get(it['resolution'])}_  \n{esc(it['detail'])}", unsafe_allow_html=True)
            our, th = it["our"] or {}, it["their"] or {}
            ev = []
            if our:
                ev.append("Cá nhân: " + (f"{our.get('time', '')} {', '.join(our.get('attendees') or [])}".strip()
                                         or str(our.get("counts", ""))))
            if th:
                ev.append(f"Trung tâm (hàng {th.get('row', '?')}): " + (f"{th.get('time', '')} {', '.join(th.get('attendees') or [])}".strip() or str(th.get('raw', ''))
                                                                       or str(th.get("counts", ""))))
            if ev:
                st.caption("  \n".join(ev))
            if can_write() and it["resolution"] != "auto_match":
                c1, c2, c3 = st.columns([2, 3, 1])
                res = c1.selectbox("Quyết định", ["accept_ours", "accept_theirs", "ignored", "pending"], key=f"ri_r_{it['id']}",
                                   index=["accept_ours", "accept_theirs", "ignored", "pending"].index(it["resolution"])
                                   if it["resolution"] in ("accept_ours", "accept_theirs", "ignored", "pending") else 0,
                                   format_func=reports.RES_LABEL.get, label_visibility="collapsed")
                note = c2.text_input("Ghi chú", it.get("resolution_note") or "", key=f"ri_n_{it['id']}", label_visibility="collapsed",
                                     placeholder="Ghi chú (bắt buộc khi theo trung tâm / bỏ qua)")
                if c3.button("Lưu", key=f"ri_go_{it['id']}"):
                    act(lambda c, i=it["id"]: reports.recon_resolve(c, uid(), i, res, note), key=f"ri_{it['id']}", payload=(res, note),
                        reason=f"duyệt chênh lệch: {res}", ok="Đã ghi quyết định. Dữ liệu cá nhân không bị thay đổi.")


def view_periods():
    pers = read(lambda c: admin.periods(c, owner()), [])
    if not pers:
        st.info("Chưa chốt kỳ nào. Vào Bảng công của tháng, kiểm tra xong rồi bấm “Chốt kỳ”.")
        return
    st.dataframe(pd.DataFrame([{"Kỳ": f"{p['month']:02d}/{p['year']}", "Trạng thái": "Đã chốt" if p["status"] == "locked" else "Đang mở lại",
                                "Phiên bản": p["version"], "Chốt lúc": p["locked_at"], "Người chốt": p["locker"],
                                "Mã băm": (p["export_sha256"] or "")[:16], "Lần mở gần nhất": p["last_unlock_reason"] or ""}
                               for p in pers]), hide_index=True, width="stretch",
                 column_config={"Chốt lúc": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
    lab = {(p["year"], p["month"]): f"{p['month']:02d}/{p['year']}" for p in pers}
    ym = st.selectbox("Xem các phiên bản đã chốt của kỳ", list(lab), format_func=lab.get, key="kp_ym")
    for v in read(lambda c: admin.period_versions(c, owner(), *ym), []):
        with st.container(border=True):
            tot = v["totals"] or {}
            st.markdown(f"**Phiên bản {v['version']}** — chốt {v['locked_at']:%d/%m/%Y %H:%M}" + (f" bởi {v['locker']}" if v["locker"] else "")
                        + "  \nTỔNG: " + " ".join(f"{k}={tot.get(k, '')}" for k in "EFGHI")
                        + (f" · tiền {money(v['pay_total'])}" if v["pay_total"] else ""))
            if v["unlocked_at"]:
                st.caption(f"Mở lại {v['unlocked_at']:%d/%m/%Y %H:%M}: {v['unlock_reason']}")
            st.caption(f"SHA-256 {v['export_sha256']}")
            if st.button(f"Chuẩn bị file bản {v['version']}", key=f"kp_f_{v['id']}"):
                r = read(lambda c, i=v["id"]: admin.period_version_file(c, i))
                st.download_button("⬇ Tải", bytes(r["export_bytes"]),
                                   f"ChamCong_{r['year']}-{r['month']:02d}_ban-chot-v{r['version']}.xlsx", XLSX, key=f"kp_d_{v['id']}")


def render():
    st.title("Chấm công")
    help_box("""
- **Bảng công** giống mẫu Excel gốc: đủ mọi ngày trong tháng, ngày trống để trống, số học viên tự đếm theo tên.
- Cuối tháng: **Đối soát** với file trung tâm → xử lý chênh lệch → **Chốt kỳ** → tải file Excel gửi trung tâm.
- Kỳ đã chốt luôn hiển thị/xuất **đúng bản đã chốt**. Muốn sửa: mở kỳ (ghi lý do), sửa, chốt lại thành phiên bản mới.
- Tiền chỉ hiện khi mọi ca có đơn giá **đã xác nhận**.""")
    v = seg(["Bảng công", "Chi tiết ca", "Đối soát", "Kỳ chấm công"], "cc_view", "Bảng công")
    if v == "Bảng công":
        view_sheet()
    elif v == "Chi tiết ca":
        view_sheet(detail=True)
    elif v == "Đối soát":
        view_recon()
    else:
        view_periods()
