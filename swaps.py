# -*- coding: utf-8 -*-
"""
دوال التبادل الودي النظيفة.

Phase 3I-a-1: نقل دوال العرض/التحليل النصي النظيفة الخاصة بالتبادل
من app.py إلى swaps.py دون أي اعتماد على Gradio أو app.py.
"""

import urllib.parse

from storage import (
    state_locked,
    teachers_db,
    swap_db,
    save_swap_db,
    get_now_oman,
    write_audit_log,
)


def build_swap_button_html(candidate_name, message_text):
    phone = teachers_db.get(candidate_name, {}).get("phone", "")
    btn_color = "#25D366"

    if phone:
        phone = "".join(filter(str.isdigit, str(phone)))
        if len(phone) == 8:
            phone = "968" + phone
        btn_text = f"✅ إرسال للأستاذ {candidate_name}"
    else:
        phone = ""
        btn_text = f"⚠️ إرسال (لا يوجد رقم)"

    encoded_msg = urllib.parse.quote(message_text)
    wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}" if phone else f"https://api.whatsapp.com/send?text={encoded_msg}"

    return (
        f'<div style="margin-top: 10px; border: 2px solid {btn_color}; border-radius: 8px; padding: 2px;">'
        f'<a href="{wa_link}" target="_blank" '
        f'style="display: block; width: 100%; text-align: center; background-color: {btn_color}; color: white; '
        f'padding: 12px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 16px;">'
        f'{btn_text}</a></div>'
    )


def extract_swap_choice_details(choice):
    candidate = ""
    comp_day = "يحدد لاحقاً"
    comp_period = "يحدد لاحقاً"

    try:
        parts = choice.split("|", 2)

        if len(parts) > 1 and ":" in parts[1]:
            candidate = parts[1].split(":", 1)[1].strip()
        else:
            candidate = str(choice).strip()

        details = parts[2].strip() if len(parts) > 2 else ""

        if "وتغطيه " in details:
            rep_part = details.split("وتغطيه ", 1)[1].split(")", 1)[0].replace("(", "")
            rep_day, rep_period = rep_part.split(" ح", 1)
            comp_day = rep_day.strip()
            comp_period = f"الحصة {rep_period.strip()}"

    except Exception:
        candidate = str(choice).strip()

    return candidate, comp_day, comp_period


def render_swap_table_html(state):
    if not isinstance(state, dict) or not state:
        return """
        <div style='background:#f8fafc; border:1px dashed #cbd5e1; border-radius:10px; padding:14px; text-align:center; color:#64748b; direction:rtl;'>
            لا توجد تبادلات معتمدة بعد.
        </div>
        """

    rows_html = ""
    for p, info in sorted(state.items(), key=lambda x: int(x[0])):
        rows_html += f"""
        <tr>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('requester', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('class', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>الحصة {p}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('candidate', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('comp_day', 'يحدد لاحقاً')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('comp_period', 'يحدد لاحقاً')}</td>
        </tr>
        """

    return f"""
    <div style='overflow-x:auto; direction:rtl; margin-top:12px;'>
        <table style='width:100%; min-width:900px; border-collapse:collapse; text-align:center; font-family:Cairo, Arial, sans-serif;'>
            <thead>
                <tr style='background:#e8f5e9; color:#0f5132;'>
                    <th style='padding:12px; border:1px solid #d1d5db;'>المعلم الطالب للتبادل</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>الصف</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>الحصة</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>المعلم البديل</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>يوم التعويض</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>حصة التعويض</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """

def extract_clean_period_number(period_value):
    raw = str(period_value).split("-")[0]
    raw = raw.replace("✅", "").replace("الحصة", "").strip()
    return raw if raw.isdigit() else ""


def format_elegant_class(raw_class):
    raw_class = str(raw_class).strip()
    if not raw_class:
        return "الصف غير محدد"
    words = raw_class.split()
    if len(words) < 2:
        return raw_class
    grade_part = ""
    subject_part = ""
    for i, word in enumerate(reversed(words)):
        if any(g in word for g in ["ثامن", "تاسع", "عاشر", "حادي", "ثاني", "1", "2", "3", "4", "5", "6", "7", "8", "9"]):
            grade_part = word
            subject_part = " ".join(words[:len(words) - 1 - i])
            break
    if grade_part and subject_part:
        return f"{grade_part} - مادة {subject_part}"
    return raw_class


@state_locked
def confirm_swap_core(t, period_value, choice, d, msg_text, state, actor_name="", actor_role=""):
    """يعتمد تبادلًا وديًا ويُرجع الحالة الحالية ورسالة تحذير خام إن وجدت."""
    t = str(t or "").split(" (")[0].strip()
    current_state = dict(state) if isinstance(state, dict) else {}

    if not t or not period_value or not choice or "❌" in str(choice):
        return current_state, ""

    p_clean = extract_clean_period_number(period_value)

    req_class_raw = teachers_db.get(t, {}).get(
        d, {}
    ).get(
        p_clean,
        teachers_db.get(t, {}).get(d, {}).get(int(p_clean) if p_clean.isdigit() else p_clean, "")
    )

    elegant_class = format_elegant_class(req_class_raw)
    candidate, comp_day, comp_period = extract_swap_choice_details(choice)

    # ── فحص محلي (داخل الحالة الحالية للمعلم) ──
    for p_ex, info_ex in current_state.items():
        if (
            info_ex.get("comp_day") == comp_day
            and info_ex.get("comp_period") == comp_period
            and p_ex != p_clean
        ):
            return (
                current_state,
                f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً لهذا المعلم.</div>"
            )

    # ── فحص عالمي (على جميع التبادلات المعتمدة) ──
    current_key = f"{t}|{d}|{p_clean}"
    for key, info in swap_db.items():
        same_comp = (
            info.get("comp_day") == comp_day
            and info.get("comp_period") == comp_period
        )
        if not same_comp:
            continue

        if info.get("requester") == t and key != current_key:
            return (
                current_state,
                f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً لهذا المعلم.</div>"
            )

        if info.get("candidate") == candidate and key != current_key:
            return (
                current_state,
                f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً على المعلم البديل.</div>"
            )

    current_state[p_clean] = {
        "requester": t,
        "class": elegant_class,
        "candidate": candidate,
        "choice": choice,
        "message": msg_text,
        "comp_day": comp_day,
        "comp_period": comp_period,
    }

    swap_db[current_key] = {
        "requester": t,
        "day": d,
        "period": p_clean,
        "class": elegant_class,
        "candidate": candidate,
        "choice": choice,
        "message": msg_text,
        "comp_day": comp_day,
        "comp_period": comp_period,
        "updated_at": get_now_oman().strftime("%Y-%m-%d %H:%M"),
    }
    save_swap_db()

    write_audit_log(
        "اعتماد تبادل ودي",
        target_teacher=t,
        old_value="",
        new_value={
            "day": d,
            "period": p_clean,
            "class": elegant_class,
            "candidate": candidate,
            "comp_day": comp_day,
            "comp_period": comp_period,
        },
        details=f"اعتماد تبادل ودي بين {t} و {candidate}",
        actor_name=actor_name,
        actor_role=actor_role,
    )

    return current_state, ""

