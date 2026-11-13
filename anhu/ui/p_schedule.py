"""Lịch dạy: xem ngày/tuần/tháng, thao tác nhanh trên từng ca, xác nhận nhiều ca có xem trước, ca phát sinh, lịch cố định."""
from __future__ import annotations

from datetime import date, time, timedelta

import pandas as pd
import streamlit as st

from ..domain.calendar_month import month_bounds, week_start
from ..domain.names import split_names
from ..services import core, schedule
from .common import (act, badge, can_write, cancel_labels, db, dm, esc, help_box, hm, kind_label, kinds_active, owner,
                     read, seg, today, uid, wd)

VIEWS = ["Hôm nay", "Tuần", "Tháng", "Chờ xác nhận", "Thêm ca", "Lịch cố định"]


# ---------------- dữ liệu dùng chung ----------------
def _ensure(a: date, b: date):
    """Sinh ca dự kiến cho khoảng đang xem (idempotent; chỉ khi có quyền ghi, tối đa 1 lần/khoảng/phiên)."""
    if not can_write():
        return
    done = st.session_state.setdefault("_ensured", set())
    k = (owner(), a, b)
    if k in done:
        return
    with db() as c:
        schedule.ensure_planned(c, owner(), a, b)
    done.add(k)


def _students():
    return read(lambda c: core.search_students(c, owner(), status="active"), [])


def _locs():
    return read(lambda c: core.locations(c, owner()), [])


def _label_student(s) -> str:
    return s["display_name"] + (f" · {s['code']}" if s.get("code") else "")


def attendee_input(key: str, students: list, expected: list) -> list[dict]:
    """Danh sách học viên THỰC TẾ: chọn hồ sơ + nhập thêm tên. Số học viên tự đếm (không có ô nhập số)."""
    by_id = {str(s["id"]): s for s in students}
    pre = [str(e["student_id"]) for e in expected if e.get("student_id") and str(e["student_id"]) in by_id]
    free = ", ".join(e["name"] for e in expected if not (e.get("student_id") and str(e["student_id"]) in by_id))
    ids = st.multiselect("Học viên có mặt", list(by_id), default=pre, format_func=lambda i: _label_student(by_id[i]),
                         key=f"{key}_ids", placeholder="Chọn từ hồ sơ")
    extra = st.text_input("Tên khác (cách nhau bằng dấu phẩy)", value=free, key=f"{key}_extra",
                          placeholder="VD: Phúc, My, My, Quỳnh")
    out = [{"student_id": i, "name": by_id[i]["display_name"]} for i in ids] + [{"student_id": None, "name": n}
                                                                                for n in split_names(extra)]
    st.caption(f"**= {len(out)} học viên** — tự đếm theo danh sách tên (tên trùng vẫn tính riêng).")
    return out


def _times(key: str, s: time, e: time):
    c1, c2 = st.columns(2)
    return (c1.time_input("Bắt đầu", s, key=f"{key}_s", step=300), c2.time_input("Kết thúc", e, key=f"{key}_e", step=300))


def _override(key: str):
    return st.text_input("Lý do nếu trùng giờ (để trống nếu không trùng)", key=f"{key}_ov")


# ---------------- thẻ ca ----------------
def session_card(p: dict, students: list, locs: list, *, show_date=False):
    disp = schedule.display_status(p, today())
    names = ", ".join(e["name"] for e in p["expected_students"]) or "—"
    with st.container(border=True):
        top = f"**{hm(p['start_time'])}–{hm(p['end_time'])}**"
        if show_date:
            top = f"{wd(p['session_date'])} {dm(p['session_date'])} · " + top
        kind = "" if p["session_kind"] == "regular" else f" · {kind_label(p['session_kind'])}"
        tt = "Dạy riêng" if p["teaching_type"] == "private" else "Trung tâm"
        st.markdown(f"{top} &nbsp; {badge(disp)}  \n{esc(p.get('location_name') or '')} · {tt}{kind}  \n"
                    f"<span class='anhu-muted'>{esc(names)}</span>", unsafe_allow_html=True)
        if not can_write():
            return
        pid = str(p["id"])
        if p["status"] in ("planned", "needs_review"):
            row = st.container(horizontal=True, gap="small")
            if row.button("✓ Đã dạy", key=f"ok_{pid}", type="primary",
                         help="Xác nhận theo danh sách học viên dự kiến. Muốn sửa danh sách: bấm Điều chỉnh."):
                if not p["expected_students"]:
                    st.session_state[f"open_{pid}"] = True
                    st.warning("Ca này chưa có học viên dự kiến — chọn học viên có mặt trong Điều chỉnh.")
                else:
                    act(lambda c: schedule.confirm(c, owner(), uid(), pid, p["expected_students"]), key=f"ok_{pid}", payload=pid,
                        reason="xác nhận buổi dạy", ok=f"Đã xác nhận ca {dm(p['session_date'])} {hm(p['start_time'])}.")
            with row.popover("Điều chỉnh"):
                _adjust(p, students, locs)
        elif p["status"] == "cancelled":
            reason = cancel_labels().get(p.get("cancel_reason"), "")
            st.caption(f"Nghỉ: {reason}{' — ' + p['cancel_note'] if p.get('cancel_note') else ''}")
            row = st.container(horizontal=True, gap="small")
            with row.popover("Thêm dạy bù"):
                d = st.date_input("Ngày dạy bù", today() + timedelta(days=1), key=f"mk_d_{pid}", format="DD/MM/YYYY")
                s, e = _times(f"mk_{pid}", p["start_time"], p["end_time"])
                ov = _override(f"mk_{pid}")
                if st.button("Tạo ca dạy bù", key=f"mk_go_{pid}", type="primary"):
                    act(lambda c: schedule.makeup(c, owner(), pid, day=d, start=s, end=e, override_reason=ov),
                        key=f"mk_{pid}", payload=(pid, d, s, e), reason="tạo dạy bù", ok="Đã tạo ca dạy bù (cần xác nhận khi dạy xong).")
            if row.button("Khôi phục ca", key=f"rs_{pid}"):
                act(lambda c: schedule.restore(c, pid), key=f"rs_{pid}", payload=pid, reason="khôi phục ca", ok="Đã khôi phục ca.")
        elif p["status"] == "confirmed":
            aid = read(lambda c: _actual_id(c, pid))
            if aid and st.button("Sửa buổi đã dạy", key=f"ed_{pid}", type="tertiary"):
                edit_actual_dialog(aid)


def _actual_id(c, pid):
    from ..db import scalar
    return scalar(c, "SELECT id FROM actual_sessions WHERE planned_session_id = :p", p=pid)


def _adjust(p, students, locs):
    pid = str(p["id"])
    mode = st.radio("Việc cần làm", ["Xác nhận (sửa danh sách)", "Báo nghỉ", "Đổi giờ / ngày", "Sửa riêng ca này"],
                    key=f"mode_{pid}", horizontal=False, label_visibility="collapsed")
    if mode == "Xác nhận (sửa danh sách)":
        att = attendee_input(f"cf_{pid}", students, p["expected_students"])
        s, e = _times(f"cf_{pid}", p["start_time"], p["end_time"])
        k = st.selectbox("Loại ca", kinds_active(), index=_kidx(p["session_kind"]), format_func=kind_label, key=f"cf_k_{pid}")
        notes = st.text_input("Ghi chú (tên bé dạy thử, ca ghép…)", p.get("notes") or "", key=f"cf_n_{pid}")
        ov = _override(f"cf_{pid}")
        if st.button("Xác nhận đã dạy", key=f"cf_go_{pid}", type="primary"):
            r = act(lambda c: schedule.confirm(c, owner(), uid(), pid, att, start=s, end=e, session_kind=k, notes=notes or None,
                                               override_reason=ov),
                    key=f"cf_{pid}", payload=(pid, repr(att), s, e, k), reason="xác nhận buổi dạy", rerun=False,
                    ok="Đã xác nhận." if att else "Đã lưu ở trạng thái Cần kiểm tra (chưa có học viên) — chưa tính công.")
            if r:
                st.rerun()
    elif mode == "Báo nghỉ":
        labels = cancel_labels()
        r = st.selectbox("Lý do", list(labels), format_func=labels.get, key=f"cx_r_{pid}")
        note = st.text_input("Ghi chú (không bắt buộc)", key=f"cx_n_{pid}")
        st.caption("Ca nghỉ không được tính công. Có thể thêm dạy bù sau.")
        if st.button("Báo nghỉ ca này", key=f"cx_go_{pid}", type="primary"):
            act(lambda c: schedule.cancel(c, pid, r, note or None), key=f"cx_{pid}", payload=(pid, r), reason="nghỉ/hủy",
                ok="Đã báo nghỉ.")
    elif mode == "Đổi giờ / ngày":
        d = st.date_input("Ngày mới", p["session_date"], key=f"rd_d_{pid}", format="DD/MM/YYYY")
        s, e = _times(f"rd_{pid}", p["start_time"], p["end_time"])
        li = _loc_select(f"rd_l_{pid}", locs, p["location_id"])
        ov = _override(f"rd_{pid}")
        st.caption("Chỉ ca này thay đổi. Muốn đổi cả lịch hằng tuần: Lịch dạy → Lịch cố định.")
        if st.button("Dời ca", key=f"rd_go_{pid}", type="primary"):
            if d == p["session_date"] and li == str(p["location_id"]):
                act(lambda c: schedule.edit_single(c, owner(), pid, start=s, end=e, override_reason=ov), key=f"rd_{pid}",
                    payload=(pid, d, s, e), reason="đổi giờ ca", ok="Đã đổi giờ.")
            else:
                act(lambda c: schedule.reschedule(c, owner(), pid, day=d, start=s, end=e, location_id=li, override_reason=ov),
                    key=f"rd_{pid}", payload=(pid, d, s, e, li), reason="dời lịch", ok=f"Đã dời sang {dm(d)}.")
    else:
        exp = attendee_input(f"es_{pid}", students, p["expected_students"])
        k = st.selectbox("Loại ca", kinds_active(), index=_kidx(p["session_kind"]), format_func=kind_label, key=f"es_k_{pid}")
        notes = st.text_input("Ghi chú", p.get("notes") or "", key=f"es_n_{pid}")
        if st.button("Lưu (chỉ ca này)", key=f"es_go_{pid}", type="primary"):
            act(lambda c: schedule.edit_single(c, owner(), pid, expected=exp, notes=notes, session_kind=k), key=f"es_{pid}",
                payload=(pid, repr(exp), k, notes), reason="sửa riêng ca", ok="Đã sửa riêng ca này.")


def _kidx(code):
    ks = kinds_active()
    return ks.index(code) if code in ks else 0


def _loc_select(key, locs, current=None, label="Cơ sở / địa điểm"):
    ids = [str(l["id"]) for l in locs]
    lab = {str(l["id"]): l["name"] + (f" — {l['center_name']}" if l.get("center_name") else "")
           + (" (dạy riêng)" if l["kind"] == "private_place" else "") for l in locs}
    if not ids:
        st.warning("Chưa có cơ sở/địa điểm — thêm ở Cài đặt → Trung tâm & cơ sở.")
        return None
    idx = ids.index(str(current)) if current and str(current) in ids else 0
    return st.selectbox(label, ids, index=idx, format_func=lab.get, key=key)


@st.dialog("Sửa buổi đã dạy", width="large")
def edit_actual_dialog(aid):
    a = read(lambda c: schedule.actual(c, aid))
    if not a:
        return
    students = _students()
    st.markdown(f"**{dm(a['session_date'])} {hm(a['start_time'])}–{hm(a['end_time'])}** · {esc(a['location_label'])} {badge(a['status'])}")
    att = attendee_input(f"ea_{aid}", students, a["attendees"])
    s, e = _times(f"ea_{aid}", a["start_time"] or time(8), a["end_time"] or time(9))
    k = st.selectbox("Loại ca", kinds_active(), index=_kidx(a["session_kind"]), format_func=kind_label, key=f"ea_k_{aid}")
    notes = st.text_input("Ghi chú", a.get("notes") or "", key=f"ea_n_{aid}")
    pay = paid = None
    if a["teaching_type"] == "private":
        pl = {"na": "Chưa có giá", "unpaid": "Chưa thu", "partial": "Thu một phần", "paid": "Đã thu"}
        pay = st.selectbox("Thu tiền", list(pl), index=list(pl).index(a["payment_status"]), format_func=pl.get, key=f"ea_p_{aid}")
        paid = st.number_input("Đã thu (₫)", min_value=0, step=10000, value=int(a["paid_amount"] or 0), key=f"ea_pa_{aid}")
    reason = st.text_input("Lý do chỉnh sửa (bắt buộc, ghi vào nhật ký)", key=f"ea_r_{aid}")
    ov = _override(f"ea_{aid}")
    c1, c2 = st.columns(2)
    if c1.button("Lưu thay đổi", type="primary", key=f"ea_go_{aid}"):
        if not reason.strip():
            st.error("Nhập lý do chỉnh sửa.")
        else:
            act(lambda c: schedule.update_actual(c, owner(), aid, attendees=att, start=s, end=e, session_kind=k, notes=notes or None,
                                                 override_reason=ov, payment_status=pay, paid_amount=paid),
                key=f"ea_{aid}", payload=(aid, repr(att), s, e, k, notes, pay, paid), reason=reason, ok="Đã lưu (có ghi nhật ký).")
    if c2.button("Hủy xác nhận", key=f"ea_un_{aid}"):
        if not reason.strip():
            st.error("Nhập lý do trước khi hủy xác nhận.")
        else:
            act(lambda c: schedule.unconfirm(c, aid), key=f"ea_un_{aid}", payload=aid, reason=reason,
                ok="Đã hủy xác nhận; ca quay về Dự kiến.")


# ---------------- các chế độ xem ----------------
def _day_list(days: list[date], items: list, students, locs, empty_msg="Không có ca."):
    by = {}
    for p in items:
        by.setdefault(p["session_date"], []).append(p)
    any_ = False
    for d in days:
        ps = by.get(d, [])
        if not ps and len(days) > 1:
            continue
        any_ = True
        hd = f"{wd(d)} {d:%d/%m}" + (" · hôm nay" if d == today() else "")
        st.markdown(f"##### {hd}")
        if not ps:
            st.caption(empty_msg)
        for p in ps:
            session_card(p, students, locs)
    if not any_:
        st.caption(empty_msg)


def _nav_date(key: str, step: int, label_fn):
    d = st.session_state.get(key, today())
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        if st.button("◀", key=f"{key}_p", help="Trước"):
            st.session_state[key] = d - timedelta(days=step)
            st.rerun()
        st.markdown(f"**{label_fn(d)}**")
        if st.button("▶", key=f"{key}_n", help="Sau"):
            st.session_state[key] = d + timedelta(days=step)
            st.rerun()
        if d != today() and st.button("Hôm nay", key=f"{key}_t", type="tertiary"):
            st.session_state[key] = today()
            st.rerun()
    return d


def view_day():
    d = _nav_date("lich_ngay", 1, lambda d: f"{wd(d)} {d:%d/%m/%Y}")
    _ensure(d, d)
    items = read(lambda c: schedule.planned_between(c, owner(), d, d), [])
    _day_list([d], items, _students(), _locs(), "Không có ca nào trong ngày.")


def view_week():
    d = _nav_date("lich_tuan", 7, lambda d: f"Tuần {week_start(d):%d/%m} – {week_start(d) + timedelta(days=6):%d/%m}")
    a = week_start(d)
    b = a + timedelta(days=6)
    _ensure(a, b)
    items = read(lambda c: schedule.planned_between(c, owner(), a, b), [])
    st.caption(f"{len([p for p in items if p['status'] not in ('rescheduled',)])} ca trong tuần.")
    _day_list([a + timedelta(days=i) for i in range(7)], items, _students(), _locs(), "Tuần này chưa có ca.")


def view_month():
    d = st.session_state.get("lich_thang", today())
    with st.container(horizontal=True, gap="small", vertical_alignment="center"):
        if st.button("◀", key="m_p", help="Tháng trước"):
            st.session_state["lich_thang"] = (d.replace(day=1) - timedelta(days=1)).replace(day=1)
            st.rerun()
        st.markdown(f"**Tháng {d:%m/%Y}**")
        if st.button("▶", key="m_n", help="Tháng sau"):
            st.session_state["lich_thang"] = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            st.rerun()
    a, b = month_bounds(d.year, d.month)
    _ensure(a, b)
    items = read(lambda c: schedule.planned_between(c, owner(), a, b), [])
    by = {}
    for p in items:
        by.setdefault(p["session_date"], []).append(p)
    cells = ["<div class='h'>" + x + "</div>" for x in ("T2", "T3", "T4", "T5", "T6", "T7", "CN")]
    start = week_start(a)
    end = week_start(b) + timedelta(days=6)
    x = start
    while x <= end:
        ps = [p for p in by.get(x, []) if p["status"] not in ("rescheduled",)]
        n_wait = sum(1 for p in ps if schedule.display_status(p, today()) in ("pending", "needs_review"))
        cls = ("o " if x.month != d.month else "") + ("td" if x == today() else "")
        inner = f"<b>{x.day}</b>"
        if ps:
            inner += f"<span class='n'>{len(ps)} ca</span>"
            if n_wait:
                inner += f"<br><span class='p'>{n_wait} chờ</span>"
        cells.append(f"<div class='{cls}'>{inner}</div>")
        x += timedelta(days=1)
    st.html("<div class='anhu-cal'>" + "".join(cells) + "</div>")
    pick = st.date_input("Xem chi tiết ngày", min(max(today(), a), b), min_value=a, max_value=b, key="m_pick", format="DD/MM/YYYY")
    _day_list([pick], by.get(pick, []), _students(), _locs(), "Không có ca nào trong ngày.")


def view_pending():
    t = today()
    a = t.replace(day=1) - timedelta(days=31)
    _ensure(a.replace(day=1), t)
    items = [p for p in read(lambda c: schedule.planned_between(c, owner(), a.replace(day=1), t), [])
             if p["status"] in ("planned", "needs_review")]
    if not items:
        st.success("Không còn ca nào chờ xác nhận. 🎉")
        return
    st.write(f"**{len(items)} ca** đã tới ngày nhưng chưa xác nhận (2 tháng gần nhất). Ca chưa xác nhận **không** được tính công.")
    df = pd.DataFrame([{"Chọn": True, "Ngày": f"{wd(p['session_date'])} {p['session_date']:%d/%m}",
                        "Giờ": f"{hm(p['start_time'])}–{hm(p['end_time'])}", "Cơ sở": p.get("location_name") or "",
                        "Học viên dự kiến": ", ".join(e["name"] for e in p["expected_students"]),
                        "Số HV": len(p["expected_students"]), "id": str(p["id"])} for p in items])
    if not can_write():
        st.dataframe(df.drop(columns=["Chọn", "id"]), hide_index=True, width="stretch")
        return
    ed = st.data_editor(df, hide_index=True, width="stretch", disabled=["Ngày", "Giờ", "Cơ sở", "Học viên dự kiến", "Số HV"],
                        column_config={"id": None, "Chọn": st.column_config.CheckboxColumn(width="small")}, key="pend_ed")
    chosen = ed[ed["Chọn"]]
    no_hv = int((chosen["Số HV"] == 0).sum())
    st.caption(f"Đã chọn {len(chosen)} ca" + (f" — {no_hv} ca chưa có học viên dự kiến sẽ vào **Cần kiểm tra** (chưa tính công)."
                                              if no_hv else "."))
    if st.button(f"Xem trước xác nhận {len(chosen)} ca", disabled=chosen.empty, key="pend_prev"):
        st.session_state["pend_preview"] = list(chosen["id"])
    ids = st.session_state.get("pend_preview")
    if ids:
        with st.container(border=True):
            st.markdown("**Xem trước** — các ca sau sẽ được xác nhận **theo đúng danh sách học viên dự kiến**:")
            st.dataframe(df[df["id"].isin(ids)].drop(columns=["Chọn", "id"]), hide_index=True, width="stretch")
            c1, c2 = st.columns(2)
            if c1.button(f"Xác nhận {len(ids)} ca", type="primary", key="pend_go"):
                st.session_state.pop("pend_preview", None)
                act(lambda c: schedule.confirm_as_planned(c, owner(), uid(), ids), key="pend_go", payload=tuple(ids),
                    reason="xác nhận nhiều ca theo kế hoạch", ok=f"Đã xác nhận {len(ids)} ca.")
            if c2.button("Hủy", key="pend_cancel"):
                st.session_state.pop("pend_preview", None)
                st.rerun()
    with st.expander("Xác nhận từng ca (sửa danh sách học viên)"):
        students, locs = _students(), _locs()
        for p in items[:30]:
            session_card(p, students, locs, show_date=True)


def view_add():
    if not can_write():
        st.info("Bạn đang xem dữ liệu chỉ xem.")
        return
    st.caption("Ca phát sinh không làm thay đổi lịch cố định. Tick “Đã dạy” để xác nhận luôn; nếu không, ca nằm ở trạng thái Dự kiến.")
    students, locs = _students(), _locs()
    d = st.date_input("Ngày", today(), key="add_d", format="DD/MM/YYYY")
    s, e = _times("add", time(17), time(18))
    li = _loc_select("add_l", locs)
    k = st.selectbox("Loại ca", kinds_active(), format_func=kind_label, key="add_k")
    att = attendee_input("add", students, [])
    notes = st.text_input("Ghi chú (tên bé dạy thử, ca ghép…)", key="add_n")
    ov = _override("add")
    done = st.checkbox("Tôi đã dạy ca này — xác nhận luôn", value=d <= today(), key="add_done")
    if st.button("Lưu ca", type="primary", key="add_go", disabled=not li):
        if done:
            act(lambda c: schedule.add_and_confirm_extra(c, owner(), uid(), day=d, start=s, end=e, location_id=li, session_kind=k,
                                                         attendees=att, notes=notes or None, override_reason=ov),
                key="add", payload=(d, s, e, li, k, repr(att)), reason="thêm ca phát sinh", ok="Đã thêm và xác nhận ca.")
        else:
            act(lambda c: schedule.add_one_off(c, owner(), day=d, start=s, end=e, location_id=li, session_kind=k, expected=att,
                                               notes=notes or None, override_reason=ov),
                key="add", payload=(d, s, e, li, k, repr(att)), reason="thêm ca ngoài lịch",
                ok="Đã thêm ca vào lịch (chưa tính công cho tới khi xác nhận).")


def view_templates():
    tpls = read(lambda c: schedule.templates(c, owner()), [])
    t = today()
    active = [x for x in tpls if not x["valid_to"] or x["valid_to"] >= t]
    st.caption("Lịch cố định tự sinh ca dự kiến trên lịch. Đổi cả chuỗi chỉ áp dụng từ ngày chọn — các ca trước đó, ca đã sửa riêng "
               "và ca đã xác nhận được giữ nguyên.")
    if active:
        df = pd.DataFrame([{"Thứ": WEEK[x["weekday"]], "Giờ": f"{hm(x['start_time'])}–{hm(x['end_time'])}",
                            "Cơ sở": x["location_name"], "Loại": kind_label(x["session_kind"]),
                            "Học viên": ", ".join(e["name"] for e in x["expected_students"]),
                            "Từ": x["valid_from"], "Đến": x["valid_to"]} for x in active])
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Từ": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                    "Đến": st.column_config.DateColumn(format="DD/MM/YYYY")})
    else:
        st.info("Chưa có lịch cố định.")
    if not can_write():
        return
    students, locs = _students(), _locs()
    with st.expander("➕ Thêm lịch cố định", expanded=not active):
        days = st.pills("Thứ trong tuần", list(range(7)), selection_mode="multi", format_func=WEEK.get, key="tp_days")
        s, e = _times("tp", time(17), time(18))
        c1, c2 = st.columns(2)
        vf = c1.date_input("Áp dụng từ", t, key="tp_vf", format="DD/MM/YYYY")
        vt = c2.date_input("Đến ngày (không bắt buộc)", None, key="tp_vt", format="DD/MM/YYYY")
        li = _loc_select("tp_l", locs)
        k = st.selectbox("Loại ca", kinds_active(), format_func=kind_label, key="tp_k")
        exp = attendee_input("tp", students, [])
        ov = _override("tp")
        if st.button("Tạo lịch", type="primary", key="tp_go", disabled=not li):
            if not days:
                st.error("Chọn ít nhất một thứ.")
            else:
                def _mk(c):
                    for w in days:
                        schedule.create_template(c, owner(), weekday=w, start=s, end=e, valid_from=vf, valid_to=vt, location_id=li,
                                                 session_kind=k, expected=exp, override_reason=ov)
                act(_mk, key="tp", payload=(tuple(days), s, e, vf, li, repr(exp)), reason="tạo lịch cố định",
                    ok=f"Đã tạo {len(days)} lịch cố định. Ca dự kiến tự hiện trên lịch.")
    for x in active:
        tid = str(x["id"])
        with st.expander(f"✏️ {WEEK[x['weekday']]} {hm(x['start_time'])}–{hm(x['end_time'])} · {x['location_name']}"):
            mode = st.radio("Thao tác", ["Đổi cả chuỗi từ một ngày", "Kết thúc chuỗi"], key=f"tm_{tid}", horizontal=True)
            if mode == "Đổi cả chuỗi từ một ngày":
                ef = st.date_input("Áp dụng từ ngày", t, key=f"tc_ef_{tid}", format="DD/MM/YYYY")
                w = st.selectbox("Thứ", list(range(7)), index=x["weekday"], format_func=WEEK.get, key=f"tc_w_{tid}")
                s2, e2 = _times(f"tc_{tid}", x["start_time"], x["end_time"])
                l2 = _loc_select(f"tc_l_{tid}", locs, x["location_id"])
                if st.button("Áp dụng thay đổi", type="primary", key=f"tc_go_{tid}"):
                    act(lambda c: schedule.change_series(c, owner(), tid, ef, start=s2, end=e2, location_id=l2, weekday=w),
                        key=f"tc_{tid}", payload=(tid, ef, w, s2, e2, l2), reason="đổi cả chuỗi lịch",
                        ok="Đã đổi lịch từ ngày hiệu lực; các ca trước đó giữ nguyên.")
            else:
                last = st.date_input("Ngày dạy cuối cùng", t, key=f"te_{tid}", format="DD/MM/YYYY")
                if st.button("Kết thúc chuỗi", key=f"te_go_{tid}"):
                    act(lambda c: schedule.end_series(c, tid, last), key=f"te_{tid}", payload=(tid, last),
                        reason="kết thúc lịch cố định", ok="Đã đặt ngày kết thúc.")
    with st.expander("Ngoại lệ gần đây (nghỉ / dời / bù / sửa riêng, ±60 ngày)"):
        ex = read(lambda c: _exceptions(c), [])
        if ex:
            st.dataframe(pd.DataFrame(ex), hide_index=True, width="stretch")
        else:
            st.caption("Không có ngoại lệ.")


WEEK = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ nhật"}


def _exceptions(c):
    from ..db import rows
    t = today()
    out = []
    lab = cancel_labels()
    for p in rows(c, """SELECT p.*, l.name AS location_name FROM planned_sessions p LEFT JOIN locations l ON l.id = p.location_id
                        WHERE p.owner_id = :o AND p.session_date BETWEEN :a AND :b
                        AND (p.status IN ('cancelled','rescheduled') OR p.is_exception OR p.template_id IS NULL)
                        ORDER BY p.session_date DESC LIMIT 80""", o=owner(), a=t - timedelta(days=60), b=t + timedelta(days=60)):
        kind = ("Nghỉ: " + lab.get(p["cancel_reason"], "") if p["status"] == "cancelled" else "Đã dời đi" if p["status"] == "rescheduled"
                else "Dạy bù" if p["makeup_for_id"] else "Ca dời tới" if p["rescheduled_from_id"]
                else "Ca ngoài lịch" if not p["template_id"] else "Sửa riêng")
        out.append({"Ngày": f"{p['session_date']:%d/%m/%Y}", "Giờ": f"{hm(p['start_time'])}–{hm(p['end_time'])}",
                    "Cơ sở": p["location_name"], "Loại": kind, "Ghi chú": p.get("cancel_note") or ""})
    return out


def render():
    st.title("Lịch dạy")
    help_box("""
- **Hôm nay / Tuần / Tháng:** xem ca và thao tác ngay trên từng ca: **✓ Đã dạy** (xác nhận theo danh sách dự kiến) hoặc **Điều chỉnh** (sửa danh sách học viên có mặt, báo nghỉ, đổi giờ/ngày).
- **Chờ xác nhận:** xác nhận nhiều ca một lúc, có bước xem trước.
- Chỉ ca **đã xác nhận** mới được tính công. Số học viên luôn tự đếm theo danh sách tên.
- Ca trùng giờ sẽ bị cảnh báo; nếu đúng là trùng, ghi lý do để lưu.""")
    view = seg(VIEWS, "lich_view", "Hôm nay")
    {"Hôm nay": view_day, "Tuần": view_week, "Tháng": view_month, "Chờ xác nhận": view_pending, "Thêm ca": view_add,
     "Lịch cố định": view_templates}[view]()
