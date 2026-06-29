# -*- coding: utf-8 -*-
"""
دوال التبادل الودي النظيفة.

Phase 3I-a-1: نقل دوال العرض/التحليل النصي النظيفة الخاصة بالتبادل
من app.py إلى swaps.py دون أي اعتماد على Gradio أو app.py.
"""

import re
import urllib.parse

from schedules import get_teacher_choices

from storage import (
    state_locked,
    teachers_db,
    swap_db,
    save_swap_db,
    get_now_oman,
    write_audit_log,
    SCHOOL_WEEK_DAYS,
)


SWAP_EMPTY_MSG = "💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا..."

def get_current_day_oman():
    weekday = get_now_oman().weekday()
    days_map = {6: "الأحد", 0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الأحد", 5: "الأحد"}
    return days_map.get(weekday, "الأحد")

def get_class_dna(class_string):
    s = str(class_string).strip()
    s = s.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')) 
    s = s.replace("ـ", "") 
    if not s: return ""
    
    nums = re.findall(r'\d+', s)
    section = nums[-1] if nums else ""
    
    grade = ""
    if any(x in s for x in ["عاشر", "10", "١٠"]): grade = "10"
    elif any(x in s for x in ["تاسع", "9", "٩"]): grade = "9"
    elif any(x in s for x in ["ثامن", "8", "٨"]): grade = "8"
    elif any(x in s for x in ["سابع", "7", "٧"]): grade = "7"
    elif any(x in s for x in ["حادي", "11", "١١"]): grade = "11"
    elif any(x in s for x in ["ثاني", "12", "١٢"]): grade = "12"
    
    if grade and section: return f"G{grade}-{section}"
    return re.sub(r'[^\w\dأ-ي]', '', s) 

def check_teacher_load(teacher_name, day_name, period_to_add):
    try:
        if teacher_name not in teachers_db: return ""
        info = teachers_db[teacher_name]
        base_p = {int(k) for k in info.get(day_name, {}).keys() if str(k).isdigit()}
        
        if str(period_to_add).isdigit():
            all_slots = sorted(list(base_p | {int(period_to_add)}))
        else:
            all_slots = sorted(list(base_p))
            
        consecutive = max_con = 1
        for i in range(len(all_slots)-1):
            if all_slots[i+1] == all_slots[i] + 1:
                consecutive += 1
                max_con = max(max_con, consecutive)  # ← داخل الحلقة
            else:
                consecutive = 1
            
        warns = []
        if max_con >= 3: warns.append("⚠️ إجهاد بدني")
        if len(all_slots) >= 6: warns.append("⚠️ كثافة عالية")
        return " | ".join(warns)
    except Exception:
        return ""

def run_radar_safe_core(t, p, d):
    """يرجع قائمة المرشحين للتبادل الودي دون أي اعتماد على Gradio."""
    t = str(t or "").split(" (")[0].strip()
    try:
        if not t or not p or "لا يوجد" in t or "اختر" in p:
            return []

        p_str_clean = extract_clean_period_number(p)
        if not p_str_clean.isdigit():
            return []
        p_int = int(p_str_clean)

        t_cls = teachers_db.get(t, {}).get(
            d, {}
        ).get(
            str(p_int),
            teachers_db.get(t, {}).get(d, {}).get(p_int, "")
        )
        if not t_cls:
            return ["❌ لا توجد حصة مسجلة لك"]

        dna = get_class_dna(t_cls)
        perf, flex = [], []

        day_weights = {"الأحد": 1, "الإثنين": 2, "الثلاثاء": 3, "الأربعاء": 4, "الخميس": 5}
        current_day_str = get_current_day_oman()
        current_weight = day_weights.get(current_day_str, 1)

        for tb, info in teachers_db.items():
            if tb == t or info.get("dept") == "الهيئة الإدارية" or info.get("role") == "إداري":
                continue
            if str(p_int) in info.get(d, {}) or p_int in info.get(d, {}):
                continue

            for db in SCHOOL_WEEK_DAYS:
                db_weight = day_weights.get(db, 1)
                db_display = f"{db} القادم" if db_weight < current_weight else db

                for pb, cb in info.get(db, {}).items():
                    if dna == get_class_dna(cb) and dna != "":
                        w_b = check_teacher_load(tb, d, p_int)
                        is_t_free = True
                        if str(pb) in teachers_db.get(t, {}).get(db, {}):
                            is_t_free = False
                        elif str(pb).isdigit() and int(str(pb)) in teachers_db.get(t, {}).get(db, {}):
                            is_t_free = False

                        if is_t_free:
                            w_a = check_teacher_load(t, db, pb)
                            warns = []
                            if w_b:
                                warns.append(f"إجهاد لـ {tb}: {w_b}")
                            if w_a:
                                warns.append(f"إجهاد لك: {w_a}")
                            w_str = f" ⚠️ ({' | '.join(warns)})" if warns else ""
                            perf.append(f"🟢 تبادل مثالي | البديل: {tb} | يغطيك ({d} ح{p_int}) وتغطيه ({db_display} ح{pb}){w_str}")
                        else:
                            w_str = f" ⚠️ (إجهاد لـ {tb}: {w_b})" if w_b else ""
                            flex.append(f"🟠 إنقاذ مرن | البديل: {tb} | يغطيك ({d} ح{p_int}) لكنك مشغول وقت حصته ({db_display} ح{pb}){w_str}")

        res = sorted(list(set(perf))) + sorted(list(set(flex)))
        if not res:
            return [f"❌ لا يوجد بديل متفرغ (بصمة: {dna})"]
        return res
    except Exception:
        return ["خطأ داخلي"]


def generate_wa_msg_core(choice, t_req, p_req, d_req):
    """يبني مسودة رسالة واتساب وزرها كقيم خام دون أي اعتماد على Gradio."""
    if not choice or "❌" in str(choice) or "خطأ" in str(choice):
        return SWAP_EMPTY_MSG, ""
    try:
        parts = str(choice).split("|")
        t_target = parts[1].split(":")[1].strip()
        details = parts[2].strip()

        p_req_clean = extract_clean_period_number(p_req)
        t_req_clean = str(t_req or "").split(" (")[0].strip()
        req_class_raw = teachers_db.get(t_req_clean, {}).get(
            d_req, {}
        ).get(
            p_req_clean,
            teachers_db.get(t_req_clean, {}).get(
                d_req, {}
            ).get(int(p_req_clean) if p_req_clean.isdigit() else p_req_clean, "")
        )
        req_class_elegant = format_elegant_class(req_class_raw)

        msg = f"السلام عليكم ورحمة الله وبركاته أستاذي العزيز ({t_target})\n\n"
        msg += f"يرغب الأستاذ ({t_req}) بالتبادل الودي معك (بعد إذنك وموافقتك طبعاً لظرف طارئ).\n"
        msg += f"ستقوم أنت مشكوراً بتغطية الصف ({req_class_elegant}) في الحصة ({p_req_clean}) يوم ({d_req}).\n"

        if "مثالي" in str(choice):
            rep_part = details.split("وتغطيه ")[1].split(")")[0].replace("(", "")
            rep_day, rep_period = rep_part.split(" ح")

            clean_rep_day = rep_day.replace(" القادم", "").strip()
            target_class_raw = teachers_db.get(t_target, {}).get(
                clean_rep_day, {}
            ).get(
                str(rep_period),
                teachers_db.get(t_target, {}).get(
                    clean_rep_day, {}
                ).get(int(rep_period) if str(rep_period).isdigit() else rep_period, "")
            )
            target_class_elegant = format_elegant_class(target_class_raw)

            msg += f"وسيقوم الأستاذ ({t_req}) بتغطية الصف ({target_class_elegant}) في الحصة ({rep_period}) يوم ({rep_day}) بدلاً عنك.\n\n"
        else:
            msg += f"ونظراً لانشغال الأستاذ ({t_req}) وقت حصتك، سيتم التنسيق لرد الحصة لاحقاً.\n\n"

        msg += "هل يناسبك هذا التبادل ليتم اعتماده؟ شاكرين ومقدرين تعاونك 🤝"

        phone = teachers_db.get(t_target, {}).get("phone", "")
        btn_color = "#25D366"

        if phone:
            phone = "".join(filter(str.isdigit, str(phone)))
            if len(phone) == 8:
                phone = "968" + phone
            btn_text = f"✅ إرسال للأستاذ {t_target}"
        else:
            phone = ""
            btn_text = "⚠️ إرسال (لا يوجد رقم)"

        encoded_msg = urllib.parse.quote(msg)
        wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}"

        btn_html = (
            f'<div style="margin-top: 10px; border: 2px solid {btn_color}; border-radius: 8px; padding: 2px;">'
            f'<a href="{wa_link}" target="_blank" '
            f'style="display: block; width: 100%; text-align: center; background-color: {btn_color}; color: white; '
            f'padding: 12px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 16px;">'
            f'{btn_text}</a></div>'
        )

        return msg, btn_html

    except Exception:
        return SWAP_EMPTY_MSG, ""


def on_swap_option_selected_core(choice, t, period_value, d):
    """يرجع: (msg_value, btn_value, is_interactive) دون أي اعتماد على Gradio."""
    if not choice or "❌" in str(choice):
        return SWAP_EMPTY_MSG, "", False

    msg_value, btn_value = generate_wa_msg_core(choice, t, period_value, d)
    return msg_value, btn_value, True


def get_swap_candidates_for_period_core(t, period_value, d, confirmed_state):
    """يرجع بيانات مرشحي التبادل الخام لحصة محددة دون أي اعتماد على Gradio."""
    empty_msg = SWAP_EMPTY_MSG

    if not t or not period_value:
        return [], None, empty_msg, "", False

    candidates = run_radar_safe_core(t, period_value, d)

    if not candidates:
        candidates = ["❌ لا يوجد بديل متفرغ"]

    p_clean = extract_clean_period_number(period_value)

    saved_choice = None
    saved_message = empty_msg
    btn_value = ""
    confirm_interactive = False

    if isinstance(confirmed_state, dict) and p_clean in confirmed_state:
        saved_choice = confirmed_state[p_clean].get("choice")
        if saved_choice not in candidates:
            saved_choice = None

        if saved_choice:
            saved_message = confirmed_state[p_clean].get("message", empty_msg) or empty_msg
            _, btn_value = generate_wa_msg_core(saved_choice, t, period_value, d)
            confirm_interactive = True

    return candidates, saved_choice, saved_message, btn_value, confirm_interactive

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


def load_confirmed_swaps_for_context_core(t, d):
    """يرجع حالة التبادلات المعتمدة للمعلم/اليوم دون أي اعتماد على Gradio."""
    t = str(t or "").split(" (")[0].strip()
    state = {}

    if not t or not d:
        return state

    for _, info in swap_db.items():
        if info.get("requester") == t and info.get("day") == d:
            p = str(info.get("period", "")).strip()
            if not p:
                continue

            state[p] = {
                "requester": info.get("requester", ""),
                "class": info.get("class", ""),
                "candidate": info.get("candidate", ""),
                "choice": info.get("choice", ""),
                "message": info.get("message", ""),
                "comp_day": info.get("comp_day", "يحدد لاحقاً"),
                "comp_period": info.get("comp_period", "يحدد لاحقاً"),
            }

    return state


def clear_swap_detail_ui_core():
    """تُرجع: (choices, selected_value, message_value, button_html, confirm_interactive)."""
    return [], None, SWAP_EMPTY_MSG, "", False



def filter_swap_teachers_safe_core(dept):
    """يرجع اختيارات معلمي التبادل كقيم خام دون أي اعتماد على Gradio."""
    try:
        choices = get_teacher_choices(dept if dept != "الكل" else "الكل")
        if not choices:
            return ["لا يوجد معلمون"], None
        return choices, None
    except Exception:
        return [], None


def get_teacher_periods_safe_core(t, d):
    """يرجع حصص المعلم كقيم خام دون أي اعتماد على Gradio."""
    try:
        if t and t in teachers_db and t != "لا يوجد معلمون":
            periods_elegant = []
            for k, v in teachers_db[t].get(d, {}).items():
                if str(k).isdigit() and str(v).strip() != "" and str(v).lower() != "nan":
                    elegant_c = format_elegant_class(v)
                    display_text = f"الحصة {k} - ({elegant_c})"
                    periods_elegant.append(display_text)
            periods_elegant.sort(key=lambda x: int(x.split("-")[0].replace("الحصة", "").strip()))
            if not periods_elegant:
                return ["لا توجد حصص"], None
            return periods_elegant, None
        return ["اختر معلماً أولاً"], None
    except Exception:
        return ["خطأ داخلي"], None

def get_teacher_periods_marked_core(t, d, confirmed_state, current_value=None):
    """يرجع اختيارات حصص المعلم مع تعليم الحصص المعتمدة دون أي اعتماد على Gradio."""
    t = str(t or "").split(" (")[0].strip()
    try:
        if not t or t not in teachers_db or t == "لا يوجد معلمون":
            return ["اختر معلماً أولاً"], None

        confirmed_keys = set()
        if isinstance(confirmed_state, dict):
            confirmed_keys = {str(k) for k in confirmed_state.keys()}

        choices = []
        selected_value = None
        current_clean = extract_clean_period_number(current_value)

        for k, v in teachers_db[t].get(d, {}).items():
            if str(k).isdigit() and str(v).strip() != "" and str(v).lower() != "nan":
                elegant_c = format_elegant_class(v)
                prefix = "✅ " if str(k) in confirmed_keys else ""
                display_text = f"{prefix}الحصة {k} - ({elegant_c})"
                choices.append((int(k), display_text))

        choices.sort(key=lambda x: x[0])
        final_choices = [text for _, text in choices]

        if current_clean:
            for k, text in choices:
                if str(k) == current_clean:
                    selected_value = text
                    break

        if not final_choices:
            return ["لا توجد حصص"], None

        return final_choices, selected_value

    except Exception:
        return ["خطأ داخلي"], None


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

