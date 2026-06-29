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

import pandas as pd
import gradio as gr

from config import ADMIN_ROLES
from storage import (
    ADMIN_FILE,
    PHONES_FILE,
    REFERENCE_STATUS_FILE,
    SCHEDULE_FILES,
    MAX_PERIODS,
    SCHOOL_WEEK_DAYS,
    OFFICIAL_DEPTS,
    ensure_data_directories,
    safe_write_json,
    teachers_db,
    save_db,
    state_locked,
)

from schedules import (
    clean_teacher_name,
    extract_class_info,
    get_absentee_choices,
    get_day_overview,
    get_teacher_choices,
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

