# -*- coding: utf-8 -*-
"""
school_data.py
دوال مركز البيانات المدرسية النظيفة - Phase 3E-a.

هذه الوحدة لا تستورد app.py ولا تحتوي ربط Gradio العام.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import html as html_lib
import urllib.parse
from pathlib import Path

from PIL import Image
import pandas as pd
import gradio as gr

from config import ADMIN_ROLES, DEFAULT_SCHOOL_CONFIG
from storage import (
    ADMIN_FILE,
    PHONES_FILE,
    REFERENCE_STATUS_FILE,
    SCHEDULE_FILES,
    SCHOOL_CONFIG_FILE,
    BRANDING_DIR,
    MAX_PERIODS,
    SCHOOL_WEEK_DAYS,
    OFFICIAL_DEPTS,
    ensure_data_directories,
    safe_write_json,
    load_school_config,
    write_audit_log,
    teachers_db,
    save_db,
    state_locked,
    get_now_oman,
)

from schedules import (
    clean_teacher_name,
    extract_class_info,
    get_absentee_choices,
    get_day_overview,
    get_teacher_choices,
    get_name_fingerprint,
    resolve_effective_dept,
)
from balances import (
    get_updated_absences,
    get_updated_balance,
)


tz_oman = datetime.timezone(datetime.timedelta(hours=4))

SCHEDULE_PERIOD_HEADER_WORDS = {
    1: "الاولى",
    2: "الثانية",
    3: "الثالثة",
    4: "الرابعة",
    5: "الخامسة",
    6: "السادسة",
    7: "السابعة",
    8: "الثامنة",
}


def load_reference_status_registry():
    if not os.path.exists(REFERENCE_STATUS_FILE):
        return {}
    try:
        with open(REFERENCE_STATUS_FILE, "r", encoding="utf-8") as registry_file:
            loaded = json.load(registry_file)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(f"load_reference_status_registry error: {exc}")
        return {}


def save_reference_status_registry(registry):
    return safe_write_json(REFERENCE_STATUS_FILE, registry)


def update_reference_file_status(
    status_key,
    stored_path,
    *,
    original_name="",
    applied=None,
    extracted_count=None,
    department="",
):
    registry = load_reference_status_registry()
    record = registry.get(str(status_key), {})
    now_text = datetime.datetime.now(tz_oman).strftime("%Y-%m-%d %H:%M:%S")

    record.update({
        "status_key": str(status_key),
        "stored_path": str(stored_path or ""),
        "stored_name": os.path.basename(str(stored_path or "")) if stored_path else "",
        "department": str(department or record.get("department", "")),
        "updated_at": now_text,
    })

    if original_name:
        record["original_name"] = os.path.basename(str(original_name))
    if applied is not None:
        record["applied"] = bool(applied)
        if bool(applied):
            record["applied_at"] = now_text
    if extracted_count is not None:
        try:
            record["extracted_count"] = int(extracted_count)
        except Exception:
            record["extracted_count"] = extracted_count

    registry[str(status_key)] = record
    save_reference_status_registry(registry)
    return record


def _reference_status_key(kind, department=""):
    if kind == "schedule":
        return f"schedule::{str(department).strip()}"
    return str(kind).strip()


def get_reference_file_status(file_path, status_key="", data_loaded=False):
    registry = load_reference_status_registry()
    record = registry.get(str(status_key), {}) if status_key else {}
    file_exists = os.path.isfile(file_path)
    data_active = bool(data_loaded)

    if file_exists:
        modified_time = datetime.datetime.fromtimestamp(
            os.path.getmtime(file_path),
            tz=tz_oman
        ).strftime("%Y-%m-%d %H:%M")
        file_name = (
            record.get("original_name")
            or record.get("stored_name")
            or os.path.basename(file_path)
        )

        if data_active:
            return {
                "exists": True,
                "data_loaded": True,
                "status_kind": "active",
                "status_text": "✅ الملف محفوظ والبيانات مفعّلة",
                "file_name": file_name,
                "modified_at": record.get("applied_at") or modified_time,
            }

        return {
            "exists": True,
            "data_loaded": False,
            "status_kind": "stored",
            "status_text": "🟠 الملف محفوظ وينتظر تحديث البيانات",
            "file_name": file_name,
            "modified_at": modified_time,
        }

    if data_active:
        return {
            "exists": False,
            "data_loaded": True,
            "status_kind": "loaded_only",
            "status_text": "🔵 البيانات محمّلة والملف المرجعي غير موجود",
            "file_name": record.get("original_name") or "—",
            "modified_at": record.get("applied_at") or record.get("updated_at") or "—",
        }

    return {
        "exists": False,
        "data_loaded": False,
        "status_kind": "missing",
        "status_text": "❌ لا يوجد ملف ولا بيانات مفعّلة",
        "file_name": record.get("original_name") or "—",
        "modified_at": record.get("updated_at") or "—",
    }


def dept_has_loaded_schedule_data(dept_name):
    weekdays = SCHOOL_WEEK_DAYS
    for _teacher_name, info in teachers_db.items():
        if str(info.get("dept", "")).strip() != str(dept_name).strip():
            continue
        for day_name in weekdays:
            day_schedule = info.get(day_name, {})
            if isinstance(day_schedule, dict) and any(str(v).strip() for v in day_schedule.values()):
                return True
    return False


def get_school_data_center_status():
    schedules_status = {}
    for dept_name, file_path in SCHEDULE_FILES.items():
        schedules_status[dept_name] = get_reference_file_status(
            file_path,
            _reference_status_key("schedule", dept_name),
            dept_has_loaded_schedule_data(dept_name),
        )

    admin_loaded = any(
        str(info.get("dept", "")).strip() == "الهيئة الإدارية"
        or str(info.get("role", "")).strip() in ADMIN_ROLES
        for info in teachers_db.values()
    )
    phones_loaded = any(
        str(info.get("phone", "")).strip()
        for info in teachers_db.values()
    )

    return {
        "admin_file": get_reference_file_status(
            ADMIN_FILE, _reference_status_key("admin"), admin_loaded
        ),
        "phones_file": get_reference_file_status(
            PHONES_FILE, _reference_status_key("phones"), phones_loaded
        ),
        "schedules": schedules_status,
    }


def render_reference_file_card(title, file_info):
    status_kind = file_info.get("status_kind", "missing")
    palettes = {
        "active": ("#2e7d32", "#e8f5e9"),
        "stored": ("#d97706", "#fff7ed"),
        "loaded_only": ("#0284c7", "#e0f2fe"),
        "missing": ("#c62828", "#ffebee"),
    }
    status_color, bg_color = palettes.get(status_kind, palettes["missing"])

    return f"""
    <div style="
        background:{bg_color};
        border:1px solid #d0d7de;
        border-right:5px solid {status_color};
        border-radius:12px;
        padding:16px;
        margin-bottom:12px;
        box-shadow:0 2px 6px rgba(0,0,0,0.05);
    ">
        <div style="font-size:18px; font-weight:bold; color:#004d40; margin-bottom:10px;">
            {title}
        </div>
        <div style="font-size:15px; margin-bottom:6px;">
            <b>الحالة:</b> {file_info["status_text"]}
        </div>
        <div style="font-size:15px; margin-bottom:6px;">
            <b>اسم الملف:</b> {file_info["file_name"]}
        </div>
        <div style="font-size:15px;">
            <b>آخر تحديث:</b> {file_info["modified_at"]}
        </div>
    </div>
    """


def render_admin_reference_card():
    status = get_school_data_center_status()
    return render_reference_file_card("🏢 ملف الإداريين", status["admin_file"])


def render_phones_reference_card():
    status = get_school_data_center_status()
    return render_reference_file_card("📱 ملف أرقام المعلمين", status["phones_file"])


def render_schedule_reference_cards():
    status = get_school_data_center_status()
    html_parts = ['<div style="margin-top:14px;"><h3 style="color:#004d40; margin-bottom:12px;">📚 ملفات جداول الأقسام</h3></div>']

    for dept_name, _file_path in SCHEDULE_FILES.items():
        file_info = status["schedules"][dept_name]
        html_parts.append(render_reference_file_card(f"📘 {dept_name}", file_info))

    return "".join(html_parts)


@state_locked
def refresh_admins_from_reference_core(dept_filter, is_owner=False):
    """
    يقرأ ملف الإداريين المرجعي بصيغة Excel أو CSV اعتمادًا على أعمدة الهاتف والدور والاسم،
    ويضيف أو يحدّث كل اسم داخل teachers_db بقسم "الهيئة الإدارية". التنفيذ مقصور على مالك النظام؛
    الملف غير الموجود أو is_owner=False ينتج عنه رسالة خطأ بلا تغيير. ترجع الدالة tuple طويلًا
    بقيمة قريبة من مخرجات Gradio مثل choices/value، لا حمولة بيانات خام.
    """
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث بيانات الإداريين متاح لمالك النظام فقط.</div>",
            {}, {}, {}, {}, {},
            {"value": render_admin_reference_card()}, {"value": None}
        )
    if not os.path.exists(ADMIN_FILE):
        return (
            "<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف إداريين مرجعي حتى الآن.</div>",
            {}, {}, {}, {}, {},
            {"value": render_admin_reference_card()}, {"value": None}
        )

    try:
        df = pd.read_excel(ADMIN_FILE, header=None) if not ADMIN_FILE.endswith(".csv") else pd.read_csv(ADMIN_FILE, header=None)
        df = df.fillna("")

        added_or_updated = 0
        found_names = []

        for r in range(len(df)):
            raw_phone = str(df.iloc[r, 0]).strip() if df.shape[1] > 0 else ""
            raw_role = str(df.iloc[r, 1]).strip() if df.shape[1] > 1 else ""
            raw_name = str(df.iloc[r, 2]).strip() if df.shape[1] > 2 else ""

            if not raw_name or raw_name == "nan":
                continue

            t_name = clean_teacher_name(raw_name)
            if not t_name or len(t_name) < 3:
                continue

            if raw_phone.endswith(".0"):
                raw_phone = raw_phone[:-2]

            phone_digits = re.sub(r"\D", "", raw_phone)
            if len(phone_digits) == 8:
                phone_digits = "968" + phone_digits

            role_val = raw_role if raw_role else "أخصائي اجتماعي"

            if t_name not in teachers_db:
                teachers_db[t_name] = {
                    "dept": "الهيئة الإدارية",
                    "cover_count": 0,
                    "absent_count": 0,
                    "shortcoming_count": 0,
                    "phone": "",
                    "specialty": "",
                    "role": role_val,
                    "exempt_days": [],
                    "exempt_periods": [],
                    "exempt_slots": [],
                    "absence_dates": [],
                    "الأحد": {},
                    "الإثنين": {},
                    "الثلاثاء": {},
                    "الأربعاء": {},
                    "الخميس": {}
                }
            else:
                teachers_db[t_name]["dept"] = "الهيئة الإدارية"
                teachers_db[t_name]["role"] = role_val

            teachers_db[t_name]["phone"] = phone_digits if phone_digits else teachers_db[t_name].get("phone", "")
            found_names.append(t_name)
            added_or_updated += 1

        save_db()
        update_reference_file_status(
            _reference_status_key("admin"),
            ADMIN_FILE,
            applied=True,
            extracted_count=added_or_updated,
        )

        dept_filter = resolve_effective_dept(dept_filter)
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        t_names_filtered = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])

        names_list_str = "، ".join(found_names) if found_names else "لا توجد أسماء صالحة"
        msg = (
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>"
            f"✅ تم تحديث بيانات الإداريين من الملف المرجعي بنجاح."
            f"<br>👥 الأسماء: {names_list_str}"
            f"</div>"
        )

        return (
            msg,
            {"choices": abs_choices},
            {"choices": choices_all, "value": None},
            {"choices": choices_all, "value": None},
            {"choices": t_names_filtered, "value": None},
            {"value": get_updated_balance(dept_filter)},
            {"value": render_admin_reference_card()},
            {"value": None}
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث الإداريين من المرجع: {str(e)}</div>",
            {}, {}, {}, {}, {},
            {"value": render_admin_reference_card()}, {"value": None}
        )


@state_locked
def refresh_phones_from_reference_core(dept_filter, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث أرقام المعلمين متاح لمالك النظام فقط.</div>",
            {}, {"value": render_phones_reference_card()}, {"value": None}
        )
    if not os.path.exists(PHONES_FILE):
        return (
            "<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف أرقام معلمين مرجعي حتى الآن.</div>",
            {}, {"value": render_phones_reference_card()}, {"value": None}
        )

    try:
        df = pd.read_excel(PHONES_FILE, header=None) if not PHONES_FILE.endswith(".csv") else pd.read_csv(PHONES_FILE, header=None)
        updated = 0
        db_fingerprints = {k: get_name_fingerprint(k) for k in teachers_db.keys()}

        for r in range(len(df)):
            raw_name = str(df.iloc[r, 0]).strip() if df.shape[1] > 0 else ""
            raw_phone = str(df.iloc[r, 1]).strip() if df.shape[1] > 1 else ""

            if not raw_name or raw_name == "nan":
                continue

            if raw_phone.endswith(".0"):
                raw_phone = raw_phone[:-2]

            phone_digits = re.sub(r"\D", "", raw_phone)
            if len(phone_digits) == 8:
                phone_digits = "968" + phone_digits
            if not phone_digits:
                continue

            phone_first_name, phone_name_fingerprint = get_name_fingerprint(raw_name)
            if not phone_first_name:
                continue

            for db_key, (db_first_name, db_words) in db_fingerprints.items():
                if db_first_name == phone_first_name and len(db_words) > 0 and db_words.issubset(phone_name_fingerprint):
                    teachers_db[db_key]["phone"] = phone_digits
                    updated += 1
                    break

        save_db()
        update_reference_file_status(
            _reference_status_key("phones"),
            PHONES_FILE,
            applied=True,
            extracted_count=updated,
        )

        msg = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>✅ تم تحديث أرقام ({updated}) من المعلمين من الملف المرجعي بنجاح.</div>"

        return (
            msg,
            {"value": get_updated_balance(dept_filter)},
            {"value": render_phones_reference_card()},
            {"value": None}
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث أرقام المعلمين من المرجع: {str(e)}</div>",
            {}, {"value": render_phones_reference_card()}, {"value": None}
        )
def save_admin_reference_file(file, is_owner=False):
    if not bool(is_owner):
        return "<div style='color:red; font-weight:bold;'>❌ اعتماد الملفات المرجعية متاح لمالك النظام فقط.</div>", gr.update(value=render_admin_reference_card())
    if file is None:
        return "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف الإداريين أولاً.</div>", gr.update(value=render_admin_reference_card())

    is_valid, msg = validate_reference_filename(file.name, ["الإدارة", "الادارة", "الإداريين", "الاداريين", "admin", "staff"])
    if not is_valid:
        return f"<div style='color:red; font-weight:bold;'>{msg}</div>", gr.update(value=render_admin_reference_card())

    try:
        ensure_data_directories()
        shutil.copy2(file.name, ADMIN_FILE)
        update_reference_file_status(
            _reference_status_key("admin"),
            ADMIN_FILE,
            original_name=file.name,
            applied=False,
        )
        return "<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد ملف الإداريين المرجعي بنجاح.</div>", gr.update(value=render_admin_reference_card())
    except Exception as e:
        return f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ الملف المرجعي: {str(e)}</div>", gr.update(value=render_admin_reference_card())


def save_phones_reference_file(file, is_owner=False):
    if not bool(is_owner):
        return "<div style='color:red; font-weight:bold;'>❌ اعتماد الملفات المرجعية متاح لمالك النظام فقط.</div>", gr.update(value=render_phones_reference_card())
    if file is None:
        return "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف أرقام المعلمين أولاً.</div>", gr.update(value=render_phones_reference_card())

    try:
        file_path = file.name if hasattr(file, "name") else str(file)

        is_valid, msg = validate_reference_filename(file_path, ["أرقام", "ارقام", "هواتف", "واتساب", "phones", "teacher_phones"])
        if not is_valid:
            return f"<div style='color:red; font-weight:bold;'>{msg}</div>", gr.update(value=render_phones_reference_card())

        ensure_data_directories()
        shutil.copy2(file_path, PHONES_FILE)
        update_reference_file_status(
            _reference_status_key("phones"),
            PHONES_FILE,
            original_name=file_path,
            applied=False,
        )

        return "<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد ملف أرقام المعلمين المرجعي بنجاح.</div>", gr.update(value=render_phones_reference_card())

    except Exception as e:
        return f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ ملف الأرقام المرجعي: {str(e)}</div>", gr.update(value=render_phones_reference_card())


def save_schedule_reference_file(file, dept_name, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ اعتماد الجداول المرجعية متاح لمالك النظام فقط.</div>",
            gr.update(value=render_schedule_reference_cards())
        )
    if file is None:
        return (
            "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف الجدول أولاً.</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    if dept_name not in SCHEDULE_FILES:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ القسم غير معتمد: {dept_name}</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    try:
        file_path = file.name if hasattr(file, "name") else str(file)

        is_valid, msg = validate_reference_filename(file_path, [dept_name])
        if not is_valid:
            return (
                f"<div style='color:red; font-weight:bold;'>{msg}</div>",
                gr.update(value=render_schedule_reference_cards())
            )

        ensure_data_directories()
        shutil.copy2(file_path, SCHEDULE_FILES[dept_name])
        update_reference_file_status(
            _reference_status_key("schedule", dept_name),
            SCHEDULE_FILES[dept_name],
            original_name=file_path,
            applied=False,
            department=dept_name,
        )

        return (
            f"<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد الملف المرجعي لقسم ({dept_name}) بنجاح.</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ الملف المرجعي للقسم ({dept_name}): {str(e)}</div>",
            gr.update(value=render_schedule_reference_cards())
        )


def _normalize_schedule_header_text(value):
    text_value = str(value or "").strip()
    text_value = text_value.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text_value = re.sub(r"\s+", " ", text_value)
    return text_value


def _excel_column_label_zero_based(index):
    """تحويل رقم عمود صفري إلى حرف Excel تقريبي للرسائل فقط."""
    try:
        index = int(index)
    except Exception:
        return str(index)
    label = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = chr(65 + remainder) + label
    return label


def precheck_schedule_excel_template(df, dept_name=""):
    """
    فحص سريع قبل قراءة جداول المعلمين.
    لا يغير منطق القراءة، بل يمنع الفشل الصامت عند رفع قالب غير مطابق
    أو عند عدم تطابق إعداد 7/8 حصص مع ملف Excel.
    """
    try:
        if df is None or len(df) == 0 or len(df.columns) == 0:
            return False, "الملف فارغ أو لا يحتوي بيانات قابلة للقراءة."

        header_row = None
        normalized_header_row = ""
        rows_to_scan = min(15, len(df))

        for i in range(rows_to_scan):
            row_values = [
                _normalize_schedule_header_text(value)
                for value in df.iloc[i].values
            ]
            row_joined = " ".join(row_values)
            if "اليوم" in row_joined and "الاولى" in row_joined:
                header_row = i
                normalized_header_row = row_joined
                break

        if header_row is None:
            return (
                False,
                "لم يُعثر على صف العناوين المتوقع (اليوم/الأولى) ضمن أول 15 صفًا. "
                "تأكد من اتباع قالب جداول المعلمين الرسمي."
            )

        if MAX_PERIODS < 8 and "الثامنة" in normalized_header_row:
            return (
                False,
                "يبدو أن ملف الجدول يحتوي على الحصة الثامنة، بينما إعداد التشغيل الحالي مضبوط على 7 حصص. "
                "افتح إعدادات التشغيل المدرسية، اختر 8 حصص، احفظ الإعداد، ثم أعد تشغيل المنظومة قبل الاستيراد."
            )

        expected_periods = [
            SCHEDULE_PERIOD_HEADER_WORDS[p]
            for p in range(1, MAX_PERIODS + 1)
            if p in SCHEDULE_PERIOD_HEADER_WORDS
        ]
        missing_from_full_header = [
            word for word in expected_periods
            if word not in normalized_header_row
        ]
        if missing_from_full_header:
            missing_text = "، ".join(missing_from_full_header)
            return (
                False,
                f"ملف الجدول لا يحتوي عناوين الحصص المطلوبة حتى ح{MAX_PERIODS}. "
                f"العناوين الناقصة: {missing_text}. تأكد من اختيار عدد الحصص الصحيح ومن استخدام القالب الرسمي."
            )

        expected_width = MAX_PERIODS + 1  # عدد الحصص + عمود اليوم
        expected_bases = [0, MAX_PERIODS + 2]
        valid_blocks = []
        block_notes = []

        for base_col in expected_bases:
            if base_col >= len(df.columns):
                continue

            end_col = base_col + MAX_PERIODS
            if end_col >= len(df.columns):
                block_notes.append(
                    f"الكتلة التي تبدأ من العمود {_excel_column_label_zero_based(base_col)} "
                    f"لا تحتوي {expected_width} أعمدة متوقعة."
                )
                continue

            block_values = [
                _normalize_schedule_header_text(df.iloc[header_row, col])
                for col in range(base_col, end_col + 1)
            ]
            block_joined = " ".join(block_values)

            if "اليوم" not in block_joined:
                block_notes.append(
                    f"لم يُعثر على عمود اليوم داخل الكتلة التي تبدأ من العمود {_excel_column_label_zero_based(base_col)}."
                )
                continue

            missing_in_block = [
                word for word in expected_periods
                if word not in block_joined
            ]
            if missing_in_block:
                block_notes.append(
                    f"الكتلة التي تبدأ من العمود {_excel_column_label_zero_based(base_col)} "
                    f"لا تحتوي كل عناوين الحصص المطلوبة حتى ح{MAX_PERIODS}."
                )
                continue

            valid_blocks.append(base_col)

        if not valid_blocks:
            notes_text = " ".join(block_notes) if block_notes else "لم تتطابق أي كتلة مع البنية المتوقعة."
            return (
                False,
                "لم يتطابق ملف الجدول مع عدد الحصص المحدد في إعدادات التشغيل. "
                f"الإعداد الحالي: {MAX_PERIODS} حصص. {notes_text}"
            )

        return True, ""

    except Exception as exc:
        return False, f"تعذر فحص قالب ملف الجدول قبل الاستيراد: {str(exc)}"


def render_schedule_precheck_error_html(message, dept_name=""):
    dept_part = f" لقسم ({html_lib.escape(str(dept_name))})" if dept_name else ""
    safe_message = html_lib.escape(str(message or ""))
    return (
        "<div style='color:#b91c1c;background:#fee2e2;padding:12px;"
        "border-radius:10px;border-right:5px solid #dc2626;font-weight:800;line-height:1.8;'>"
        f"❌ تعذر تحديث جدول{dept_part}.<br>{safe_message}"
        "</div>"
    )


def validate_reference_filename(file_name, expected_keywords):
    if not file_name:
        return False, "❌ لم يتم العثور على اسم الملف."

    base_name = os.path.basename(str(file_name)).strip().lower()

    if "." in base_name:
        base_name = ".".join(base_name.split(".")[:-1]).strip()

    normalized_name = (
        base_name
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )

    normalized_keywords = []
    for kw in expected_keywords:
        kw_norm = (
            str(kw).strip().lower()
            .replace("أ", "ا")
            .replace("إ", "ا")
            .replace("آ", "ا")
            .replace("ى", "ي")
            .replace("ة", "ه")
        )
        normalized_keywords.append(kw_norm)

    for kw in normalized_keywords:
        if kw in normalized_name:
            return True, ""

    expected_str = " / ".join(expected_keywords)
    return False, f"❌ اسم الملف لا يطابق المطلوب. يجب أن يحتوي على: {expected_str}"


@state_locked
def process_uploaded_excel_core(file, selected_dept, current_day):
    """
    منطق رفع ملف Excel لقسم محدد بقيم خام فقط.

    يرجع 10 قيم خام:
    (dept_choices_or_None, abs_choices_or_None, teacher_choices_a_or_None,
     teacher_choices_b_or_None, balance_html, absences_html, day_overview,
     message_html, teacher_names_all_or_None, reset_upload=True)
    """
    if file is None:
        return (
            None,
            None,
            None,
            None,
            get_updated_balance("الكل"),
            get_updated_absences("الكل"),
            get_day_overview(current_day, "الكل"),
            "<div style='color:red; font-weight:bold;'>❌ الرجاء رفع ملف الإكسل أولاً.</div>",
            None,
            True,
        )

    try:
        df = pd.read_excel(file.name, header=None) if not file.name.endswith('.csv') else pd.read_csv(file.name, header=None)
        df = df.fillna('')

        precheck_ok, precheck_message = precheck_schedule_excel_template(df, selected_dept)
        if not precheck_ok:
            return (
                None,
                None,
                None,
                None,
                get_updated_balance("الكل"),
                get_updated_absences("الكل"),
                get_day_overview(current_day, "الكل"),
                render_schedule_precheck_error_html(precheck_message, selected_dept),
                None,
                True,
            )

        found_in_file = []
        start_row = 0
        for i in range(min(15, len(df))):
            row_str = " ".join([str(x) for x in df.iloc[i].values])
            if "اليوم" in row_str and ("الأولى" in row_str or "الاولى" in row_str):
                start_row = i - 2
                break
        if start_row < 0:
            start_row = 0

        for r in range(start_row, len(df), 10):
            if r + 2 >= len(df):
                break
            for base_col in [0, MAX_PERIODS + 2]:
                if base_col + MAX_PERIODS >= len(df.columns):
                    continue
                t_name_raw = str(df.iloc[r, base_col]).strip()
                if not t_name_raw or "ALBATINAH" in t_name_raw.upper() or "اليوم" in t_name_raw:
                    continue
                t_name = clean_teacher_name(t_name_raw)
                if not t_name or len(t_name) < 3:
                    continue
                if t_name not in found_in_file:
                    found_in_file.append(t_name)

                if t_name not in teachers_db:
                    teachers_db[t_name] = {
                        "dept": selected_dept,
                        "cover_count": 0,
                        "absent_count": 0,
                        "shortcoming_count": 0,
                        "phone": "",
                        "specialty": "",
                        "role": "معلم",
                        "exempt_days": [],
                        "exempt_periods": [],
                        "exempt_slots": [],
                        "absence_dates": [],
                        "الأحد": {},
                        "الإثنين": {},
                        "الثلاثاء": {},
                        "الأربعاء": {},
                        "الخميس": {},
                    }
                else:
                    teachers_db[t_name]["dept"] = selected_dept

                col_to_p = {}
                day_col = -1
                for c in range(base_col, min(base_col + MAX_PERIODS + 1, len(df.columns))):
                    val = str(df.iloc[r + 2, c]).strip().replace("أ", "ا").replace("إ", "ا")
                    if "اليوم" in val:
                        day_col = c
                    elif "الاولى" in val:
                        col_to_p[c] = 1
                    elif "الثانية" in val:
                        col_to_p[c] = 2
                    elif "الثالثة" in val:
                        col_to_p[c] = 3
                    elif "الرابعة" in val:
                        col_to_p[c] = 4
                    elif "الخامسة" in val:
                        col_to_p[c] = 5
                    elif "السادسة" in val:
                        col_to_p[c] = 6
                    elif "السابعة" in val:
                        col_to_p[c] = 7
                    elif "الثامنة" in val:
                        col_to_p[c] = 8

                if day_col == -1:
                    day_col = base_col + MAX_PERIODS
                if day_col >= len(df.columns):
                    continue

                for dr in range(r + 3, min(r + 8, len(df))):
                    day_cell = str(df.iloc[dr, day_col]).replace("أ", "ا").replace("إ", "ا")
                    current_day_val = next((d for d in ["الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس"] if d in day_cell), None)
                    if not current_day_val:
                        continue
                    current_day_val = current_day_val.replace("الاحد", "الأحد").replace("الاثنين", "الإثنين").replace("الاربعاء", "الأربعاء")
                    for c, pnum in col_to_p.items():
                        if c < len(df.columns):
                            val = str(df.iloc[dr, c]).strip()
                            cls = extract_class_info(val, selected_dept)
                            if cls:
                                teachers_db[t_name][current_day_val][pnum] = cls

        save_db()
        t_names_all = sorted(list(teachers_db.keys()))
        choices_all = get_teacher_choices("الكل")
        abs_choices = get_absentee_choices("الكل")
        names_list_str = "، ".join(found_in_file)
        current_time = get_now_oman().strftime("%H:%M:%S")
        success_msg = (
            f"<div style='color:#004d40; background:#e0f2f1; padding:15px; border-radius:10px; border-right: 5px solid #004d40;'>"
            f"<b style='font-size:1.2em;'>✅ تمت معالجة مصفوفة ({selected_dept}) بنجاح فائق!</b> 🕒 {current_time}"
            f"<br>📌 <b>المعلمون المستخرجون:</b> {len(found_in_file)} معلمين"
            f"<br>👨‍🏫 <b>الأسماء:</b> {names_list_str}"
            f"<br><hr style='border-top:1px solid #b2dfdb; margin:10px 0;'>"
            f"📊 إجمالي المعلمين في المنظومة: {len(t_names_all)}</div>"
        )
        return (
            ["الكل"] + OFFICIAL_DEPTS,
            abs_choices,
            choices_all,
            choices_all,
            get_updated_balance("الكل"),
            get_updated_absences("الكل"),
            get_day_overview(current_day, "الكل"),
            success_msg,
            t_names_all,
            True,
        )

    except Exception as e:
        return (
            None,
            None,
            None,
            None,
            get_updated_balance("الكل"),
            get_updated_absences("الكل"),
            get_day_overview(current_day, "الكل"),
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء الرفع: {str(e)}</div>",
            None,
            True,
        )


@state_locked
def delete_department_data_core(dept_to_delete, current_day):
    """Core logic for deleting department data without Gradio objects.

    Returns 10 raw slots matching the original Gradio wrapper contract:
    (dept_choices, abs_choices, teacher_choices_a, teacher_choices_b,
     balance_html, absences_html, day_overview, message_html,
     teacher_names_all, reset_upload)
    """
    if not dept_to_delete:
        return (
            None,
            None,
            None,
            None,
            get_updated_balance("الكل"),
            get_updated_absences("الكل"),
            get_day_overview(current_day, "الكل"),
            "<div style='color:red; font-weight:bold;'>❌ الرجاء تحديد القسم أولاً.</div>",
            None,
            False,
        )

    teachers_to_delete = [t for t, d in teachers_db.items() if d.get("dept") == dept_to_delete]
    for teacher_name in teachers_to_delete:
        del teachers_db[teacher_name]
    save_db()

    teacher_names_all = sorted(list(teachers_db.keys()))
    msg = (
        f"<div style='color:#c62828; background:#ffebee; padding:15px; border-radius:10px; "
        f"border-right: 5px solid #c62828;'><b style='font-size:1.2em;'>🗑️ تمت عملية المسح بنجاح!</b><br>"
        f"تم حذف جميع بيانات وسجلات معلمي قسم ({dept_to_delete}).</div>"
    )

    choices_all = get_teacher_choices("الكل")
    return (
        ["الكل"] + OFFICIAL_DEPTS,
        get_absentee_choices("الكل"),
        choices_all,
        choices_all,
        get_updated_balance("الكل"),
        get_updated_absences("الكل"),
        get_day_overview(current_day, "الكل"),
        msg,
        teacher_names_all,
        True,
    )

@state_locked
def refresh_schedule_from_reference_core(dept_name, current_day, is_owner=False):
    """
    منطق تحديث جدول قسم من الملف المرجعي بقيم خام فقط.

    يرجع 8 قيم خام:
    (message_html, abs_choices_or_None, choices_all_or_None,
     balance_html_or_None, absences_html_or_None, day_overview_or_None,
     cards_html, reset_upload=True)
    """
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث الجداول المرجعية متاح لمالك النظام فقط.</div>",
            None,
            None,
            None,
            None,
            None,
            render_schedule_reference_cards(),
            True,
        )

    if dept_name not in SCHEDULE_FILES:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ القسم غير معتمد: {dept_name}</div>",
            None,
            None,
            None,
            None,
            None,
            render_schedule_reference_cards(),
            True,
        )

    schedule_file = SCHEDULE_FILES[dept_name]

    if not os.path.exists(schedule_file):
        return (
            f"<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف مرجعي محفوظ لقسم ({dept_name}) حتى الآن.</div>",
            None,
            None,
            None,
            None,
            None,
            render_schedule_reference_cards(),
            True,
        )

    try:
        df = pd.read_excel(schedule_file, header=None) if not schedule_file.endswith(".csv") else pd.read_csv(schedule_file, header=None)
        df = df.fillna("")

        precheck_ok, precheck_message = precheck_schedule_excel_template(df, dept_name)
        if not precheck_ok:
            return (
                render_schedule_precheck_error_html(precheck_message, dept_name),
                None,
                None,
                None,
                None,
                None,
                render_schedule_reference_cards(),
                True,
            )

        found_in_file = []
        start_row = 0

        for i in range(min(15, len(df))):
            row_str = " ".join([str(x) for x in df.iloc[i].values])
            if "اليوم" in row_str and ("الأولى" in row_str or "الاولى" in row_str):
                start_row = i - 2
                break

        if start_row < 0:
            start_row = 0

        for r in range(start_row, len(df), 10):
            if r + 2 >= len(df):
                break

            for base_col in [0, MAX_PERIODS + 2]:
                if base_col + MAX_PERIODS >= len(df.columns):
                    continue

                t_name_raw = str(df.iloc[r, base_col]).strip()
                if not t_name_raw or "ALBATINAH" in t_name_raw.upper() or "اليوم" in t_name_raw:
                    continue

                t_name = clean_teacher_name(t_name_raw)
                if not t_name or len(t_name) < 3:
                    continue

                if t_name not in found_in_file:
                    found_in_file.append(t_name)

                if t_name not in teachers_db:
                    teachers_db[t_name] = {
                        "dept": dept_name,
                        "cover_count": 0,
                        "absent_count": 0,
                        "shortcoming_count": 0,
                        "phone": "",
                        "specialty": "",
                        "role": "معلم",
                        "exempt_days": [],
                        "exempt_periods": [],
                        "exempt_slots": [],
                        "absence_dates": [],
                        "الأحد": {},
                        "الإثنين": {},
                        "الثلاثاء": {},
                        "الأربعاء": {},
                        "الخميس": {},
                    }
                else:
                    teachers_db[t_name]["dept"] = dept_name
                    teachers_db[t_name]["الأحد"] = {}
                    teachers_db[t_name]["الإثنين"] = {}
                    teachers_db[t_name]["الثلاثاء"] = {}
                    teachers_db[t_name]["الأربعاء"] = {}
                    teachers_db[t_name]["الخميس"] = {}

                col_to_p = {}
                day_col = -1
                for c in range(base_col, min(base_col + MAX_PERIODS + 1, len(df.columns))):
                    val = str(df.iloc[r + 2, c]).strip().replace("أ", "ا").replace("إ", "ا")
                    if "اليوم" in val:
                        day_col = c
                    elif "الاولى" in val:
                        col_to_p[c] = 1
                    elif "الثانية" in val:
                        col_to_p[c] = 2
                    elif "الثالثة" in val:
                        col_to_p[c] = 3
                    elif "الرابعة" in val:
                        col_to_p[c] = 4
                    elif "الخامسة" in val:
                        col_to_p[c] = 5
                    elif "السادسة" in val:
                        col_to_p[c] = 6
                    elif "السابعة" in val:
                        col_to_p[c] = 7
                    elif "الثامنة" in val:
                        col_to_p[c] = 8

                if day_col == -1:
                    day_col = base_col + MAX_PERIODS
                if day_col >= len(df.columns):
                    continue

                for dr in range(r + 3, min(r + 8, len(df))):
                    day_cell = str(df.iloc[dr, day_col]).replace("أ", "ا").replace("إ", "ا")
                    current_day_val = next((d for d in ["الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس"] if d in day_cell), None)
                    if not current_day_val:
                        continue

                    current_day_val = current_day_val.replace("الاحد", "الأحد").replace("الاثنين", "الإثنين").replace("الاربعاء", "الأربعاء")

                    for c, pnum in col_to_p.items():
                        if c < len(df.columns):
                            val = str(df.iloc[dr, c]).strip()
                            cls = extract_class_info(val, dept_name)
                            if cls:
                                teachers_db[t_name][current_day_val][pnum] = cls

        save_db()
        update_reference_file_status(
            _reference_status_key("schedule", dept_name),
            schedule_file,
            applied=True,
            extracted_count=len(found_in_file),
            department=dept_name,
        )

        choices_all = get_teacher_choices("الكل")
        abs_choices = get_absentee_choices("الكل")
        names_list_str = "، ".join(found_in_file) if found_in_file else "لا توجد أسماء صالحة"

        msg = (
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>"
            f"✅ تم تحديث قسم ({dept_name}) من الملف المرجعي بنجاح."
            f"<br>👥 الأسماء: {names_list_str}"
            f"</div>"
        )

        return (
            msg,
            abs_choices,
            choices_all,
            get_updated_balance("الكل"),
            get_updated_absences("الكل"),
            get_day_overview(current_day, "الكل"),
            render_schedule_reference_cards(),
            True,
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث قسم ({dept_name}) من المرجع: {str(e)}</div>",
            None,
            None,
            None,
            None,
            None,
            render_schedule_reference_cards(),
            True,
        )



@state_locked
def save_school_operational_settings_core(
    periods_per_day,
    is_owner=False,
    actor_name="",
    actor_role="",
):
    """
    ينفذ منطق حفظ إعدادات التشغيل (عدد الحصص اليومية) ويُرجع قيمًا خامة
    كافية للـwrapper لإنتاج نفس مخرجات Gradio القديمة.
    لا يحتوي gr.update. لا يستورد gradio. لا يستورد app.py.
    """
    current_config = load_school_config()

    def _get_current_periods(cfg):
        try:
            raw = cfg.get("periods_per_day", DEFAULT_SCHOOL_CONFIG["periods_per_day"])
            parsed = int(str(raw).strip())
        except Exception:
            parsed = int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])
        return parsed if parsed in (7, 8) else int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])

    def _coerce(value, default):
        try:
            parsed = int(str(value).strip())
        except Exception:
            parsed = default if default is not None else int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])
        if parsed not in (7, 8):
            parsed = default if default in (7, 8) else int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])
        return int(parsed)

    current_saved = _get_current_periods(current_config)

    if not bool(is_owner):
        return {
            "periods_value": current_saved,
            "message": "<div style='color:#b91c1c;font-weight:800;'>رفض الحفظ: إعدادات التشغيل مخصصة لمالك النظام فقط.</div>",
            "summary_config": current_config,
            "status_config": current_config,
        }

    new_periods = _coerce(periods_per_day, current_saved)
    if new_periods not in (7, 8):
        return {
            "periods_value": current_saved,
            "message": "<div style='color:#b91c1c;font-weight:800;'>عدد الحصص يجب أن يكون 7 أو 8 فقط.</div>",
            "summary_config": current_config,
            "status_config": current_config,
        }

    old_periods = current_saved
    current_config["periods_per_day"] = int(new_periods)

    if not safe_write_json(SCHOOL_CONFIG_FILE, current_config):
        return {
            "periods_value": old_periods,
            "message": "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ إعداد عدد الحصص في ملف المدرسة.</div>",
            "summary_config": current_config,
            "status_config": current_config,
        }

    if old_periods != new_periods:
        write_audit_log(
            "تعديل إعداد عدد الحصص اليومية",
            target_teacher="",
            old_value=old_periods,
            new_value=new_periods,
            details="تحديث عدد الحصص اليومية من إعدادات التشغيل المدرسية. يلزم إعادة تشغيل المنظومة للتطبيق.",
            actor_name=actor_name,
            actor_role=actor_role,
        )

    saved_config = load_school_config()
    reboot_note = ""
    if int(MAX_PERIODS) != int(new_periods):
        reboot_note = "<br>⚠️ يلزم عمل Restart / Factory reboot حتى تعمل المنظومة بعدد الحصص الجديد."

    return {
        "periods_value": int(new_periods),
        "message": (
            "<div style='color:#166534;background:#dcfce7;padding:10px;"
            "border-radius:8px;font-weight:800;line-height:1.8;'>"
            f"تم حفظ عدد الحصص اليومية: {int(new_periods)}.{reboot_note}"
            "</div>"
        ),
        "summary_config": saved_config,
        "status_config": saved_config,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 3K-identity-core: دوال هوية المدرسة (منطق خالص بلا Gradio)
# ──────────────────────────────────────────────────────────────────────────────

# ثوابت هوية المدرسة (مستقلة عن app.py)
IDENTITY_CONFIG_KEYS = (
    "school_name",
    "directorate_region",
    "logo_url",
    "theme_color",
    "theme_color_2",
    "accent_color",
)

FIXED_IDENTITY_KEYS = (
    "ministry_name",
    "directorate_prefix",
    "system_name",
    "system_subtitle",
    "developer_credit",
)


def _normalize_identity_text(value, fallback="", max_length=220):
    """تنظيف وتقليم نص الهوية."""
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        cleaned = str(fallback or "").strip()
    return cleaned[:max_length]


def _normalize_hex_color(value, fallback):
    """تطبيع لون HEX مع fallback آمن."""
    raw = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return raw.lower()
    fallback_raw = str(fallback or "#004d40").strip()
    return fallback_raw.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", fallback_raw) else "#004d40"


def _is_valid_identity_logo_value(value):
    """التحقق من صلاحية قيمة الشعار (URL أو مسار ملف أو base64)."""
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw.startswith("data:image/"):
        return True
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return True
    path = os.path.abspath(raw)
    return os.path.isfile(path)


def _save_uploaded_identity_logo(uploaded_file):
    """حفظ ملف شعار مرفوع إلى مجلد branding وإرجاع المسار النسبي."""
    if uploaded_file is None:
        return None

    source_path = getattr(uploaded_file, "name", uploaded_file)
    source_path = str(source_path or "").strip()
    if not source_path or not os.path.isfile(source_path):
        raise ValueError("ملف الشعار المرفوع غير صالح.")

    try:
        with Image.open(source_path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("الملف المرفوع ليس صورة صالحة.") from exc

    ext = Path(source_path).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        ext = ".png"

    ensure_data_directories()
    destination = os.path.join(BRANDING_DIR, f"school_logo{ext}")
    shutil.copy2(source_path, destination)
    return os.path.relpath(destination, os.getcwd())


@state_locked
def save_school_identity_settings_core(
    school_name,
    directorate_region,
    logo_url,
    logo_upload,
    theme_color,
    theme_color_2,
    accent_color,
    is_owner=False,
):
    """
    ينفذ منطق حفظ إعدادات هوية المدرسة، مثل الاسم والمديرية والشعار والألوان، ويرجع
    (config, status_html, apply_globals). ترفض الدالة غير المالك، والاسم الفارغ، وأي لون ليس
    بصيغة HEX صحيحة (#rrggbb)، مع apply_globals=False ودون حفظ.
    لا يحتوي gr.update. لا يستورد gradio. لا يستورد app.py.
    لا يستدعي _identity_full_output ولا _apply_school_identity_globals.
    """
    if not bool(is_owner):
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رفض الحفظ: إعدادات الهوية مخصصة لمالك النظام فقط.</div>",
            False,
        )

    school_name_clean = _normalize_identity_text(school_name, "", 140)
    directorate_region_clean = _normalize_identity_text(
        directorate_region,
        DEFAULT_SCHOOL_CONFIG["directorate_region"],
        80,
    )
    if not school_name_clean:
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>اسم المدرسة حقل إلزامي.</div>",
            False,
        )

    colors = {
        "theme_color": str(theme_color or "").strip(),
        "theme_color_2": str(theme_color_2 or "").strip(),
        "accent_color": str(accent_color or "").strip(),
    }
    invalid_colors = [
        key for key, value in colors.items()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    ]
    if invalid_colors:
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>ألوان الهوية يجب أن تكون بصيغة HEX مثل #004d40.</div>",
            False,
        )

    saved_logo_value = str(logo_url or "").strip()
    try:
        uploaded_logo = _save_uploaded_identity_logo(logo_upload)
        if uploaded_logo:
            saved_logo_value = uploaded_logo
    except Exception as exc:
        return (
            load_school_config(),
            f"<div style='color:#b91c1c;font-weight:800;'>{html_lib.escape(str(exc))}</div>",
            False,
        )

    if not saved_logo_value:
        saved_logo_value = str(DEFAULT_SCHOOL_CONFIG["logo_url"])

    if not _is_valid_identity_logo_value(saved_logo_value):
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رابط أو ملف الشعار غير صالح.</div>",
            False,
        )

    new_config = load_school_config()
    for fixed_key in FIXED_IDENTITY_KEYS:
        new_config[fixed_key] = DEFAULT_SCHOOL_CONFIG[fixed_key]

    new_config.update({
        "school_name": school_name_clean,
        "directorate_region": directorate_region_clean,
        "logo_url": saved_logo_value,
        "theme_color": colors["theme_color"].lower(),
        "theme_color_2": colors["theme_color_2"].lower(),
        "accent_color": colors["accent_color"].lower(),
    })

    if not safe_write_json(SCHOOL_CONFIG_FILE, new_config):
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ ملف إعدادات المدرسة.</div>",
            False,
        )

    return (
        new_config,
        "<div style='color:#166534;background:#dcfce7;padding:10px;border-radius:8px;font-weight:800;'>تم حفظ هوية المدرسة بنجاح. العناصر الثابتة بقيت كما هي، وتغيرت المدرسة والمحافظة والشعار والألوان فقط.</div>",
        True,
    )


@state_locked
def reset_school_identity_settings_core(is_owner=False):
    """
    ينفذ منطق إعادة ضبط هوية المدرسة للإعدادات الافتراضية.
    يرجع (config, status_html, apply_globals).
    لا يحتوي gr.update. لا يستورد gradio. لا يستورد app.py.
    """
    if not bool(is_owner):
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رفض الاستعادة: هذه الأداة مخصصة لمالك النظام فقط.</div>",
            False,
        )

    config = load_school_config()

    for key in FIXED_IDENTITY_KEYS:
        config[key] = DEFAULT_SCHOOL_CONFIG[key]
    for key in IDENTITY_CONFIG_KEYS:
        config[key] = DEFAULT_SCHOOL_CONFIG[key]

    if not safe_write_json(SCHOOL_CONFIG_FILE, config):
        return (
            load_school_config(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر استعادة الهوية الافتراضية.</div>",
            False,
        )

    return (
        config,
        "<div style='color:#166534;background:#dcfce7;padding:10px;border-radius:8px;font-weight:800;'>تمت استعادة الهوية الافتراضية. تُطبق الألوان العامة بالكامل بعد إعادة تشغيل التطبيق.</div>",
        True,
    )
