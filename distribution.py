# -*- coding: utf-8 -*-
"""
distribution.py

Phase 3J-a1: دوال مساعدة نظيفة منخفضة المخاطر من عنقود التوزيع والاحتياط.
هذا الملف لا يعتمد على Gradio ولا يستورد app.py.
"""

import urllib.parse

from config import ADMIN_ROLES
from storage import teachers_db, daily_db, SCHOOL_WEEK_DAYS
from schedules import resolve_effective_dept, format_teacher_name
from exemptions import is_teacher_exempt_for_slot
from swaps import get_date_of_weekday, get_current_day_oman, get_class_dna, check_teacher_load, format_elegant_class


def resolve_teacher_display_value(raw_name, choices):
    if not raw_name:
        return None
    if raw_name in choices:
        return raw_name
    for c in choices:
        if str(c).startswith(str(raw_name) + " ("):
            return c
    return None

def resolve_teacher_display_values(raw_names, choices):
    if not raw_names:
        return []
    out = []
    for name in raw_names:
        resolved = resolve_teacher_display_value(name, choices)
        if resolved:
            out.append(resolved)
    return out

def get_teacher_schedule_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    choices = []
    for t, d in sorted(teachers_db.items(), key=lambda item: item[0]):
        dept = str(d.get("dept", "")).strip()
        role = str(d.get("role", "معلم")).strip() or "معلم"
        if dept == "الهيئة الإدارية":
            continue
        if role in ADMIN_ROLES:
            continue
        if dept_filter != "الكل" and dept != dept_filter:
            continue
        choices.append(f"{t} ({role})" if role != "معلم" else t)
    return choices

def get_falcon_eye_candidates(absent_t, period, day_name):
    try:
        if not absent_t or not period: return []
        
        p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
        if not p_str_clean.isdigit(): return [] 
        p_int = int(p_str_clean)
        
        target_class = teachers_db.get(absent_t, {}).get(day_name, {}).get(str(p_int), "")
        if not target_class: target_class = teachers_db.get(absent_t, {}).get(day_name, {}).get(p_int, "")
        if not target_class: return []
        
        target_fingerprint = get_class_dna(target_class)
        candidates = []
        
        for name, info in teachers_db.items():
            if name == absent_t: continue
            if is_teacher_exempt_for_slot(name, day_name, p_int): continue
            if str(p_int) in info.get(day_name, {}) or p_int in info.get(day_name, {}): continue
            
            teaches_same = False
            for d in SCHOOL_WEEK_DAYS:
                for c in info.get(d, {}).values():
                    if target_fingerprint == get_class_dna(c) and target_fingerprint != "": teaches_same = True
                    
            if teaches_same:
                warn = check_teacher_load(name, day_name, p_int)
                warn_str = f" {warn}" if warn else ""
                candidates.append(f"🦅 {name} (يدرس نفس الصف){warn_str}")
        return candidates
    except Exception as e:
        return []

def get_dynamic_header(day_name):
    target_date = get_date_of_weekday(day_name)
    return f"<div style='background:#004d40; padding:15px; border-radius:10px; text-align:center;'><div style='font-size:1.4em; font-weight:bold; color:#ffffff !important;'>📅 {day_name} | {target_date}</div></div>"

def get_initial_header(): return get_dynamic_header(get_current_day_oman())

def format_sub_display(row):
    sub = str(row.get("المعلم البديل", ""))
    status = str(row.get("حالة_التكليف", ""))
    name_fmt = format_teacher_name(sub) if sub != "إشراف إداري" else sub
    if status == "تبادل": return f"{name_fmt} (تبادل 🤝)"
    elif status == "تقصير": return f"{name_fmt} (لم يُنفذ التكليف ❌)"
    return name_fmt

def format_sub_display_for_image(row):
    """تنسيق نص المعلم البديل للصورة فقط دون رمز المصافحة."""
    display_text = format_sub_display(row)
    swap_emoji = chr(0x1F91D)
    display_text = display_text.replace(f" {swap_emoji}", "").replace(swap_emoji, "")
    return display_text

def get_empty_generation_state():
    return {
        "day": "",
        "dept": "",
        "absents": [],
        "signature": "",
        "generated": False,
        "has_results": False,
    }

def normalize_absent_names(absent_list):
    if not absent_list:
        return []

    if isinstance(absent_list, str):
        absent_list = [absent_list]

    cleaned = []
    for name in absent_list:
        raw = str(name).split(" (")[0].strip()
        if raw and raw not in cleaned:
            cleaned.append(raw)

    return sorted(cleaned)

def build_generation_signature(absent_list, day_name, dept_filter):
    cleaned = normalize_absent_names(absent_list)
    return f"{day_name}||{dept_filter}||{'|'.join(cleaned)}"

def same_generation_context(generation_state, day_name, dept_filter):
    return (
        isinstance(generation_state, dict)
        and generation_state.get("day") == day_name
        and generation_state.get("dept") == dept_filter
    )

def get_existing_absents_for_context(day_name, dept_filter):
    target_date = get_date_of_weekday(day_name)
    found = []

    for row in daily_db:
        if row.get("date") == target_date and (dept_filter == "الكل" or row.get("dept") == dept_filter):
            name = str(row.get("المعلم الغائب", "")).strip()
            if name and name not in found:
                found.append(name)

    return sorted(found)

def build_absence_conflict_warning_html(conflicts, day_name):
    if not conflicts:
        return ""

    if len(conflicts) == 1:
        conflict = conflicts[0]
        formatted_name = format_teacher_name(conflict["name"])
        subject_text = f"المعلم الغائب <b>({formatted_name})</b> لديه حصص احتياط مسندة إليه مسبقًا في يوم <b>({day_name})</b>."
    else:
        names_text = "، ".join([format_teacher_name(item["name"]) for item in conflicts])
        subject_text = f"المعلمون الغائبون <b>({names_text})</b> لديهم حصص احتياط مسندة إليهم مسبقًا في يوم <b>({day_name})</b>."

    return (
        "<div style='background:#eaf4ff; color:#0f3d91; padding:15px; border-radius:10px; "
        "border:2px solid #64b5f6; text-align:center; font-weight:bold; font-size:15px; margin-top:12px; margin-bottom:15px;'>"
        "ℹ️ تنبيه تعارض في الاحتياط<br>"
        f"{subject_text}<br>"
        "لضمان صحة جميع الإسنادات، استخدم <b>\"لوحة القيادة\"</b> أو <b>\"إعادة توليد من جديد\"</b>."
        "</div>"
    )

def detect_conflicted_absence_slots(display_records):
    absent_names = {
        str(r.get("المعلم الغائب", "")).split(" (")[0].strip()
        for r in display_records
        if str(r.get("المعلم الغائب", "")).strip()
    }

    conflicted_teachers = set()
    conflicted_slots = set()

    for row in display_records:
        sub_name = str(row.get("المعلم البديل", "")).split(" (")[0].strip()
        abs_name = str(row.get("المعلم الغائب", "")).split(" (")[0].strip()
        period_val = str(row.get("الحصة", "")).strip()

        if not sub_name or sub_name == "إشراف إداري":
            continue
        if row.get("حالة_التكليف") == "تقصير":
            continue

        if sub_name in absent_names:
            conflicted_teachers.add(abs_name)
            conflicted_slots.add((abs_name, period_val))

    return conflicted_teachers, conflicted_slots


def detect_absence_assignment_conflicts_for_context(day_name, dept_filter, current_abs=None):
    effective_dept = resolve_effective_dept(dept_filter)
    cleaned = normalize_absent_names(current_abs) if current_abs else []
    if not cleaned:
        cleaned = get_existing_absents_for_context(day_name, effective_dept)

    if not cleaned or not day_name:
        return []

    target_date = get_date_of_weekday(day_name)
    conflict_rows = {}

    for row in daily_db:
        if row.get("date") != target_date:
            continue
        if effective_dept != "الكل" and row.get("dept") != effective_dept:
            continue
        if str(row.get("المعلم البديل", "")).strip() in ["", "إشراف إداري"]:
            continue
        if row.get("حالة_التكليف") == "تقصير":
            continue

        sub_name = str(row.get("المعلم البديل", "")).split(" (")[0].strip()
        if sub_name not in cleaned:
            continue

        conflict_rows.setdefault(sub_name, [])
        period_val = str(row.get("الحصة", "")).strip()
        if period_val and period_val not in conflict_rows[sub_name]:
            conflict_rows[sub_name].append(period_val)

    conflicts = []
    for teacher_name, periods in conflict_rows.items():
        conflicts.append({
            "name": teacher_name,
            "periods": sorted(periods, key=lambda x: int(x) if str(x).isdigit() else str(x))
        })

    conflicts.sort(key=lambda item: item["name"])
    return conflicts

def generate_styled_html_table(df):
    if df is None or df.empty: return "<div style='text-align:center; color:gray; padding:20px; border: 1px dashed #ccc; border-radius: 10px;'>لا توجد تكليفات للعرض. اختر معلماً غائباً واضغط توليد.</div>"
    html = "<div style='overflow-x: auto; margin-top: 15px;'><table style='width: 100%; border-collapse: separate; border-spacing: 0 6px; text-align: center; font-family: Cairo, Arial, sans-serif; direction: rtl; border: 1px solid #e5e7eb; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>"
    html += "<tr style='background-color: #004d40; color: white; font-size: 16px; border-bottom: 3px solid #ffca28;'><th style='padding: 15px;'>المعلم الغائب</th><th style='padding: 15px;'>الصف</th><th style='padding: 15px;'>الحصة</th><th style='padding: 15px;'>المعلم البديل</th></tr>"
    for index, row in df.iterrows():
        sub_teacher_display = str(row.get("المعلم البديل عرض", row["المعلم البديل"]))
        abs_teacher = str(row["المعلم الغائب"])
        status = row.get("حالة_التكليف", "")
        is_admin_supervision = "إشراف" in sub_teacher_display

        if status == "تقصير" or "❌" in sub_teacher_display: bg_color, text_color, border_style = "#ffebee", "#c62828", "border-top: 2px solid #ef9a9a; border-bottom: 2px solid #ef9a9a;"
        elif status == "تبادل" or "🤝" in sub_teacher_display: bg_color, text_color, border_style = "#e0f2f1", "#00695c", "border-top: 2px solid #80cbc4; border-bottom: 2px solid #80cbc4;"
        elif is_admin_supervision: bg_color, text_color, border_style = "#fee2e2", "#991b1b", "border-top: 4px solid #ef4444; border-bottom: 4px solid #ef4444;"
        else: bg_color, text_color, border_style = "#f1f8e9" if index % 2 == 0 else "#ffffff", "#333333", "border-bottom: 1px solid #e5e7eb;"

        if is_admin_supervision:
            row_shadow = "box-shadow: inset 0 0 0 2px rgba(220, 38, 38, 0.18), 0 0 0 2px rgba(248, 113, 113, 0.35);"
            admin_badge_style = "display:inline-block; background:#dc2626; color:#ffffff; padding:4px 12px; border-radius:999px; font-weight:900; box-shadow:0 2px 4px rgba(220,38,38,0.25);"
            sub_teacher_display = f"<span style='{admin_badge_style}'>{sub_teacher_display}</span>"
        else:
            row_shadow = ""

        base_cell_style = f"padding: 12px; font-size: 15px; font-weight: bold; background-color: {bg_color} !important; color: {text_color} !important; {border_style} {row_shadow}"
        right_cell_style = base_cell_style + (" border-right: 10px solid #dc2626; border-top-right-radius: 14px; border-bottom-right-radius: 14px;" if is_admin_supervision else "")
        left_cell_style = base_cell_style + (" border-left: 10px solid #dc2626; border-top-left-radius: 14px; border-bottom-left-radius: 14px;" if is_admin_supervision else "")

        html += f"<tr style='background-color: {bg_color}; color: {text_color};'>"
        html += f"<td style='{right_cell_style}'>{abs_teacher}</td>"
        html += f"<td style='{base_cell_style}'>{row['الصف']}</td>"
        html += f"<td style='{base_cell_style}'>{row['الحصة']}</td>"
        html += f"<td style='{left_cell_style}'>{sub_teacher_display}</td></tr>"
    html += "</table></div>"
    return html

def generate_whatsapp_html(df_state, day_name, absent_list):
    if df_state is None or df_state.empty: return "", "<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>"
    absents_str = "، ".join([format_teacher_name(a) for a in absent_list]) if absent_list else "لا يوجد"
    summary = f" ملخص احتياط اليوم: {day_name}\n المعلم الغائب: {absents_str}\n تم توزيع حصص الاحتياط بنجاح عبر منظومة الباسط.. يعطيكم العافية جميعاً! "
    html_cards = ""
    for _, row in df_state.iterrows():
        sub_raw = str(row["المعلم البديل"])
        abs_raw = str(row["المعلم الغائب"])
        status = str(row.get("حالة_التكليف", ""))
        
        if status == "تقصير" or "إشراف" in sub_raw: continue
        
        sub_fmt = format_teacher_name(sub_raw)
        abs_fmt = format_teacher_name(abs_raw)
 
        spec = teachers_db.get(sub_raw, {}).get("specialty", "")
        sub_display = f"{sub_fmt} [{spec}]" if spec else sub_fmt
        
        elegant_class = format_elegant_class(row['الصف'])
        
        if status == "تبادل":
            msg = f"أهلاً بك أستاذنا المتعاون 🤝 {sub_display}،\nتم اعتماد التكليف كحصة (تبادلية) للصف ({elegant_class}) في الحصة ({row['الحصة']})، بدلاً من الأستاذ {abs_fmt}.\nعلى أن يتم التنسيق بينكما ليعوض الأستاذ {abs_fmt} حصته.\nإدارة مدرسة الباسط تشكر لكم هذا التعاون المثمر!"
            btn_color = "#00897b"
        else:
            msg = f"أهلاً بك أستاذنا المبدع {sub_display}،\nتم تكليفك اليوم بمهمة قيادة الصف ({elegant_class}) في الحصة ({row['الحصة']})، بدلاً من الأستاذ {abs_fmt}.\nشاكرين لك مبادرتك وتعاونك الدائم!\n- إدارة مدرسة الباسط"
            btn_color = "#25D366" if teachers_db.get(sub_raw, {}).get("phone", "") else "#075e54"
            
        encoded_msg = urllib.parse.quote(msg)
        phone = teachers_db.get(sub_raw, {}).get("phone", "")
        wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}" if phone else f"https://api.whatsapp.com/send?text={encoded_msg}"
        btn_text = f"✅ إرسال للأستاذ {sub_raw}" if phone else f"⚠️ إرسال (لا يوجد رقم)"
        
        card = f"<div style='background:#ffffff; border: 2px solid {btn_color}; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); direction: rtl; text-align: right;'><h4 style='color: {btn_color}; margin-top: 0; font-size: 1.1em;'>👤 {'المعلم المتعاون' if status=='تبادل' else 'المعلم البديل'}: {sub_display}</h4><p style='white-space: pre-wrap; font-size: 14px; background: #f1f8e9; padding: 10px; border-radius: 5px; color:#333; line-height: 1.6;'>{msg}</p><a href='{wa_link}' target='_blank' style='display: inline-block; background-color: {btn_color}; color: white; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px;'>{btn_text}</a></div>"
        html_cards += card
    if not html_cards: html_cards = "<div style='text-align:center; color:gray; padding:20px; border: 1px dashed #ccc; border-radius: 10px;'>جميع التكليفات إدارية أو تقصير ولا توجد رسائل فردية للمكلفين.</div>"
    return summary, html_cards
