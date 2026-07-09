# -*- coding: utf-8 -*-
"""منطق الإعفاءات في منظومة مسار.

هذا الملف يحتوي دوال الإعفاءات النظيفة فقط، بلا Gradio وبلا اعتماد على app.py.
"""

import re

from storage import (
    MAX_PERIODS,
    SCHOOL_WEEK_DAYS,
    teachers_db,
    save_db,
    state_locked,
    get_now_oman,
    write_audit_log,
)
from auth import get_permissions
from schedules import format_teacher_name, get_name_fingerprint


def normalize_exempt_slots(raw_slots):
    """Return clean [{"day": day, "period": int}] pairs for specific day-period exemptions."""
    clean_slots = []
    seen = set()
    for slot in raw_slots or []:
        day = None
        period = None
        if isinstance(slot, dict):
            day = slot.get("day") or slot.get("اليوم")
            period = slot.get("period") or slot.get("الحصة")
        elif isinstance(slot, (list, tuple)) and len(slot) >= 2:
            day, period = slot[0], slot[1]
        else:
            s = str(slot or "").strip()
            day = next((d for d in SCHOOL_WEEK_DAYS if d in s), None)
            m = re.search(r"(?:ح|الحصة)?\s*(\d+)", s)
            period = m.group(1) if m else None
        day = str(day or "").strip()
        if day not in SCHOOL_WEEK_DAYS:
            continue
        try:
            period_int = int(period)
        except Exception:
            continue
        if period_int < 1 or period_int > MAX_PERIODS:
            continue
        key = (day, period_int)
        if key in seen:
            continue
        seen.add(key)
        clean_slots.append({"day": day, "period": period_int})
    return clean_slots


def build_exempt_slots_from_days_periods(days, periods):
    clean_days = [str(d).strip() for d in (days or []) if str(d).strip() in SCHOOL_WEEK_DAYS]
    clean_periods = []
    for p in periods or []:
        try:
            p_int = int(p)
        except Exception:
            continue
        if 1 <= p_int <= MAX_PERIODS and p_int not in clean_periods:
            clean_periods.append(p_int)
    return [{"day": d, "period": p} for d in clean_days for p in clean_periods]


def format_exempt_slots_for_display(slots):
    clean_slots = normalize_exempt_slots(slots)
    if not clean_slots:
        return "—"
    return "، ".join([f"{slot['day']} ح{slot['period']}" for slot in clean_slots])


def clean_teacher_name_from_ui(value):
    text = str(value or "").strip()
    for mark in ["🚨", "🔷", "✅", "⚠️", "🟦", "🦅"]:
        text = text.replace(mark, "")
    text = " ".join(text.split())
    if " (" in text:
        text = text.split(" (")[0].strip()
    return text.strip()


def is_teacher_exempt_for_slot(teacher_name, day_name, period_int):
    teacher_name = str(teacher_name or "").split(" (")[0].strip()
    info = teachers_db.get(teacher_name, {})
    exempt_days = info.get("exempt_days", []) or []
    exempt_periods = info.get("exempt_periods", []) or []
    exempt_slots = normalize_exempt_slots(info.get("exempt_slots", []) or [])

    try:
        period_int = int(period_int)
    except Exception:
        return False

    try:
        exempt_periods = [int(p) for p in exempt_periods]
    except Exception:
        exempt_periods = [p for p in exempt_periods]

    # توافق مع الإعفاءات العامة القديمة والجديدة:
    # يوم فقط = اليوم كامل، حصة فقط = الحصة طوال الأسبوع.
    if day_name in exempt_days:
        return True
    if period_int in exempt_periods:
        return True

    # الإعفاءات المحددة الجديدة: يوم + حصة فقط.
    for slot in exempt_slots:
        if slot.get("day") == day_name and int(slot.get("period")) == period_int:
            return True
    return False


def resolve_teacher_key_from_ui(value):
    raw = str(value or "").strip()
    if not raw:
        return ""

    cleaned = clean_teacher_name_from_ui(raw)

    if cleaned in teachers_db:
        return cleaned

    if raw in teachers_db:
        return raw

    for key in teachers_db.keys():
        if str(raw).startswith(str(key) + " (") or str(cleaned).startswith(str(key) + " ("):
            return key

    try:
        target_fp = get_name_fingerprint(cleaned)
        for key in teachers_db.keys():
            if get_name_fingerprint(key) == target_fp:
                return key
    except Exception:
        pass

    return cleaned


def render_exemptions_log_html():
    active_rows = []

    for teacher_name, info in teachers_db.items():
        if info.get("is_admin_staff", False):
            continue
        days = info.get("exempt_days", []) or []
        periods = info.get("exempt_periods", []) or []
        slots = normalize_exempt_slots(info.get("exempt_slots", []) or [])

        clean_days = [str(d).strip() for d in days if str(d).strip()]
        clean_periods = []
        for p in periods:
            try:
                clean_periods.append(int(p))
            except Exception:
                if str(p).strip():
                    clean_periods.append(str(p).strip())

        if not clean_days and not clean_periods and not slots:
            continue

        active_rows.append({
            "teacher": teacher_name,
            "dept": info.get("dept", "—"),
            "days": clean_days,
            "periods": clean_periods,
            "slots": slots,
            "updated_at": info.get("exemption_updated_at", "محفوظ")
        })

    if not active_rows:
        return "<div style='background:#f8fafc; border:1px dashed #cbd5e1; border-radius:10px; padding:14px; text-align:center; color:#475569;'>🗂️ لا يوجد سجل إعفاءات محفوظ حتى الآن.</div>"

    active_rows.sort(key=lambda item: (str(item.get("dept", "")), str(item.get("teacher", ""))))

    rows_html = ""
    for item in active_rows:
        teacher_name = format_teacher_name(str(item.get("teacher", "")).strip()) if str(item.get("teacher", "")).strip() else "—"
        dept = str(item.get("dept", "—")).strip() or "—"
        days_text = "، ".join(item["days"]) if item["days"] else "—"
        periods_text = "، ".join([f"ح{p}" for p in item["periods"]]) if item["periods"] else "—"
        slots_text = format_exempt_slots_for_display(item.get("slots", []))
        updated_at = str(item.get("updated_at", "محفوظ")).strip() or "محفوظ"
        rows_html += f"""
        <tr>
            <td style='padding:8px; border:1px solid #d1d5db;'>{teacher_name}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{dept}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{days_text}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{periods_text}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{slots_text}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{updated_at}</td>
        </tr>
        """

    return f"""
    <div style='margin-top:14px; background:#f8fafc; border:1px solid #dbeafe; border-radius:12px; padding:14px;'>
        <div style='font-weight:bold; color:#0f172a; margin-bottom:8px;'>🗂️ سجل حالات الإعفاء الحالية</div>
        <div style='font-size:13px; color:#475569; margin-bottom:10px;'>
            يوم فقط = إعفاء اليوم كاملًا، حصة فقط = إعفاء الحصة طوال الأسبوع، يوم + حصة = إعفاء محدد لذلك اليوم وتلك الحصة فقط.
        </div>
        <div style='overflow-x:auto;'>
            <table style='width:100%; border-collapse:collapse; text-align:center; direction:rtl; font-size:14px;'>
                <thead>
                    <tr style='background:#0f766e; color:#ffffff;'>
                        <th style='padding:9px; border:1px solid #d1d5db;'>المعلم</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>القسم</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>أيام كاملة</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>حصص أسبوعية</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>إعفاءات محددة</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>آخر تحديث</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
    """


@state_locked
def save_teacher_rules_core(t_name, days, periods, actor_name="", actor_role="", is_admin=False, is_owner=False):
    """حفظ قواعد إعفاء المعلم وإرجاع رسالة HTML خام فقط، بلا Gradio."""
    permissions = get_permissions(role=actor_role, is_owner=is_owner, is_admin_flag=is_admin)
    if not permissions["can_manage_exemptions"]:
        return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ لا تملك صلاحية تعديل حالات الإعفاء.</div>"

    t_key = resolve_teacher_key_from_ui(t_name)
    if t_key and t_key in teachers_db:
        if teachers_db[t_key].get("is_admin_staff", False):
            return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ لا يمكن تسجيل حالات إعفاء للهيئة الإدارية أو الإداريين.</div>"
        clean_days = [str(d).strip() for d in (days or []) if str(d).strip() in SCHOOL_WEEK_DAYS]
        clean_periods = []
        for p in (periods or []):
            try:
                p_int = int(p)
                if 1 <= p_int <= MAX_PERIODS and p_int not in clean_periods:
                    clean_periods.append(p_int)
            except Exception:
                continue

        old_days = list(teachers_db[t_key].get("exempt_days", []) or [])
        old_periods = list(teachers_db[t_key].get("exempt_periods", []) or [])
        old_slots = normalize_exempt_slots(teachers_db[t_key].get("exempt_slots", []) or [])

        if clean_days and clean_periods:
            # الاختيار المشترك يعني إعفاءات محددة: كل الأيام المختارة × كل الحصص المختارة.
            new_days = []
            new_periods = []
            new_slots = build_exempt_slots_from_days_periods(clean_days, clean_periods)
            exemption_mode = "إعفاء محدد"
            mode_details = f"إعفاءات محددة: {format_exempt_slots_for_display(new_slots)}"
        elif clean_days:
            new_days = clean_days
            new_periods = []
            new_slots = []
            exemption_mode = "إعفاء يوم كامل"
            mode_details = f"أيام كاملة: {'، '.join(new_days)}"
        elif clean_periods:
            new_days = []
            new_periods = clean_periods
            new_slots = []
            exemption_mode = "إعفاء حصة أسبوعية"
            mode_details = "حصص أسبوعية: " + "، ".join([f"ح{p}" for p in new_periods])
        else:
            new_days = []
            new_periods = []
            new_slots = []
            exemption_mode = "إلغاء الإعفاء"
            mode_details = "لا توجد أيام أو حصص محددة"

        teachers_db[t_key]["exempt_days"] = new_days
        teachers_db[t_key]["exempt_periods"] = new_periods
        teachers_db[t_key]["exempt_slots"] = new_slots

        if old_days != new_days or old_periods != new_periods or old_slots != new_slots:
            write_audit_log(
                "تعديل حالات الإعفاء",
                target_teacher=t_key,
                old_value={"days": old_days, "periods": old_periods, "slots": old_slots},
                new_value={"days": new_days, "periods": new_periods, "slots": new_slots},
                details=f"{exemption_mode} - {mode_details}",
                actor_name=actor_name,
                actor_role=actor_role
            )

        if new_days or new_periods or new_slots:
            teachers_db[t_key]["exemption_updated_at"] = get_now_oman().strftime("%Y-%m-%d %H:%M")
            status_html = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم تثبيت قوانين الإعفاء للأستاذ ({format_teacher_name(t_key)}) بنجاح!<br><span style='font-weight:600; color:#166534;'>{mode_details}</span></div>"
        else:
            teachers_db[t_key]["exemption_updated_at"] = ""
            status_html = f"<div style='color:#b45309; font-weight:bold; background:#fff7ed; padding:10px; border-radius:5px; text-align:center;'>ℹ️ تم إلغاء إعفاءات الأستاذ ({format_teacher_name(t_key)}) لأنه لا توجد أيام أو حصص محددة.</div>"

        save_db()
        return status_html

    return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ اختر معلمًا أولًا قبل حفظ الإعفاء.</div>"
