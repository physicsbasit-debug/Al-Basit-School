# -*- coding: utf-8 -*-
"""
distribution.py

Phase 3J-a1: دوال مساعدة نظيفة منخفضة المخاطر من عنقود التوزيع والاحتياط.
هذا الملف لا يعتمد على Gradio ولا يستورد app.py.
"""

import random
import re
import urllib.parse

import pandas as pd

from config import ADMIN_ROLES
from storage import (
    teachers_db, daily_db, processed_absences, last_assigned_teachers, SCHOOL_WEEK_DAYS,
    load_db, load_daily_db, save_db, save_daily_db, state_locked,
    _queue_audit_change, _flush_audit_changes, write_audit_log,
)
from schedules import resolve_effective_dept, format_teacher_name, get_teacher_choices, get_absentee_choices, get_day_table_updates_core, get_day_overview
from balances import get_updated_balance, get_updated_absences, get_updated_shortcomings
from exemptions import is_teacher_exempt_for_slot, clean_teacher_name_from_ui
from swaps import get_date_of_weekday, get_current_day_oman, get_class_dna, check_teacher_load, format_elegant_class
from auth import get_permissions_from_flags


@state_locked
def assign_logic_core(absent_list, day_name, dept_filter, max_reserves, is_alt, is_admin_logged_in, actor_name="", actor_role=""):
    audit_entries = []

    if isinstance(absent_list, str):
        raw_absents = [absent_list]
    else:
        raw_absents = list(absent_list or [])

    absent_list_clean = []
    for item in raw_absents:
        clean_name = clean_teacher_name_from_ui(item)
        if clean_name and clean_name not in absent_list_clean:
            absent_list_clean.append(clean_name)

    target_date = get_date_of_weekday(day_name)

    existing_absent_today = []
    for row in daily_db:
        if row.get("date") != target_date:
            continue
        row_absent = clean_teacher_name_from_ui(row.get("المعلم الغائب", ""))
        if row_absent and row_absent not in existing_absent_today:
            existing_absent_today.append(row_absent)

    target_absents = []
    for name in absent_list_clean + existing_absent_today:
        if name and name not in target_absents:
            target_absents.append(name)

    if is_alt:
        records_to_keep = []
        records_to_delete = []

        for row in daily_db:
            row_absent = clean_teacher_name_from_ui(row.get("المعلم الغائب", ""))
            row_status = str(row.get("حالة_التكليف", "")).strip()
            should_replace_auto = (
                row.get("date") == target_date
                and row_absent in target_absents
                and row_status == ""
            )
            if should_replace_auto:
                records_to_delete.append(row)
            else:
                records_to_keep.append(row)

        daily_db.clear()
        daily_db.extend(records_to_keep)

        for row in records_to_delete:
            old_sub = clean_teacher_name_from_ui(row.get("المعلم البديل", ""))
            if old_sub != "إشراف إداري" and old_sub in teachers_db:
                old_count = int(teachers_db[old_sub].get("cover_count", 0) or 0)
                new_count = max(0, old_count - 1)
                teachers_db[old_sub]["cover_count"] = new_count
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    old_sub,
                    old_count,
                    new_count,
                    f"إلغاء إسناد آلي بسبب طلب مقترح آخر ليوم {day_name}",
                )

        generation_absents = target_absents
    else:
        generation_absents = absent_list_clean
        for abs_t in absent_list_clean:
            if (target_date, abs_t) not in processed_absences:
                if abs_t in teachers_db:
                    old_absent = int(teachers_db[abs_t].get("absent_count", 0) or 0)
                    new_absent = old_absent + 1
                    teachers_db[abs_t]["absent_count"] = new_absent
                    _queue_audit_change(
                        audit_entries,
                        "تعديل مرات الغياب",
                        abs_t,
                        old_absent,
                        new_absent,
                        f"تسجيل غياب يوم {day_name} ({target_date})",
                    )
                    if "absence_dates" not in teachers_db[abs_t]:
                        teachers_db[abs_t]["absence_dates"] = []
                    date_entry = f"{day_name} ({target_date})"
                    if date_entry not in teachers_db[abs_t]["absence_dates"] and target_date not in teachers_db[abs_t]["absence_dates"]:
                        teachers_db[abs_t]["absence_dates"].append(date_entry)
                processed_absences.add((target_date, abs_t))

    all_absent_today = set(target_absents if is_alt else (existing_absent_today + absent_list_clean))

    preserved_slots = {
        (
            clean_teacher_name_from_ui(row.get("المعلم الغائب", "")),
            str(row.get("الحصة", "")).strip()
        )
        for row in daily_db
        if row.get("date") == target_date
        and clean_teacher_name_from_ui(row.get("المعلم الغائب", "")) in all_absent_today
    }

    res, current_assigned = [], []
    daily_assigned_count = {t: 0 for t in teachers_db}
    assigned_periods_today = {t: set() for t in teachers_db}
    for r in daily_db:
        if r["date"] == target_date and r["المعلم البديل"] != "إشراف إداري" and r.get("حالة_التكليف") != "تقصير":
            t = r["المعلم البديل"]
            if t in daily_assigned_count:
                daily_assigned_count[t] += 1
                assigned_periods_today[t].add(int(r["الحصة"]))

    for abs_t in generation_absents:
        abs_dept = teachers_db.get(abs_t, {}).get("dept", "عام")
        for p_str, cl in teachers_db.get(abs_t, {}).get(day_name, {}).items():
            p_int = int(p_str)
            p_key = str(p_int)
            if (abs_t, p_key) in preserved_slots:
                continue

            cands = []
            for t, t_info in teachers_db.items():
                if t in all_absent_today:
                    continue
                if t_info.get("dept") != abs_dept:
                    continue
                if p_int in t_info.get(day_name, {}):
                    continue
                role = t_info.get("role", "معلم")
                if role in ADMIN_ROLES:
                    continue
                if p_int in assigned_periods_today[t]:
                    continue
                if daily_assigned_count[t] >= max_reserves:
                    continue
                if is_teacher_exempt_for_slot(t, day_name, p_int):
                    continue
                cands.append(t)
            if not cands:
                res.append({"المعلم الغائب": abs_t, "الصف": cl, "الحصة": str(p_int), "المعلم البديل": "إشراف إداري", "حالة_التكليف": ""})
            else:
                random.shuffle(cands)
                cands.sort(key=lambda t: teachers_db[t]["cover_count"])
                sel = cands[0]
                old_cover = int(teachers_db[sel].get("cover_count", 0) or 0)
                new_cover = old_cover + 1
                teachers_db[sel]["cover_count"] = new_cover
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    sel,
                    old_cover,
                    new_cover,
                    f"إسناد احتياط آلي بدل {abs_t} في الحصة {p_int} يوم {day_name}",
                )
                daily_assigned_count[sel] += 1
                assigned_periods_today[sel].add(p_int)
                current_assigned.append(sel)
                res.append({"المعلم الغائب": abs_t, "الصف": cl, "الحصة": str(p_int), "المعلم البديل": sel, "حالة_التكليف": ""})

    last_assigned_teachers.clear()
    last_assigned_teachers.extend(current_assigned)
    save_db()
    for r in res:
        r["date"] = target_date
        r["dept"] = teachers_db.get(r["المعلم الغائب"], {}).get("dept", "عام")
        is_dup = any(
            x["date"] == r["date"] and
            x["المعلم الغائب"] == r["المعلم الغائب"] and
            x["الحصة"] == r["الحصة"]
            for x in daily_db
        )
        if not is_dup:
            daily_db.append(r)
    save_daily_db()
    _flush_audit_changes(audit_entries, actor_name, actor_role)
    return {
        "refresh_dept": dept_filter,
        "refresh_day": day_name,
        "refresh_is_admin": is_admin_logged_in,
        "refresh_current_abs": (target_absents if is_alt else absent_list_clean),
    }


@state_locked
def cancel_teacher_absence_core(abs_t, day_name, dept_filter, is_admin_logged_in, current_abs, actor_name="", actor_role=""):
    if not abs_t or not day_name:
        return {
            "refresh_dept": dept_filter,
            "refresh_day": day_name,
            "refresh_is_admin": is_admin_logged_in,
            "refresh_current_abs": current_abs,
        }

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    if not abs_t_clean:
        return {
            "refresh_dept": dept_filter,
            "refresh_day": day_name,
            "refresh_is_admin": is_admin_logged_in,
            "refresh_current_abs": current_abs,
        }

    audit_entries = []
    target_date = get_date_of_weekday(day_name)
    records_to_keep, records_to_delete = [], []

    for r in daily_db:
        row_date = r.get("date")
        row_absent = str(r.get("المعلم الغائب", "")).split(" (")[0].strip()
        if row_date == target_date and row_absent == abs_t_clean:
            records_to_delete.append(r)
        else:
            records_to_keep.append(r)

    daily_db.clear()
    daily_db.extend(records_to_keep)

    for r in records_to_delete:
        sub = str(r.get("المعلم البديل", "")).replace(" 🔄", "").replace("🔄", "").strip()
        status = r.get("حالة_التكليف", "")
        if sub != "إشراف إداري" and sub in teachers_db and status == "":
            old_cover = int(teachers_db[sub].get("cover_count", 0) or 0)
            new_cover = max(0, old_cover - 1)
            teachers_db[sub]["cover_count"] = new_cover
            _queue_audit_change(
                audit_entries,
                "تعديل رصيد الاحتياط",
                sub,
                old_cover,
                new_cover,
                f"إلغاء احتياط بسبب إلغاء غياب {abs_t_clean} يوم {day_name}",
            )

    if abs_t_clean in teachers_db:
        old_absent = int(teachers_db[abs_t_clean].get("absent_count", 0) or 0)
        new_absent = max(0, old_absent - 1)
        teachers_db[abs_t_clean]["absent_count"] = new_absent
        _queue_audit_change(
            audit_entries,
            "تعديل مرات الغياب",
            abs_t_clean,
            old_absent,
            new_absent,
            f"إلغاء غياب يوم {day_name} ({target_date})",
        )
        date_entry = f"{day_name} ({target_date})"
        if "absence_dates" in teachers_db[abs_t_clean]:
            if date_entry in teachers_db[abs_t_clean]["absence_dates"]:
                teachers_db[abs_t_clean]["absence_dates"].remove(date_entry)
            elif target_date in teachers_db[abs_t_clean]["absence_dates"]:
                teachers_db[abs_t_clean]["absence_dates"].remove(target_date)

    if (target_date, abs_t_clean) in processed_absences:
        processed_absences.remove((target_date, abs_t_clean))

    save_db()
    save_daily_db()
    _flush_audit_changes(audit_entries, actor_name, actor_role)

    updated_abs = []
    if current_abs:
        updated_abs = [t for t in current_abs if clean_teacher_name_from_ui(t) != abs_t_clean]

    return {
        "refresh_dept": dept_filter,
        "refresh_day": day_name,
        "refresh_is_admin": is_admin_logged_in,
        "refresh_current_abs": updated_abs,
    }

@state_locked
def process_admin_action_core(df_state, abs_t, period, new_sub, day_name, dept_filter, is_admin_logged_in, current_abs, action_type, actor_name="", actor_role=""):
    if df_state is None or df_state.empty or not abs_t or not period:
        return {
            "refresh_dept": dept_filter,
            "refresh_day": day_name,
            "refresh_is_admin": is_admin_logged_in,
            "refresh_current_abs": current_abs,
        }

    if action_type != "penalty":
        if not new_sub or str(new_sub).startswith("⚠️") or str(new_sub).startswith("ℹ️"):
            return {
                "refresh_dept": dept_filter,
                "refresh_day": day_name,
                "refresh_is_admin": is_admin_logged_in,
                "refresh_current_abs": current_abs,
            }

    target_date = get_date_of_weekday(day_name)
    audit_entries = []

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()

    for r in daily_db:
        if r["date"] == target_date and r["المعلم الغائب"] == abs_t_clean and r["الحصة"] == p_str_clean:
            old_sub = r["المعلم البديل"]
            old_status = r.get("حالة_التكليف", "")

            if action_type == "penalty":
                target_sub = old_sub
            else:
                if not new_sub:
                    return {
                        "refresh_dept": dept_filter,
                        "refresh_day": day_name,
                        "refresh_is_admin": is_admin_logged_in,
                        "refresh_current_abs": current_abs,
                    }
                if new_sub == "إشراف إداري":
                    target_sub = new_sub
                else:
                    target_sub = new_sub.split(" (")[0].replace("🦅 ", "").strip()

            if old_sub == target_sub and action_type == "normal" and old_status == "":
                break

            if old_sub != "إشراف إداري" and old_sub in teachers_db and old_status == "":
                old_count = int(teachers_db[old_sub].get("cover_count", 0) or 0)
                new_count = max(0, old_count - 1)
                teachers_db[old_sub]["cover_count"] = new_count
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    old_sub,
                    old_count,
                    new_count,
                    f"إلغاء تكليف سابق للحصة {p_str_clean} يوم {day_name}",
                )

            if action_type == "penalty":
                if target_sub != "إشراف إداري" and old_status != "تقصير" and target_sub in teachers_db:
                    old_short = int(teachers_db[target_sub].get("shortcoming_count", 0) or 0)
                    new_short = old_short + 1
                    teachers_db[target_sub]["shortcoming_count"] = new_short
                    _queue_audit_change(
                        audit_entries,
                        "تعديل حالات التقصير",
                        target_sub,
                        old_short,
                        new_short,
                        f"رصد تقصير في تكليف الحصة {p_str_clean} يوم {day_name}",
                    )
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = "تقصير"

            elif action_type == "tabadul":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = "تبادل"

            elif action_type == "normal":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = ""
                if target_sub != "إشراف إداري" and target_sub in teachers_db:
                    old_count = int(teachers_db[target_sub].get("cover_count", 0) or 0)
                    new_count = old_count + 1
                    teachers_db[target_sub]["cover_count"] = new_count
                    _queue_audit_change(
                        audit_entries,
                        "تعديل رصيد الاحتياط",
                        target_sub,
                        old_count,
                        new_count,
                        f"تكليف احتياط رسمي للحصة {p_str_clean} يوم {day_name}",
                    )

            save_db()
            save_daily_db()
            _flush_audit_changes(audit_entries, actor_name, actor_role)
            break

    return {
        "refresh_dept": dept_filter,
        "refresh_day": day_name,
        "refresh_is_admin": is_admin_logged_in,
        "refresh_current_abs": current_abs,
    }


@state_locked
def update_manual_count_core(name, new_val, new_abs_val, new_short_val, new_phone, new_specialty, new_role, dept_filter, day_val, df_state, abs_in_list, is_admin=False, is_owner=False, actor_name="", actor_role=""):
    permissions = get_permissions_from_flags(is_admin=is_admin, is_owner=is_owner)
    can_edit_vault = permissions["can_edit_vault_basic"]
    owner_mode = permissions["can_edit_sensitive_teacher_data"]

    def build_payload(message, abs_update=None, teacher_update_1=None, teacher_update_2=None):
        return {
            "balance": get_updated_balance(dept_filter),
            "absences": get_updated_absences(dept_filter),
            "shortcomings": get_updated_shortcomings(dept_filter),
            "day_overview": get_day_overview(day_val, dept_filter),
            "message": message,
            "abs_update": abs_update or {},
            "teacher_update_1": teacher_update_1 or {},
            "teacher_update_2": teacher_update_2 or {},
        }

    if not can_edit_vault:
        return build_payload(
            "<div style='color:#c62828; font-weight:bold; background:#ffebee; padding:10px; border-radius:5px; text-align:center;'>❌ لا تملك صلاحية تعديل الخزنة.</div>"
        )

    if name and name in teachers_db:
        old_cover_count = teachers_db[name].get("cover_count", 0)
        old_absent_count = teachers_db[name].get("absent_count", 0)
        old_shortcoming_count = teachers_db[name].get("shortcoming_count", 0)

        if new_val is not None:
            try:
                parsed_cover = int(new_val)
                if parsed_cover != old_cover_count:
                    teachers_db[name]["cover_count"] = parsed_cover
                    write_audit_log(
                        "تعديل رصيد الاحتياط",
                        target_teacher=name,
                        old_value=old_cover_count,
                        new_value=parsed_cover,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if new_abs_val is not None:
            try:
                parsed_absent = int(new_abs_val)
                if parsed_absent != old_absent_count:
                    teachers_db[name]["absent_count"] = parsed_absent
                    write_audit_log(
                        "تعديل مرات الغياب",
                        target_teacher=name,
                        old_value=old_absent_count,
                        new_value=parsed_absent,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if new_short_val is not None:
            try:
                parsed_short = int(new_short_val)
                if parsed_short != old_shortcoming_count:
                    teachers_db[name]["shortcoming_count"] = parsed_short
                    write_audit_log(
                        "تعديل حالات التقصير",
                        target_teacher=name,
                        old_value=old_shortcoming_count,
                        new_value=parsed_short,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if owner_mode and new_phone is not None:
            phone_clean = re.sub(r'\D', '', str(new_phone))
            if phone_clean:
                if len(phone_clean) == 8:
                    phone_clean = "968" + phone_clean
                teachers_db[name]["phone"] = phone_clean
            else:
                teachers_db[name]["phone"] = ""
        if owner_mode and new_specialty is not None:
            teachers_db[name]["specialty"] = str(new_specialty).strip()
        if owner_mode and new_role is not None:
            teachers_db[name]["role"] = str(new_role).strip()

        save_db()
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        permission_note = "" if owner_mode else "<br><span style='color:#6b7280;'>ℹ️ تم تجاهل تعديل المنصب ورقم الواتساب والتخصص الدقيق لأن هذه الحقول مخصصة لصاحب النظام فقط.</span>"
        return build_payload(
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم حفظ التعديلات للأستاذ ({name}) بنجاح!{permission_note}</div>",
            {"choices": abs_choices},
            {"choices": choices_all, "value": None},
            {"choices": choices_all, "value": None},
        )

    return build_payload("<div style='color:red;'>❌ لم يتم الحفظ</div>")


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


def update_available_subs_smart_core(abs_t, period, intervention_type, day_name, df_state, is_admin):
    """يرجع: (choices, value, interactive) لقائمة البدائل المتاحين دون أي اعتماد على Gradio."""
    # 1 — لم يُختر معلم بعد
    if not abs_t:
        return [], None, False

    # 2 — لم تُختر الحصة بعد
    if not period:
        msg = "ℹ️ اختر الحصة أولًا"
        return [msg], msg, False

    fallback_msg = "⚠️ لا يوجد بديل متاح"
    fallback = ([fallback_msg], fallback_msg, False)

    if not day_name or not intervention_type:
        return fallback

    try:
        p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
        p_int = int(p_str_clean)
    except Exception:
        return fallback

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    target_date = get_date_of_weekday(day_name)
    already_subbing, absent_today = set(), set()
    if df_state is not None and not df_state.empty:
        subs = df_state[df_state["الحصة"] == str(p_int)]["المعلم البديل"].tolist()
        already_subbing.update(subs)
        absent_today.update(
            df_state["المعلم الغائب"].apply(lambda x: str(x).split(" (")[0].strip()).tolist()
        )

    # ✅ استبعاد المعلمين المكلفين في نفس الحصة من جميع الأقسام
    for r in daily_db:
        if r["date"] == target_date and r["الحصة"] == str(p_int) and r.get("حالة_التكليف") != "تقصير":
            already_subbing.add(r["المعلم البديل"])

    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    target_dept = intervention_type
    if "نفس القسم" in target_dept:
        target_dept = abs_dept

    def no_result_update(label):
        msg = f"⚠️ لا يوجد بديل متاح من {label}"
        return [msg], msg, False

    def admin_supervision_only_update():
        return ["إشراف إداري"], None, True

    opts = []

    # الهيئة التدريسية (يستبعد الإداريين)
    if target_dept == "الهيئة التدريسية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today:
                continue
            if is_teacher_exempt_for_slot(t, day_name, p_int):
                continue
            if info.get("dept") == "الهيئة الإدارية":
                continue
            if p_int not in info.get(day_name, {}):
                available_cands.append(t)

        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            c_dept = teachers_db[c].get("dept", "عام")
            warn_str = check_teacher_load(c, day_name, p_int)
            warn_str = f" ⚠️ {warn_str}" if warn_str else ""
            opts.append(f"{c} ({c_dept}){warn_str}")

        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return opts, None, True

    # الهيئة الإدارية (خاص بالمدير)
    if target_dept == "الهيئة الإدارية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today:
                continue
            if is_teacher_exempt_for_slot(t, day_name, p_int):
                continue
            if info.get("dept") == "الهيئة الإدارية" and p_int not in info.get(day_name, {}):
                available_cands.append(t)

        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            role = teachers_db[c].get("role", "إداري")
            opts.append(f"{c} ({role})")

        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return opts, None, True

    # --- بقية الأقسام ومعلمو الصف ---
    falcon_cands = get_falcon_eye_candidates(abs_t_clean, period, day_name)

    if target_dept != abs_dept:
        for cand_str in falcon_cands:
            name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
            if is_teacher_exempt_for_slot(name_part, day_name, p_int):
                continue
            if name_part not in already_subbing and name_part not in absent_today:
                if target_dept == "معلمو الصف" or teachers_db.get(name_part, {}).get("dept") == target_dept:
                    opts.append(cand_str)
        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return opts, None, True

    for cand_str in falcon_cands:
        name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
        if is_teacher_exempt_for_slot(name_part, day_name, p_int):
            continue
        if name_part not in already_subbing and name_part not in absent_today and teachers_db.get(name_part, {}).get("dept") == abs_dept:
            opts.append(cand_str)

    available_cands = []
    for t, info in teachers_db.items():
        if t == abs_t_clean or t in already_subbing or t in absent_today:
            continue
        if is_teacher_exempt_for_slot(t, day_name, p_int):
            continue
        if p_int not in info.get(day_name, {}):
            if info.get("dept") == target_dept:
                available_cands.append(t)

    available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
    for c in available_cands:
        is_falcon = False
        for opt in opts:
            if c in opt:
                is_falcon = True
                break
        if is_falcon:
            continue

        warn_str = check_teacher_load(c, day_name, p_int)
        warn_str = f" ⚠️ {warn_str}" if warn_str else ""
        opts.append(f"{c} ({abs_dept}){warn_str}")

    if not opts:
        if not is_admin:
            return admin_supervision_only_update()
        return no_result_update(target_dept)
    if not is_admin:
        opts.append("إشراف إداري")
    return opts, None, True

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
