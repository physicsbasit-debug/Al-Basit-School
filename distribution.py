# -*- coding: utf-8 -*-
"""
distribution.py

Phase 3J-a1: دوال مساعدة نظيفة منخفضة المخاطر من عنقود التوزيع والاحتياط.
هذا الملف لا يعتمد على Gradio ولا يستورد app.py.
"""

from config import ADMIN_ROLES
from storage import teachers_db, daily_db, SCHOOL_WEEK_DAYS
from schedules import resolve_effective_dept, format_teacher_name
from exemptions import is_teacher_exempt_for_slot
from swaps import get_date_of_weekday, get_current_day_oman, get_class_dna, check_teacher_load


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
