# -*- coding: utf-8 -*-
"""
distribution.py

Phase 3J-a1: دوال مساعدة نظيفة منخفضة المخاطر من عنقود التوزيع والاحتياط.
هذا الملف لا يعتمد على Gradio ولا يستورد app.py.
"""

import urllib.parse

import pandas as pd

from config import ADMIN_ROLES
from storage import teachers_db, daily_db, SCHOOL_WEEK_DAYS, load_db, load_daily_db
from schedules import resolve_effective_dept, format_teacher_name, get_teacher_choices, get_absentee_choices, get_day_table_updates_core
from balances import get_updated_balance, get_updated_absences, get_updated_shortcomings
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


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3J-a3: refresh_ui_on_change core
# ─────────────────────────────────────────────────────────────────────────────

def refresh_ui_on_change_core(dept, day_name, is_admin_logged_in, current_abs=None):
    """يبني القيم الخام لمحرك إعادة العرض المركزي.

    يرجع 27 قيمة خام بنفس ترتيب مخرجات refresh_ui_on_change الأصلية.
    لا يعتمد على Gradio ولا يعدّل الحالة العامة مباشرة.
    """
    if not teachers_db:
        load_db()
    if not daily_db:
        load_daily_db()

    effective_dept = resolve_effective_dept(dept)
    is_shared_teacher_view = str(dept or "").strip() == "المعلمون"
    target_date = get_date_of_weekday(day_name)
    display_records = [
        r for r in daily_db
        if r["date"] == target_date and (effective_dept == "الكل" or r["dept"] == effective_dept)
    ]
    df = pd.DataFrame(
        display_records,
        columns=["المعلم الغائب", "الصف", "الحصة", "المعلم البديل", "dept", "date", "حالة_التكليف"],
    ).sort_values(["المعلم الغائب", "الحصة"])

    if not df.empty:
        df["المعلم البديل عرض"] = df.apply(format_sub_display, axis=1)
        df["المعلم الغائب"] = df["المعلم الغائب"].apply(format_teacher_name)

    is_visible = not df.empty
    warning_html = ""

    if is_admin_logged_in:
        global_records = [r for r in daily_db if r["date"] == target_date]
        uncovered = len([r for r in global_records if r["المعلم البديل"] == "إشراف إداري"])
        if uncovered > 0:
            warning_html = f"<div style='background:#ffebee; color:#c62828; padding:15px; border-radius:10px; border:2px solid #c62828; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px; animation: pulse 2s infinite;'>🚨 رادار القيادة: بقي لديك ({uncovered}) حصص إشراف إداري تتطلب التدخل العاجل!</div>"
        else:
            if len(global_records) > 0:
                warning_html = f"<div style='background:#e8f5e9; color:#2e7d32; padding:15px; border-radius:10px; border:2px solid #2e7d32; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>✅ رادار القيادة: تم تأمين المدرسة بالكامل! جميع الحصص مغطاة.</div>"
            else:
                warning_html = f"<div style='background:#f1f8e9; color:#388e3c; padding:15px; border-radius:10px; border:1px dashed #388e3c; text-align:center; font-weight:bold; font-size:15px; margin-bottom:15px;'>🛡️ النظام جاهز: لا توجد حالات غياب مسجلة حتى الآن.</div>"
    else:
        uncovered = len([r for r in display_records if r["المعلم البديل"] == "إشراف إداري"])
        if uncovered > 0:
            warning_html = f"<div style='background:#fff3e0; color:#e65100; padding:15px; border-radius:10px; border:2px solid #e65100; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>⚠️ تنبيه للقسم: يوجد ({uncovered}) حصص غير مغطاة تم تحويلها للإدارة.</div>"
        else:
            if len(display_records) > 0:
                warning_html = f"<div style='background:#e8f5e9; color:#2e7d32; padding:15px; border-radius:10px; border:2px solid #2e7d32; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>✅ اكتملت المهمة: تم تأمين جميع حصص القسم بنجاح.</div>"
            else:
                warning_html = f"<div style='background:#f1f8e9; color:#388e3c; padding:15px; border-radius:10px; border:1px dashed #388e3c; text-align:center; font-weight:bold; font-size:15px; margin-bottom:15px;'>🛡️ القسم جاهز: لا توجد حالات غياب.</div>"

    exhausted_msgs = []
    checked_exhausted = set()
    for r in display_records:
        sub = r["المعلم البديل"]
        if sub != "إشراف إداري" and r.get("حالة_التكليف") != "تقصير" and sub not in checked_exhausted:
            checked_exhausted.add(sub)
            if sub in teachers_db:
                base_p = {int(p) for p in teachers_db[sub].get(day_name, {}).keys()}
                sub_p = {
                    int(r2["الحصة"])
                    for r2 in daily_db
                    if r2["date"] == target_date
                    and r2["المعلم البديل"] == sub
                    and r2.get("حالة_التكليف") != "تقصير"
                }
                all_p = base_p | sub_p
                consecutive_groups = []
                for i in range(1, 7):
                    if i in all_p and i + 1 in all_p and i + 2 in all_p:
                        consecutive_groups.append(f"{i}، {i+1}، {i+2}")
                if consecutive_groups:
                    grp_str = consecutive_groups[0]
                    exhausted_msgs.append(f"<li style='margin-bottom:5px;'>⚠️ الأستاذ <b>{sub}</b> سيدرس الحصص ({grp_str}) متتالية!</li>")

    if exhausted_msgs:
        radar_alert = "<div style='background:#fff8e1; color:#e65100; padding:15px; border-radius:10px; border:2px solid #ffb74d; margin-bottom:15px; text-align:right;'><b style='font-size:16px;'>الرادار الإنساني (تنبيه إرهاق):</b><ul style='margin-top:8px; margin-bottom:0; padding-right:20px; font-size:14px;'>" + "".join(exhausted_msgs) + "</ul></div>"
        warning_html = radar_alert + warning_html

    persistent_conflict_html = ""
    if not is_shared_teacher_view:
        persistent_conflict_html = build_absence_conflict_warning_html(
            detect_absence_assignment_conflicts_for_context(day_name, effective_dept, current_abs),
            day_name,
        )
        if persistent_conflict_html:
            warning_html = warning_html + persistent_conflict_html

    actual_abs = sorted(list(set([r["المعلم الغائب"] for r in display_records])))
    conflicted_teachers, conflicted_slots = detect_conflicted_absence_slots(display_records)
    opts_abs = []

    if is_admin_logged_in:
        admin_title_val = "<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة العمليات الإدارية والقيادة العليا</h4><p style='text-align:center; color:#555; font-size:13px;'>صلاحيات مطلقة: يمكنك إسناد أي حصة لأي معلم، واعتماد التبادلات، ورصد التقصير.</p>"
        admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b; direction:rtl; text-align:right; line-height:1.9; font-weight:800;'>💡 <b>توضيح:</b><br>لإلغاء غياب معلم من اليوم بالكامل، اختر <b>المعلم الغائب</b> ثم اضغط زر <b>إلغاء غياب اليوم بالكامل</b>.<br>لعمل <b>تكليف احتياط رسمي</b> أو <b>اعتماد كتبادل</b>، اختر المعلم الغائب، ثم الحصة، ثم <b>البديل المنقذ</b>.<br>لعمل <b>رصد تقصير في التكليف</b>، اختر المعلم الغائب ثم الحصة، ثم اضغط زر <b>رصد تقصير في التكليف</b>.</div>"
        period_update_raw = {"choices": [], "value": None, "label": "2️⃣ اختر الحصة", "interactive": is_visible}
        cb_cross_update_raw = {"visible": False, "value": False}
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            clean_c = str(c).split(" (")[0].strip()
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == clean_c)
            has_conflict_sup = clean_c in conflicted_teachers

            if has_admin_sup and has_conflict_sup:
                radar_icon = " 🚨🔷 "
            elif has_admin_sup:
                radar_icon = " 🚨 "
            elif has_conflict_sup:
                radar_icon = " 🔷 "
            else:
                radar_icon = " ✅ "

            opts_abs.append(f"{c} ({role}){radar_icon}" if role != "معلم" else f"{c}{radar_icon}")
    else:
        if is_shared_teacher_view:
            admin_title_val = "<h4 style='color:#004d40; text-align:center; margin-top:0;'>📘 التبادل الودي الأسبوعي</h4><p style='text-align:center; color:#555; font-size:13px;'>عرض التبادلات الودية الأسبوعية وجداول المدرسة المتاحة للحساب العام للمعلمين.</p>"
            admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b;'>💡 <b>توضيح:</b> هذا الحساب مخصص للعرض المحدود فقط للوصول إلى التبادل الودي الأسبوعي، جدول اليوم، وجدول المعلم الأسبوعي.</div>"
            period_update_raw = {"choices": [], "value": None, "label": "2️⃣ الحصة", "interactive": False}
            cb_cross_update_raw = {"visible": False, "value": False, "interactive": False}
        else:
            dept_leader_title = "المعلم الأول"
            for t_info in teachers_db.values():
                if str(t_info.get("dept", "")).strip() == str(dept).strip():
                    role = str(t_info.get("role", "")).strip()
                    if "منسق" in role:
                        dept_leader_title = "منسق المادة"
                        break
                    elif "معلم أول" in role:
                        dept_leader_title = "المعلم الأول"
                        break
            admin_title_val = f"<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة عمليات {dept_leader_title} ({dept})</h4><p style='text-align:center; color:#555; font-size:13px;'>استبدل المعلم الغائب بمعلم آخر، أو فعّل التعاون للوصول لأقسام أخرى.</p>"
            admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b; direction:rtl; text-align:right; line-height:1.9; font-weight:800;'>💡 <b>توضيح:</b><br>⚫️ لإلغاء غياب معلم من اليوم بالكامل، اختر <b>المعلم الغائب</b> ثم اضغط زر <b>إلغاء غياب اليوم بالكامل</b>.<br>⚫️ لعمل <b>تكليف احتياط رسمي</b> أو <b>اعتماد كتبادل</b>، اختر المعلم الغائب، ثم اختر الحصة، ثم اختر <b>البديل المنقذ</b> من نفس القسم.<br>⚫️ إذا لم يظهر بديل مناسب من نفس القسم، اختر <b>إشراف إداري</b>، أو فعّل خيار <b>التعاون مع قسم آخر</b> لتظهر لك بدائل من الأقسام الأخرى.<br>⚫️ لعمل <b>رصد تقصير في التكليف</b>، اختر المعلم الغائب ثم اختر الحصة، ثم اضغط زر <b>رصد تقصير في التكليف</b>.</div>"
            period_update_raw = {"choices": [], "value": None, "label": "2️⃣ الحصة المراد تعديلها", "interactive": is_visible}
            cb_cross_update_raw = {"visible": True, "value": False, "interactive": True}
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            clean_c = str(c).split(" (")[0].strip()
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == clean_c)
            has_conflict_sup = clean_c in conflicted_teachers

            if has_admin_sup and has_conflict_sup:
                radar_icon = " 🚨🔷 "
            elif has_admin_sup:
                radar_icon = " 🚨 "
            elif has_conflict_sup:
                radar_icon = " 🔷 "
            else:
                radar_icon = " ✅ "

            opts_abs.append(f"{c} ({role}){radar_icon}" if role != "معلم" else f"{c}{radar_icon}")

    t_names_filtered = sorted([t for t, d in teachers_db.items() if effective_dept == "الكل" or d.get("dept") == effective_dept])
    choices = get_teacher_choices(effective_dept)
    teacher_schedule_choices = get_teacher_schedule_choices(effective_dept)
    abs_choices = get_absentee_choices(effective_dept)
    summary_txt, html_cards = generate_whatsapp_html(df, day_name, actual_abs) if not df.empty else ("", "<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>")
    styled_table_html = generate_styled_html_table(df)
    if isinstance(current_abs, str):
        current_abs = [current_abs]
    elif not current_abs:
        current_abs = []

    safe_abs_value = resolve_teacher_display_values(current_abs, abs_choices)
    fallback_abs_value = resolve_teacher_display_values(actual_abs, abs_choices)
    day_table_raw = get_day_table_updates_core(day_name, effective_dept, 0)

    return (
        {"choices": abs_choices, "value": safe_abs_value if safe_abs_value else fallback_abs_value},
        get_updated_balance(effective_dept),
        get_updated_absences(effective_dept),
        get_updated_shortcomings(effective_dept),
        *day_table_raw,
        t_names_filtered,
        teacher_schedule_choices,
        choices,
        warning_html,
        styled_table_html,
        opts_abs,
        df,
        summary_txt,
        html_cards,
        get_dynamic_header(day_name),
        admin_title_val,
        admin_help_val,
        period_update_raw,
        cb_cross_update_raw,
        is_visible,
        is_visible,
    )
