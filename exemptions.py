# -*- coding: utf-8 -*-
"""منطق الإعفاءات في منظومة مسار.

هذا الملف يحتوي دوال الإعفاءات النظيفة فقط، بلا Gradio وبلا اعتماد على app.py.
"""

import re

from config import ADMIN_ROLES
from storage import MAX_PERIODS, SCHOOL_WEEK_DAYS, teachers_db
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
        if info.get("dept") == "الهيئة الإدارية" or info.get("role", "معلم") in ADMIN_ROLES:
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
