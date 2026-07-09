#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_masar_safety.py
شبكة أمان ثابتة لمنظومة مسار قبل وبعد أي تعديل.

الاستخدام:
    python check_masar_safety.py app.py
    python check_masar_safety.py Masar_v1_8_4p_shared_pin_and_day_filter.py

ملاحظة:
- السكربت لا يشغّل المنظومة ولا يغيّر أي ملف.
- يفحص علامات حساسة في الكود حتى لا تعود أخطاء قديمة بصمت، لأن الصمت في الكود غالبًا ليس حكمة، بل كمين.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import py_compile
import re
import sys
import tokenize
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


EXPECTED_SYMBOL_COUNTS = {
    "❌": 52,
    "🤝": 9,
    "🦅": 5,
    "⚠️": 22,
}

REQUIRED_ELEM_IDS = [
    "school_data_panel_references",
    "school_data_panel_identity",
    "school_data_panel_periods",
    "school_data_panel_accounts",
    "school_data_panel_audit",
]

REQUIRED_BUTTON_IDS = [
    "school_data_btn_references",
    "school_data_btn_identity",
    "school_data_btn_periods",
    "school_data_btn_accounts",
    "school_data_btn_audit",
]

REQUIRED_FEATURE_MARKERS = [
    ("school_data_panel_js", "قاعدة فتح لوحات مركز البيانات عبر JavaScript المباشر"),
    ("show_school_data_panel", "دالة إظهار لوحات مركز البيانات"),
    ("masar-arrow-fix", "إصلاح سهم القوائم المفردة RTL"),
    ("masar-accordion-arrow-fix", "إصلاح سهم الـAccordion RTL"),
    ("absent-box", "إصلاح multiselect الخاص بالمعلمين الغائبين"),
    ("exempt_slots", "منطق الإعفاءات المحددة يوم+حصة"),
    ("is_teacher_exempt_for_slot", "دالة الإعفاء المركزية"),
    ("day_dept_filter", "فلتر جدول اليوم المستقل عن dept_in"),
    ("render_day_all_departments_html", "عرض جدول اليوم لكل الأقسام بتنسيق الأقسام"),
    ("format_sub_display_for_image", "حذف إيموجي التبادل من الصورة فقط"),
]

FORBIDDEN_PATTERNS = [
    (r"school_data_tab\.select\s*\(", "ممنوع رجوع school_data_tab.select للّوحات الداخلية في مركز البيانات"),
    (r"select_tab_js\(\s*[\"']مركز البيانات[\"']", "ممنوع رجوع select_tab_js('مركز البيانات')"),
    (r"for\s+base_col\s+in\s+\[\s*0\s*,\s*9\s*\]", "ممنوع رجوع قارئ Excel إلى [0, 9]"),
    (r"SCHOOL_CONFIG\s*\[\s*[\"']periods_per_day[\"']\s*\]\s*=\s*8", "ممنوع وجود إجبار مؤقت للحصص إلى 8"),
]

STYLE_MARKERS = [
    "masar-file-upload-right",
    "masar-date-label-right",
    "svelte-jdcl7l",
    "background-color",
]


@dataclass
class CheckResult:
    name: str
    status: str  # PASS / FAIL / WARN / INFO
    detail: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def collect_style_text(app_path: Path) -> str:
    """اجمع نص CSS إن كان داخليًا أو خارجيًا بعد مرحلة استخراج CSS."""
    texts = []
    for css_name in ("masar_styles.css", "styles.css"):
        css_path = app_path.with_name(css_name)
        if css_path.exists():
            try:
                texts.append(css_path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                texts.append(css_path.read_text(encoding="utf-8-sig"))
    return "\n".join(texts)


def line_numbers_for_pattern(text: str, pattern: str) -> list[int]:
    regex = re.compile(pattern)
    return [i for i, line in enumerate(text.splitlines(), start=1) if regex.search(line)]


def find_function_range(lines: list[str], func_name: str) -> tuple[int, int] | None:
    start = None
    pattern = re.compile(rf"^def\s+{re.escape(func_name)}\s*\(")
    for idx, line in enumerate(lines, start=1):
        if pattern.match(line):
            start = idx
            break
    if start is None:
        return None

    end = len(lines)
    for idx in range(start + 1, len(lines) + 1):
        line = lines[idx - 1]
        if line.startswith("def ") or line.startswith("class "):
            end = idx - 1
            break
    return start, end


def function_body(text: str, func_name: str) -> str:
    lines = text.splitlines()
    rng = find_function_range(lines, func_name)
    if not rng:
        return ""
    start, end = rng
    return "\n".join(lines[start - 1:end])


def add(results: list[CheckResult], name: str, status: str, detail: str) -> None:
    results.append(CheckResult(name=name, status=status, detail=detail))


def check_syntax(path: Path, results: list[CheckResult]) -> None:
    try:
        py_compile.compile(str(path), doraise=True)
        add(results, "Python syntax", "PASS", "py_compile نجح بلا أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "Python syntax", "FAIL", f"فشل py_compile: {exc}")


def check_forbidden_patterns(text: str, results: list[CheckResult]) -> None:
    for pattern, desc in FORBIDDEN_PATTERNS:
        lines = line_numbers_for_pattern(text, pattern)
        if lines:
            add(results, desc, "FAIL", f"وجد النمط في الأسطر: {lines[:10]}")
        else:
            add(results, desc, "PASS", "غير موجود.")


def check_required_markers(combined_text: str, app_text: str, results: list[CheckResult]) -> None:
    for marker, desc in REQUIRED_FEATURE_MARKERS:
        count = combined_text.count(marker)
        if count > 0:
            add(results, desc, "PASS", f"موجود بعدد {count}.")
        else:
            add(results, desc, "FAIL", f"المؤشر غير موجود: {marker}")

    for elem_id in REQUIRED_ELEM_IDS:
        count = app_text.count(elem_id)
        add(
            results,
            f"elem_id مركز البيانات: {elem_id}",
            "PASS" if count > 0 else "FAIL",
            f"عدد الظهور: {count}",
        )

    for btn_id in REQUIRED_BUTTON_IDS:
        count = app_text.count(btn_id)
        add(
            results,
            f"زر مركز البيانات: {btn_id}",
            "PASS" if count > 0 else "FAIL",
            f"عدد الظهور: {count}",
        )


SYMBOL_SCAN_MODULES = [
    "app.py",
    "distribution.py",
    "swaps.py",
    "exemptions.py",
    "school_data.py",
    "schedules.py",
    "balances.py",
    "storage.py",
    "auth.py",
    "config.py",
]


def _docstring_spans(tree: ast.AST) -> set[tuple[int, int, int, int]]:
    """حدّد مواضع docstrings حتى لا تُحسب ضمن الرموز الحساسة."""
    spans: set[tuple[int, int, int, int]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and hasattr(first, "end_lineno")
            and hasattr(first, "end_col_offset")
        ):
            spans.add((first.lineno, first.col_offset, first.end_lineno, first.end_col_offset))
    return spans


def _token_inside_span(token: tokenize.TokenInfo, spans: set[tuple[int, int, int, int]]) -> bool:
    start_line, start_col = token.start
    end_line, end_col = token.end
    for span_start_line, span_start_col, span_end_line, span_end_col in spans:
        starts_inside = (start_line > span_start_line) or (start_line == span_start_line and start_col >= span_start_col)
        ends_inside = (end_line < span_end_line) or (end_line == span_end_line and end_col <= span_end_col)
        if starts_inside and ends_inside:
            return True
    return False


def _code_text_without_comments_or_docstrings(source: str) -> str:
    """أعد نصًا برمجيًا قابلاً للعد مع تجاهل comments وdocstrings فقط."""
    try:
        spans = _docstring_spans(ast.parse(source))
    except SyntaxError:
        spans = set()

    tokens: list[str] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type in {
            tokenize.COMMENT,
            tokenize.ENCODING,
            tokenize.ENDMARKER,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.NEWLINE,
            tokenize.NL,
        }:
            continue
        if token.type == tokenize.STRING and _token_inside_span(token, spans):
            continue
        tokens.append(token.string)
    return "\n".join(tokens)


def check_symbol_counts_across_modules(app_path: Path, results: list[CheckResult], expected: dict[str, int]) -> None:
    """افحص مجموع الرموز الحساسة عبر وحدات المنظومة، لا app.py وحده.

    يستبعد هذا الفحص check_masar_safety.py نفسه، ويتجاهل التعليقات وdocstrings
    لتجنب المطابقات الزائفة بعد تفكيك المنظومة إلى وحدات متعددة.
    """
    totals = {symbol: 0 for symbol in expected}
    per_file: dict[str, dict[str, int]] = {}
    scanned_files: list[str] = []

    for module_name in SYMBOL_SCAN_MODULES:
        module_path = app_path.with_name(module_name)
        if not module_path.exists():
            continue
        try:
            source = module_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            source = module_path.read_text(encoding="utf-8-sig")
        cleaned = _code_text_without_comments_or_docstrings(source)
        scanned_files.append(module_name)
        file_counts = {symbol: cleaned.count(symbol) for symbol in expected}
        per_file[module_name] = file_counts
        for symbol, count in file_counts.items():
            totals[symbol] += count

    add(
        results,
        "فحص الرموز الحساسة عبر كل الوحدات",
        "PASS" if scanned_files else "FAIL",
        "تم فحص الوحدات: " + ", ".join(scanned_files) if scanned_files else "لم يتم العثور على وحدات للفحص.",
    )

    for symbol, expected_count in expected.items():
        actual = totals[symbol]
        contributing = {name: counts[symbol] for name, counts in per_file.items() if counts[symbol]}
        status = "PASS" if actual == expected_count else "FAIL"
        add(
            results,
            f"عدد الرمز {symbol} عبر الوحدات",
            status,
            f"المتوقع {expected_count}، الحالي {actual}. التفاصيل: {contributing}",
        )


def check_symbol_counts(text: str, results: list[CheckResult], expected: dict[str, int]) -> None:
    """فحص قديم محفوظ للتوافق، لكن الفحص المعتمد بعد 3J هو عبر الوحدات."""
    for symbol, expected_count in expected.items():
        actual = text.count(symbol)
        status = "PASS" if actual == expected_count else "FAIL"
        add(results, f"عدد الرمز {symbol}", status, f"المتوقع {expected_count}، الحالي {actual}.")


def check_excel_and_periods(text: str, results: list[CheckResult]) -> None:
    good = "MAX_PERIODS + 2" in text
    add(
        results,
        "قارئ Excel يستخدم MAX_PERIODS + 2",
        "PASS" if good else "FAIL",
        "وجد MAX_PERIODS + 2." if good else "لم يجد MAX_PERIODS + 2.",
    )

    # تحذير إذا وجد أي [0, 9] عام، حتى لو لم يكن داخل base_col.
    general_lines = line_numbers_for_pattern(text, r"\[\s*0\s*,\s*9\s*\]")
    if general_lines:
        add(results, "أي ظهور عام لـ[0, 9]", "WARN", f"وجد في الأسطر: {general_lines[:10]}")
    else:
        add(results, "أي ظهور عام لـ[0, 9]", "PASS", "غير موجود.")


def check_error_updates(text: str, results: list[CheckResult]) -> None:
    m = re.search(r"error_updates\s*=\s*\[\s*gr\.update\(\)\s*\]\s*\*\s*(\d+)", text)
    if not m:
        add(results, "error_updates", "FAIL", "لم يتم العثور على تعريف error_updates المتوقع.")
        return
    count = int(m.group(1))
    add(
        results,
        "error_updates count",
        "PASS" if count == 27 else "FAIL",
        f"القيمة الحالية: {count}، المتوقعة: 27.",
    )


def check_exemption_centralization(text: str, results: list[CheckResult]) -> None:
    lines = text.splitlines()
    rng = find_function_range(lines, "is_teacher_exempt_for_slot")
    if not rng:
        add(results, "دالة الإعفاء المركزية", "FAIL", "لم يتم العثور على is_teacher_exempt_for_slot.")
        return

    start, end = rng
    add(results, "دالة الإعفاء المركزية", "PASS", f"موجودة من السطر {start} إلى {end}.")

    # نبحث فقط عن شروط عضوية مباشرة مثل:
    #   if day_name in exempt_days:
    #   if p_int in t_info.get("exempt_periods", []):
    # ولا نعتبر مجرد قراءة/عرض info.get("exempt_days") مخالفة. نعم، الكود يحتاج عدسة لا مطرقة.
    direct_patterns = [
        r"if\s+.*day_name\s+in\s+.*exempt_days",
        r"if\s+.*period_?int\s+in\s+.*exempt_periods",
        r"if\s+.*p_int\s+in\s+.*exempt_periods",
        r"if\s+.*day_name\s+in\s+.*\.get\(\s*[\"']exempt_days[\"']",
        r"if\s+.*p_int\s+in\s+.*\.get\(\s*[\"']exempt_periods[\"']",
        r"if\s+.*period_?int\s+in\s+.*\.get\(\s*[\"']exempt_periods[\"']",
    ]

    offenders: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        if start <= idx <= end:
            continue
        for pat in direct_patterns:
            if re.search(pat, line):
                offenders.append((idx, line.strip()))
                break

    if offenders:
        preview = "; ".join(f"L{n}: {l[:120]}" for n, l in offenders[:6])
        add(results, "منع فحص الإعفاء inline خارج الدالة المركزية", "FAIL", preview)
    else:
        add(results, "منع فحص الإعفاء inline خارج الدالة المركزية", "PASS", "لا توجد شروط مباشرة خارج الدالة المركزية.")

    for func_name, min_calls in [
        ("assign_logic_core", 1),
        ("update_available_subs_smart_core", 1),
        ("get_falcon_eye_candidates", 1),
    ]:
        body = function_body(text, func_name)
        if not body:
            add(results, f"{func_name} موجودة", "WARN", "لم يتم العثور على الدالة، راجع الاسم أو مكانها.")
            continue
        calls = body.count("is_teacher_exempt_for_slot")
        add(
            results,
            f"{func_name} تستخدم دالة الإعفاء المركزية",
            "PASS" if calls >= min_calls else "FAIL",
            f"عدد الاستدعاءات داخل الدالة: {calls}.",
        )

    load_body = function_body(text, "check_teacher_load")
    if load_body:
        direct = "exempt_days" in load_body or "exempt_periods" in load_body
        add(
            results,
            "check_teacher_load لا يحتوي فحص إعفاء مباشر",
            "PASS" if not direct else "WARN",
            "لا يحتوي فحص إعفاء مباشر." if not direct else "يحتوي exempt_days/exempt_periods؛ راجعه لأنه دالة تحذير لا ترشيح.",
        )


def check_day_filter_isolation(text: str, results: list[CheckResult]) -> None:
    day_count = text.count("day_dept_filter")
    change_count = text.count("day_dept_filter.change")
    dept_change_count = text.count("dept_in.change")

    add(results, "day_dept_filter موجود", "PASS" if day_count > 0 else "FAIL", f"عدد الظهور: {day_count}.")
    add(results, "day_dept_filter.change مستقل", "PASS" if change_count > 0 else "FAIL", f"عدد الظهور: {change_count}.")
    add(results, "dept_in.change ما زال موجودًا", "PASS" if dept_change_count > 0 else "WARN", f"عدد الظهور: {dept_change_count}.")

    # فحص تقريبي: تحديث جدول اليوم يجب أن يكون مرتبطًا بالفلتر المستقل.
    if "get_day_table_updates" in text and "day_dept_filter.change" in text:
        add(results, "ربط جدول اليوم مستقل", "PASS", "وجد get_day_table_updates و day_dept_filter.change.")
    else:
        add(results, "ربط جدول اليوم مستقل", "WARN", "لم يظهر الربط المتوقع بوضوح؛ راجع يدويًا.")


def check_shared_pin(text: str, results: list[CheckResult]) -> None:
    has_shared = "is_shared_teacher" in text
    has_pin = "self_pin_accordion" in text or "تغيير رمز دخولي" in text
    add(results, "is_shared_teacher", "PASS" if has_shared else "WARN", "موجود." if has_shared else "غير موجود نصيًا.")
    add(results, "عناصر تغيير الرمز موجودة للفحص", "PASS" if has_pin else "WARN", "موجودة." if has_pin else "لم تظهر نصيًا.")


def check_css_markers(combined_text: str, results: list[CheckResult]) -> None:
    for marker in STYLE_MARKERS:
        count = combined_text.count(marker)
        add(
            results,
            f"مؤشر CSS: {marker}",
            "PASS" if count > 0 else "WARN",
            f"عدد الظهور: {count}.",
        )



def check_external_css_extraction(app_path: Path, app_text: str, style_text: str, results: list[CheckResult]) -> None:
    """فحص مرحلة استخراج CSS الخارجي v1.8.5a."""
    css_path = app_path.with_name("masar_styles.css")

    add(
        results,
        "CSS خارجي: وجود masar_styles.css",
        "PASS" if css_path.exists() else "FAIL",
        f"المسار: {css_path}" if css_path.exists() else f"غير موجود: {css_path}",
    )

    add(
        results,
        "CSS خارجي: دالة load_masar_css",
        "PASS" if "def load_masar_css" in app_text else "FAIL",
        "الدالة موجودة في app.py." if "def load_masar_css" in app_text else "الدالة غير موجودة.",
    )

    add(
        results,
        "CSS خارجي: قراءة masar_styles.css داخل app.py",
        "PASS" if "masar_styles.css" in app_text and "open(css_path" in app_text else "FAIL",
        "app.py يقرأ ملف CSS الخارجي." if "masar_styles.css" in app_text and "open(css_path" in app_text else "لم يظهر نمط القراءة المتوقع.",
    )

    add(
        results,
        "CSS خارجي: تمرير css=css إلى app.launch",
        "PASS" if re.search(r"app\.launch\(\s*\n\s*css\s*=\s*css", app_text) else "FAIL",
        "app.launch يستخدم css=css." if re.search(r"app\.launch\(\s*\n\s*css\s*=\s*css", app_text) else "لم يظهر css=css في app.launch.",
    )

    add(
        results,
        "CSS خارجي: عدم بقاء MASAR_CSS الداخلي",
        "PASS" if "MASAR_CSS" not in app_text else "FAIL",
        "لا يوجد MASAR_CSS داخل app.py." if "MASAR_CSS" not in app_text else "وجد MASAR_CSS داخل app.py.",
    )

    critical_css_markers = [
        "masar-arrow-fix",
        "masar-accordion-arrow-fix",
        "absent-box",
        "masar-file-upload-right",
        "masar-date-label-right",
        "school-data-panel-box",
    ]
    missing = [marker for marker in critical_css_markers if marker not in style_text]
    add(
        results,
        "CSS خارجي: العلامات الحرجة موجودة في ملف CSS",
        "PASS" if not missing else "FAIL",
        "كل العلامات الحرجة موجودة." if not missing else f"ناقص: {missing}",
    )


def check_config_phase3a(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص مرحلة config.py الآمنة: نقل الثوابت الخام فقط دون القيم الديناميكية."""
    config_path = app_path.with_name("config.py")
    storage_path = app_path.with_name("storage.py")
    storage_text = storage_path.read_text(encoding="utf-8") if storage_path.exists() else ""
    runtime_text = app_text + "\n" + storage_text

    if not config_path.exists():
        add(results, "config.py: وجود الملف", "FAIL", f"غير موجود: {config_path}")
        return

    config_text = config_path.read_text(encoding="utf-8")
    add(results, "config.py: وجود الملف", "PASS", f"موجود: {config_path}")

    imports_config = "from config import" in app_text or "from config import" in storage_text
    add(
        results,
        "config.py: استيراد الثوابت من config",
        "PASS" if imports_config else "FAIL",
        "app.py أو storage.py يستورد من config.py." if imports_config else "لم يظهر from config import.",
    )

    required_config_markers = [
        "APP_DIR",
        "DEFAULT_SCHOOL_CONFIG",
        "SCHEDULE_FILE_NAMES",
        "SCHOOL_CONFIG_FILENAME",
        "DB_FILENAME",
    ]
    missing_required = [marker for marker in required_config_markers if marker not in config_text]
    add(
        results,
        "config.py: الثوابت الخام الأساسية موجودة",
        "PASS" if not missing_required else "FAIL",
        "الثوابت الأساسية موجودة." if not missing_required else f"ناقص: {missing_required}",
    )

    forbidden_dynamic_patterns = [
        r"def\s+load_school_config\s*\(",
        r"def\s+safe_write_json\s*\(",
        r"^\s*SCHOOL_CONFIG\s*=",
        r"^\s*MAX_PERIODS\s*=",
        r"^\s*OFFICIAL_DEPTS\s*=",
    ]
    offenders = []
    for pattern in forbidden_dynamic_patterns:
        offenders.extend(line_numbers_for_pattern(config_text, pattern))
    add(
        results,
        "config.py: لا يحتوي قيمًا ديناميكية أو دوال تخزين",
        "PASS" if not offenders else "FAIL",
        "لا توجد تعريفات runtime ممنوعة في config.py." if not offenders else f"وجدت مخالفات في الأسطر: {offenders[:10]}",
    )

    runtime_markers = [
        "def load_school_config",
        "SCHOOL_CONFIG = load_school_config()",
        "MAX_PERIODS",
        "OFFICIAL_DEPTS",
    ]
    missing_runtime = [marker for marker in runtime_markers if marker not in runtime_text]
    add(
        results,
        "runtime config: القيم الديناميكية خارج config.py",
        "PASS" if not missing_runtime else "FAIL",
        "load_school_config وSCHOOL_CONFIG وMAX_PERIODS وOFFICIAL_DEPTS موجودة خارج config.py." if not missing_runtime else f"ناقص: {missing_runtime}",
    )

    imported_markers = [
        "DEFAULT_SCHOOL_CONFIG",
        "SCHEDULE_FILE_NAMES",
        "SCHOOL_CONFIG_FILENAME",
        "DB_FILENAME",
    ]
    missing_imported = [marker for marker in imported_markers if marker not in runtime_text]
    add(
        results,
        "app.py/storage.py: يستخدمان ثوابت config الأساسية",
        "PASS" if not missing_imported else "FAIL",
        "ثوابت config الأساسية مستخدمة في app.py أو storage.py." if not missing_imported else f"ناقص: {missing_imported}",
    )


def check_storage_phase3b(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص مرحلة storage.py: طبقة التخزين + إعدادات التشغيل الديناميكية حتى Phase 3C."""
    storage_path = app_path.with_name("storage.py")
    if not storage_path.exists():
        add(results, "storage.py: وجود الملف", "FAIL", f"غير موجود: {storage_path}")
        return

    storage_text = storage_path.read_text(encoding="utf-8")
    add(results, "storage.py: وجود الملف", "PASS", f"موجود: {storage_path}")

    required_storage_markers = [
        "def safe_write_json",
        "def ensure_data_directories",
        "def state_locked",
        "def _get_json_file_lock",
        "DATA_DIR",
        "SCHEDULE_FILES",
        "AUTH_DB_FILE",
    ]
    missing_storage = [marker for marker in required_storage_markers if marker not in storage_text]
    add(
        results,
        "storage.py: عناصر التخزين الأساسية موجودة",
        "PASS" if not missing_storage else "FAIL",
        "عناصر التخزين الأساسية موجودة." if not missing_storage else f"ناقص: {missing_storage}",
    )

    app_storage_imports = [
        "from storage import",
        "safe_write_json",
        "ensure_data_directories",
        "state_locked",
        "SCHEDULE_FILES",
    ]
    missing_app_imports = [marker for marker in app_storage_imports if marker not in app_text]
    add(
        results,
        "app.py: يستورد طبقة storage",
        "PASS" if not missing_app_imports else "FAIL",
        "app.py يستورد عناصر storage المطلوبة." if not missing_app_imports else f"ناقص: {missing_app_imports}",
    )

    forbidden_in_app = [
        r"^def\s+safe_write_json\s*\(",
        r"^def\s+_probe_writable_directory\s*\(",
        r"^def\s+ensure_data_directories\s*\(",
        r"^\s*DATA_DIR\s*=",
        r"^\s*SCHEDULE_FILES\s*=",
    ]
    app_offenders = []
    for pattern in forbidden_in_app:
        app_offenders.extend(line_numbers_for_pattern(app_text, pattern))
    add(
        results,
        "app.py: لا يحتوي تعريفات storage المنقولة",
        "PASS" if not app_offenders else "FAIL",
        "تعريفات storage المنقولة غير موجودة داخل app.py." if not app_offenders else f"وجدت في الأسطر: {app_offenders[:10]}",
    )

    runtime_storage_markers = [
        "def load_school_config",
        "SCHOOL_CONFIG = load_school_config()",
        "MAX_PERIODS = _coerce_runtime_periods_per_day(SCHOOL_CONFIG)",
        "SCHOOL_WEEK_DAYS = list(",
        "SCHOOL_WEEKEND_DAYS = list(",
        "OFFICIAL_DEPTS = list(",
    ]
    missing_runtime_storage = [marker for marker in runtime_storage_markers if marker not in storage_text]
    add(
        results,
        "storage.py: يحتوي إعدادات التشغيل الديناميكية Phase 3C",
        "PASS" if not missing_runtime_storage else "FAIL",
        "تم نقل load_school_config وSCHOOL_CONFIG وMAX_PERIODS وOFFICIAL_DEPTS إلى storage.py." if not missing_runtime_storage else f"ناقص: {missing_runtime_storage}",
    )

    forbidden_runtime_in_app = [
        r"^def\s+load_school_config\s*\(",
        r"^SCHOOL_CONFIG\s*=\s*load_school_config\s*\(",
        r"^\s*MAX_PERIODS\s*=\s*int\(",
        r"^\s*OFFICIAL_DEPTS\s*=\s*list\(",
    ]
    app_runtime_offenders = []
    for pattern in forbidden_runtime_in_app:
        app_runtime_offenders.extend(line_numbers_for_pattern(app_text, pattern))
    add(
        results,
        "app.py: لا يحتوي تعريفات إعدادات التشغيل المنقولة",
        "PASS" if not app_runtime_offenders else "FAIL",
        "تعريفات load_school_config/MAX_PERIODS/OFFICIAL_DEPTS المنقولة غير موجودة داخل app.py." if not app_runtime_offenders else f"وجدت في الأسطر: {app_runtime_offenders[:10]}",
    )

    app_runtime_imports = [
        "load_school_config",
        "SCHOOL_CONFIG",
        "MAX_PERIODS",
        "SCHOOL_WEEK_DAYS",
        "SCHOOL_WEEKEND_DAYS",
        "OFFICIAL_DEPTS",
    ]
    missing_runtime_imports = [marker for marker in app_runtime_imports if marker not in app_text]
    add(
        results,
        "app.py: يستورد إعدادات التشغيل من storage.py",
        "PASS" if not missing_runtime_imports else "FAIL",
        "app.py يستورد إعدادات التشغيل الديناميكية من storage.py." if not missing_runtime_imports else f"ناقص: {missing_runtime_imports}",
    )

    storage_runtime_integrity = all(
        marker in storage_text
        for marker in ["DEFAULT_SCHOOL_CONFIG", "SCHOOL_CONFIG_FILE", "safe_write_json", "_coerce_runtime_periods_per_day"]
    )
    add(
        results,
        "storage.py: تحميل إعدادات المدرسة يستخدم التخزين الآمن",
        "PASS" if storage_runtime_integrity else "FAIL",
        "load_school_config يعتمد DEFAULT_SCHOOL_CONFIG وSCHOOL_CONFIG_FILE وsafe_write_json." if storage_runtime_integrity else "نمط تحميل إعدادات المدرسة غير مكتمل.",
    )

    add(
        results,
        "storage.py: يستورد من config.py",
        "PASS" if "from config import" in storage_text else "FAIL",
        "storage.py يستورد الثوابت الخام من config.py." if "from config import" in storage_text else "لم يظهر from config import داخل storage.py.",
    )



def check_auth_phase3d(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص مرحلة auth.py: فصل الحسابات والصلاحيات دون نقل ربط Gradio."""
    auth_path = app_path.with_name("auth.py")
    if not auth_path.exists():
        add(results, "auth.py: وجود الملف", "FAIL", f"غير موجود: {auth_path}")
        return

    auth_text = auth_path.read_text(encoding="utf-8")
    add(results, "auth.py: وجود الملف", "PASS", f"موجود: {auth_path}")

    required_auth_markers = [
        "OWNER_ROLE",
        "SHARED_TEACHER_ROLE",
        "def load_auth_db",
        "def authenticate_login_pin",
        "def load_auth_accounts",
        "def save_auth_accounts",
        "def get_auth_account_choices",
        "def get_permissions",
        "def get_ui_visibility_updates",
    ]
    missing_auth = [marker for marker in required_auth_markers if marker not in auth_text]
    add(
        results,
        "auth.py: عناصر الحسابات والصلاحيات الأساسية موجودة",
        "PASS" if not missing_auth else "FAIL",
        "عناصر auth الأساسية موجودة." if not missing_auth else f"ناقص: {missing_auth}",
    )

    app_auth_imports = [
        "from auth import",
        "authenticate_login_pin",
        "load_auth_accounts",
        "save_auth_accounts",
        "get_permissions",
        "OWNER_ACCOUNT_ID",
        "OWNER_ROLE",
        "SHARED_TEACHER_ROLE",
    ]
    missing_imports = [marker for marker in app_auth_imports if marker not in app_text]
    add(
        results,
        "app.py: يستورد طبقة auth",
        "PASS" if not missing_imports else "FAIL",
        "app.py يستورد عناصر auth المطلوبة." if not missing_imports else f"ناقص: {missing_imports}",
    )

    moved_auth_patterns = [
        r"^def\s+load_auth_db\s*\(",
        r"^def\s+authenticate_login_pin\s*\(",
        r"^def\s+load_auth_accounts\s*\(",
        r"^def\s+save_auth_accounts\s*\(",
        r"^def\s+get_auth_account_choices\s*\(",
        r"^def\s+get_permissions\s*\(",
        r"^def\s+get_ui_visibility_updates\s*\(",
        r"^PIN_HASH_ALGORITHM\s*=",
        r"^AUTH_ACCOUNTS_VERSION\s*=",
        r"^OWNER_ACCOUNT_ID\s*=",
    ]
    app_offenders: list[int] = []
    for pattern in moved_auth_patterns:
        app_offenders.extend(line_numbers_for_pattern(app_text, pattern))
    add(
        results,
        "app.py: لا يحتوي تعريفات auth المنقولة",
        "PASS" if not app_offenders else "FAIL",
        "تعريفات auth المنقولة غير موجودة داخل app.py." if not app_offenders else f"وجدت في الأسطر: {app_offenders[:10]}",
    )

    no_gradio_in_auth = (
        "import gradio" not in auth_text
        and "gr." not in auth_text
        and "Blocks(" not in auth_text
        and ".click(" not in auth_text
        and ".change(" not in auth_text
        and ".submit(" not in auth_text
    )
    add(
        results,
        "auth.py: لا يحتوي ربط Gradio",
        "PASS" if no_gradio_in_auth else "FAIL",
        "auth.py خالٍ من مكونات وربط Gradio." if no_gradio_in_auth else "ظهر أثر Gradio داخل auth.py؛ هذا ممنوع في Phase 3D.",
    )

    storage_import_ok = "from storage import" in auth_text and "AUTH_ACCOUNTS_FILE" in auth_text and "safe_write_json" in auth_text
    add(
        results,
        "auth.py: يستخدم storage للتخزين",
        "PASS" if storage_import_ok else "FAIL",
        "auth.py يستورد ملفات الحسابات والحفظ الآمن من storage.py." if storage_import_ok else "استيراد storage داخل auth.py غير مكتمل.",
    )

    login_binding_preserved = (
        "login_btn.click" in app_text
        and "pin_input.submit" in app_text
        and "attempt_login" in app_text
    )
    add(
        results,
        "app.py: ربط تسجيل الدخول بقي في app.py",
        "PASS" if login_binding_preserved else "FAIL",
        "ربط Gradio لتسجيل الدخول ما زال داخل app.py." if login_binding_preserved else "لم تظهر روابط login_btn/pin_input/attempt_login كما ينبغي.",
    )


def check_state_phase3e_pre(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-pre: نقل الحالة العامة إلى storage.py مع الحفاظ على مراجع الكائنات."""
    storage_path = app_path.with_name("storage.py")
    if not storage_path.exists():
        add(results, "3E-pre: وجود storage.py", "FAIL", f"غير موجود: {storage_path}")
        return

    storage_text = storage_path.read_text(encoding="utf-8")
    state_names = ["teachers_db", "daily_db", "processed_absences", "swap_db", "exemptions_log"]

    missing_state = [name for name in state_names if not re.search(rf"^{name}\s*=", storage_text, re.MULTILINE)]
    add(
        results,
        "3E-pre: الحالة العامة معرفة في storage.py",
        "PASS" if not missing_state else "FAIL",
        "teachers_db/daily_db/processed_absences/swap_db/exemptions_log معرفة في storage.py." if not missing_state else f"ناقص: {missing_state}",
    )

    missing_app_state_imports = [name for name in state_names if name not in app_text]
    add(
        results,
        "3E-pre: app.py يستخدم الحالة المستوردة من storage.py",
        "PASS" if not missing_app_state_imports and "from storage import" in app_text else "FAIL",
        "app.py يحتوي استيراد الحالة العامة من storage.py." if not missing_app_state_imports and "from storage import" in app_text else f"ناقص أو غير واضح: {missing_app_state_imports}",
    )

    moved_storage_functions = [
        "save_db",
        "load_db",
        "save_daily_db",
        "load_daily_db",
        "save_swap_db",
        "load_swap_db",
        "save_exemptions_log",
        "load_exemptions_log",
    ]
    missing_storage_funcs = [fn for fn in moved_storage_functions if f"def {fn}" not in storage_text]
    add(
        results,
        "3E-pre: دوال الحفظ والتحميل موجودة في storage.py",
        "PASS" if not missing_storage_funcs else "FAIL",
        "دوال save/load الأساسية موجودة في storage.py." if not missing_storage_funcs else f"ناقص: {missing_storage_funcs}",
    )

    app_moved_func_lines = []
    for fn in moved_storage_functions:
        app_moved_func_lines.extend(line_numbers_for_pattern(app_text, rf"^def\s+{re.escape(fn)}\s*\("))
    add(
        results,
        "3E-pre: app.py لا يحتوي دوال التخزين المنقولة",
        "PASS" if not app_moved_func_lines else "FAIL",
        "دوال save/load المنقولة غير معرفة داخل app.py." if not app_moved_func_lines else f"وجدت في الأسطر: {app_moved_func_lines[:10]}",
    )

    app_state_reassign = []
    for name in state_names:
        app_state_reassign.extend(line_numbers_for_pattern(app_text, rf"^\s*{name}\s*="))
    add(
        results,
        "3E-pre: منع إعادة تعريف الحالة داخل app.py",
        "PASS" if not app_state_reassign else "FAIL",
        "لا توجد إعادة تعيين مباشرة للحالة العامة داخل app.py." if not app_state_reassign else f"وجدت في الأسطر: {app_state_reassign[:10]}",
    )

    storage_state_reassign = []
    for name in state_names:
        # يسمح بالتعريف الأولي top-level فقط، ويمنع أي assignment داخل دالة/كتلة بعد مسافة بادئة.
        storage_state_reassign.extend(line_numbers_for_pattern(storage_text, rf"^\s+{name}\s*="))
    add(
        results,
        "3E-pre: منع إعادة تعيين الحالة داخل دوال storage.py",
        "PASS" if not storage_state_reassign else "FAIL",
        "لا توجد إعادة تعيين مباشرة للحالة داخل دوال storage.py." if not storage_state_reassign else f"وجدت في الأسطر: {storage_state_reassign[:10]}",
    )

    loaders_requirements = {
        "load_db": ["teachers_db.clear()", "teachers_db.update("],
        "load_daily_db": ["daily_db.clear()", "daily_db.extend(", "processed_absences.clear()", "processed_absences.update("],
        "load_swap_db": ["swap_db.clear()", "swap_db.update("],
        "load_exemptions_log": ["exemptions_log.clear()", "exemptions_log.extend("],
    }
    missing_loader_patterns = []
    for fn, markers in loaders_requirements.items():
        body = function_body(storage_text, fn)
        if not body:
            missing_loader_patterns.append(f"{fn}: الدالة غير موجودة")
            continue
        for marker in markers:
            if marker not in body:
                missing_loader_patterns.append(f"{fn}: ناقص {marker}")
    add(
        results,
        "3E-pre: دوال load تستخدم in-place mutation",
        "PASS" if not missing_loader_patterns else "FAIL",
        "load_db/load_daily_db/load_swap_db/load_exemptions_log تحافظ على هوية الكائنات." if not missing_loader_patterns else "; ".join(missing_loader_patterns[:8]),
    )

    clear_body = function_body(app_text, "clear_all_data")
    clear_markers = ["teachers_db.clear()", "daily_db.clear()", "processed_absences.clear()"]
    missing_clear = [marker for marker in clear_markers if marker not in clear_body]
    add(
        results,
        "3E-pre: clear_all_data يستخدم clear بدل reassignment",
        "PASS" if clear_body and not missing_clear else "FAIL",
        "clear_all_data يحافظ على مراجع teachers_db/daily_db/processed_absences." if clear_body and not missing_clear else f"ناقص: {missing_clear or ['clear_all_data غير موجودة']}",
    )

    no_reverse_import = all(marker not in storage_text for marker in ["import app", "from app import"])
    add(
        results,
        "3E-pre: storage.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من storage.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل storage.py.",
    )


def check_school_data_phase3ea(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-a: فصل دوال مركز البيانات النظيفة إلى school_data.py دون اعتماد عكسي على app.py."""
    school_path = app_path.with_name("school_data.py")
    if not school_path.exists():
        add(results, "3E-a: وجود school_data.py", "FAIL", f"غير موجود: {school_path}")
        return

    school_text = school_path.read_text(encoding="utf-8")
    add(results, "3E-a: وجود school_data.py", "PASS", f"موجود: {school_path}")

    required_functions = [
        "load_reference_status_registry",
        "save_reference_status_registry",
        "update_reference_file_status",
        "_reference_status_key",
        "get_reference_file_status",
        "dept_has_loaded_schedule_data",
        "get_school_data_center_status",
        "render_reference_file_card",
        "render_admin_reference_card",
        "render_phones_reference_card",
        "render_schedule_reference_cards",
        "save_admin_reference_file",
        "save_phones_reference_file",
        "save_schedule_reference_file",
        "precheck_schedule_excel_template",
        "render_schedule_precheck_error_html",
        "validate_reference_filename",
        "_normalize_schedule_header_text",
        "_excel_column_label_zero_based",
    ]
    missing = [fn for fn in required_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", school_text, re.MULTILINE)]
    add(
        results,
        "3E-a: الدوال النظيفة موجودة في school_data.py",
        "PASS" if not missing else "FAIL",
        "جميع دوال مركز البيانات النظيفة موجودة." if not missing else f"ناقص: {missing}",
    )

    constant_ok = "SCHEDULE_PERIOD_HEADER_WORDS" in school_text
    add(
        results,
        "3E-a: ثابت عناوين الحصص موجود في school_data.py",
        "PASS" if constant_ok else "FAIL",
        "SCHEDULE_PERIOD_HEADER_WORDS موجود." if constant_ok else "SCHEDULE_PERIOD_HEADER_WORDS غير موجود.",
    )

    app_local_defs = []
    for fn in required_functions:
        app_local_defs.extend(line_numbers_for_pattern(app_text, rf"^def\s+{re.escape(fn)}\s*\("))
    add(
        results,
        "3E-a: app.py لا يحتوي تعريفات الدوال المنقولة",
        "PASS" if not app_local_defs else "FAIL",
        "الدوال المنقولة غير معرفة داخل app.py." if not app_local_defs else f"وجدت في الأسطر: {app_local_defs[:10]}",
    )

    # بعد 3F-a و3G-a أصبحت دوال الاختيارات/جدول اليوم/الأرصدة في وحدات نظيفة،
    # لذلك يبقى المنع موجهاً فقط للدوال Gradio-bound التي ما زالت في app.py.
    dangerous = [
        "delete_department_data",
        "get_day_table_updates",
        "process_uploaded_excel",
    ]
    found_dangerous = [
        name for name in dangerous
        if re.search(rf"\b{re.escape(name)}\s*\(", school_text)
    ]
    add(
        results,
        "3E-a/3E-b: school_data.py لا يستدعي دوال app المتبقية",
        "PASS" if not found_dangerous else "FAIL",
        "لا توجد دوال app المتبقية داخل school_data.py." if not found_dangerous else f"وجد: {found_dangerous}",
    )

    no_reverse_import = all(marker not in school_text for marker in ["import app", "from app import"])
    add(
        results,
        "3E-a: school_data.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من school_data.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل school_data.py.",
    )

    deferred_functions = [
        "refresh_admins_from_reference",
        "refresh_phones_from_reference",
        "refresh_schedule_from_reference",
        "process_uploaded_excel",
    ]
    missing_in_app = [fn for fn in deferred_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    wrongly_in_school = [fn for fn in deferred_functions if re.search(rf"^def\s+{re.escape(fn)}\s*\(", school_text, re.MULTILINE)]
    add(
        results,
        "3E-a: الدوال المؤجلة بقيت في app.py",
        "PASS" if not missing_in_app and not wrongly_in_school else "FAIL",
        "refresh/process ودوال تحديث الإداريين/الأرقام بقيت في app.py كما هو مخطط." if not missing_in_app and not wrongly_in_school else f"ناقص في app: {missing_in_app}; موجود خطأ في school_data: {wrongly_in_school}",
    )

    import_ok = "from school_data import" in app_text
    add(
        results,
        "3E-a: app.py يستورد school_data.py",
        "PASS" if import_ok else "FAIL",
        "app.py يستورد دوال مركز البيانات من school_data.py." if import_ok else "لم يظهر from school_data import داخل app.py.",
    )


def check_schedules_phase3fa(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3F-a: فصل دوال الجداول والاختيارات النظيفة إلى schedules.py دون Gradio أو اعتماد عكسي."""
    schedules_path = app_path.with_name("schedules.py")
    if not schedules_path.exists():
        add(results, "3F-a: وجود schedules.py", "FAIL", f"غير موجود: {schedules_path}")
        return

    schedules_text = schedules_path.read_text(encoding="utf-8")
    add(results, "3F-a: وجود schedules.py", "PASS", f"موجود: {schedules_path}")

    required_functions = [
        "get_teacher_choices",
        "get_absentee_choices",
        "resolve_effective_dept",
        "clean_teacher_name",
        "get_name_fingerprint",
        "extract_class_info",
        "get_day_overview",
        "format_teacher_name",
        "get_day_dept_style",
        "render_day_department_section_html",
        "render_day_all_departments_html",
        "render_day_table_html",
    ]
    missing = [fn for fn in required_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", schedules_text, re.MULTILINE)]
    add(
        results,
        "3F-a: الدوال النظيفة موجودة في schedules.py",
        "PASS" if not missing else "FAIL",
        "الدوال الـ12 موجودة في schedules.py." if not missing else f"ناقص: {missing}",
    )

    required_constants = ["DAY_DEPT_STYLE_MAP", "DAY_DEPT_FALLBACK_STYLES"]
    missing_constants = [name for name in required_constants if not re.search(rf"^{re.escape(name)}\s*=", schedules_text, re.MULTILINE)]
    add(
        results,
        "3F-a: ثوابت تلوين جدول اليوم موجودة في schedules.py",
        "PASS" if not missing_constants else "FAIL",
        "الثابتان موجودان في schedules.py." if not missing_constants else f"ناقص: {missing_constants}",
    )

    app_local_defs = []
    for fn in required_functions:
        app_local_defs.extend(line_numbers_for_pattern(app_text, rf"^def\s+{re.escape(fn)}\s*\("))
    add(
        results,
        "3F-a: app.py لا يحتوي تعريفات دوال schedules المنقولة",
        "PASS" if not app_local_defs else "FAIL",
        "الدوال المنقولة غير معرفة داخل app.py." if not app_local_defs else f"وجدت في الأسطر: {app_local_defs[:10]}",
    )

    app_local_constants = []
    for name in required_constants:
        app_local_constants.extend(line_numbers_for_pattern(app_text, rf"^{re.escape(name)}\s*="))
    add(
        results,
        "3F-a: app.py لا يحتوي ثوابت جدول اليوم المنقولة",
        "PASS" if not app_local_constants else "FAIL",
        "الثوابت المنقولة غير معرفة داخل app.py." if not app_local_constants else f"وجدت في الأسطر: {app_local_constants[:10]}",
    )

    no_reverse_import = all(marker not in schedules_text for marker in ["import app", "from app import"])
    add(
        results,
        "3F-a: schedules.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من schedules.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل schedules.py.",
    )

    no_gradio = "gr.update" not in schedules_text and "import gradio" not in schedules_text and "from gradio" not in schedules_text
    add(
        results,
        "3F-a: schedules.py بلا Gradio",
        "PASS" if no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio داخل schedules.py." if no_gradio else "ظهر اعتماد مباشر على Gradio داخل schedules.py.",
    )

    deferred_functions = [
        "refresh_schedule_from_reference",
        "process_uploaded_excel",
    ]
    missing_in_app = [fn for fn in deferred_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    wrongly_in_schedules = [fn for fn in deferred_functions if re.search(rf"^def\s+{re.escape(fn)}\s*\(", schedules_text, re.MULTILINE)]
    add(
        results,
        "3F-a: الدوال الثقيلة المؤجلة بقيت في app.py",
        "PASS" if not missing_in_app and not wrongly_in_schedules else "FAIL",
        "refresh/process بقيتا في app.py كما هو مخطط." if not missing_in_app and not wrongly_in_schedules else f"ناقص في app: {missing_in_app}; موجود خطأ في schedules: {wrongly_in_schedules}",
    )

    app_import_ok = "from schedules import" in app_text
    add(
        results,
        "3F-a: app.py يستورد schedules.py",
        "PASS" if app_import_ok else "FAIL",
        "app.py يستورد دوال الجداول والاختيارات من schedules.py." if app_import_ok else "لم يظهر from schedules import داخل app.py.",
    )

    try:
        py_compile.compile(str(schedules_path), doraise=True)
        add(results, "3F-a: py_compile schedules.py", "PASS", "schedules.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3F-a: py_compile schedules.py", "FAIL", f"فشل py_compile: {exc}")


def check_balances_phase3ga(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3G-a: فصل دوال الأرصدة والغياب والتقصير النظيفة إلى balances.py."""
    balances_path = app_path.with_name("balances.py")
    if not balances_path.exists():
        add(results, "3G-a: وجود balances.py", "FAIL", f"غير موجود: {balances_path}")
        return

    balances_text = balances_path.read_text(encoding="utf-8")
    add(results, "3G-a: وجود balances.py", "PASS", f"موجود: {balances_path}")

    required_functions = [
        "get_updated_balance",
        "get_updated_absences",
        "get_updated_shortcomings",
        "render_compact_rtl_table_html",
    ]
    missing = [fn for fn in required_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", balances_text, re.MULTILINE)]
    add(
        results,
        "3G-a: دوال الأرصدة موجودة في balances.py",
        "PASS" if not missing else "FAIL",
        "الدوال الأربع موجودة في balances.py." if not missing else f"ناقص: {missing}",
    )

    app_local_defs = []
    for fn in required_functions:
        app_local_defs.extend(line_numbers_for_pattern(app_text, rf"^def\s+{re.escape(fn)}\s*\("))
    add(
        results,
        "3G-a: app.py لا يحتوي تعريفات دوال balances المنقولة",
        "PASS" if not app_local_defs else "FAIL",
        "الدوال المنقولة غير معرفة داخل app.py." if not app_local_defs else f"وجدت في الأسطر: {app_local_defs[:10]}",
    )

    no_reverse_import = all(marker not in balances_text for marker in ["import app", "from app import"])
    add(
        results,
        "3G-a: balances.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من balances.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل balances.py.",
    )

    no_gradio = "gr.update" not in balances_text and "import gradio" not in balances_text and "from gradio" not in balances_text
    add(
        results,
        "3G-a: balances.py بلا Gradio",
        "PASS" if no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio داخل balances.py." if no_gradio else "ظهر اعتماد مباشر على Gradio داخل balances.py.",
    )

    required_import_markers = [
        "from schedules import",
        "resolve_effective_dept",
        "format_teacher_name",
        "from storage import teachers_db",
    ]
    missing_import_markers = [marker for marker in required_import_markers if marker not in balances_text]
    add(
        results,
        "3G-a: balances.py يعتمد على schedules/storage/config فقط",
        "PASS" if not missing_import_markers else "FAIL",
        "اعتماد balances.py نظيف على schedules/storage/config." if not missing_import_markers else f"مؤشرات ناقصة: {missing_import_markers}",
    )

    forbidden_calls = [
        "refresh_schedule_from_reference",
        "process_uploaded_excel",
        "get_absentee_choices",
        "get_teacher_choices",
        "get_day_overview",
    ]
    found_forbidden = [fn for fn in forbidden_calls if fn in balances_text]
    add(
        results,
        "3G-a: balances.py لا يستدعي دوال مؤجلة أو واجهة",
        "PASS" if not found_forbidden else "FAIL",
        "لا توجد دوال مؤجلة/واجهة داخل balances.py." if not found_forbidden else f"وجد: {found_forbidden}",
    )

    deferred_functions = [
        "refresh_schedule_from_reference",
        "process_uploaded_excel",
    ]
    missing_in_app = [fn for fn in deferred_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    wrongly_in_balances = [fn for fn in deferred_functions if re.search(rf"^def\s+{re.escape(fn)}\s*\(", balances_text, re.MULTILINE)]
    add(
        results,
        "3G-a: refresh/process باقيتان في app.py",
        "PASS" if not missing_in_app and not wrongly_in_balances else "FAIL",
        "refresh_schedule_from_reference و process_uploaded_excel بقيتا في app.py." if not missing_in_app and not wrongly_in_balances else f"ناقص في app: {missing_in_app}; موجود خطأ في balances: {wrongly_in_balances}",
    )

    app_import_ok = "from balances import" in app_text
    add(
        results,
        "3G-a: app.py يستورد balances.py",
        "PASS" if app_import_ok else "FAIL",
        "app.py يستورد دوال الأرصدة من balances.py." if app_import_ok else "لم يظهر from balances import داخل app.py.",
    )

    try:
        py_compile.compile(str(balances_path), doraise=True)
        add(results, "3G-a: py_compile balances.py", "PASS", "balances.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3G-a: py_compile balances.py", "FAIL", f"فشل py_compile: {exc}")


def check_time_helper_phase3eb1(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-b-1: نقل get_now_oman إلى storage.py كدالة وقت عامة."""
    storage_path = app_path.with_name("storage.py")
    if not storage_path.exists():
        add(results, "3E-b-1: وجود storage.py", "FAIL", f"غير موجود: {storage_path}")
        return

    storage_text = storage_path.read_text(encoding="utf-8")

    in_storage = bool(re.search(r"^def\s+get_now_oman\s*\(", storage_text, re.MULTILINE))
    add(
        results,
        "3E-b-1: get_now_oman موجودة في storage.py",
        "PASS" if in_storage else "FAIL",
        "تم نقل get_now_oman إلى storage.py." if in_storage else "لم يتم العثور على get_now_oman في storage.py.",
    )

    local_in_app = bool(re.search(r"^def\s+get_now_oman\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3E-b-1: app.py لا يعرّف get_now_oman محليًا",
        "PASS" if not local_in_app else "FAIL",
        "لا يوجد تعريف محلي للدالة داخل app.py." if not local_in_app else "ما زال app.py يحتوي def get_now_oman.",
    )

    imported_in_app = "get_now_oman" in app_text and "from storage import" in app_text
    add(
        results,
        "3E-b-1: app.py يستورد get_now_oman من storage.py",
        "PASS" if imported_in_app else "FAIL",
        "app.py يستخدم get_now_oman المستوردة من storage.py." if imported_in_app else "لم يظهر استيراد get_now_oman من storage.py بوضوح.",
    )

    no_reverse_import = "import app" not in storage_text and "from app import" not in storage_text
    add(
        results,
        "3E-b-1: storage.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من storage.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل storage.py.",
    )

    try:
        py_compile.compile(str(storage_path), doraise=True)
        add(results, "3E-b-1: py_compile storage.py", "PASS", "storage.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3E-b-1: py_compile storage.py", "FAIL", f"فشل py_compile: {exc}")


def _function_return_tuple_lengths(source_text: str, func_name: str) -> list[int | str]:
    try:
        module = ast.parse(source_text)
    except SyntaxError:
        return ["syntax_error"]
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            lengths: list[int | str] = []
            for item in ast.walk(node):
                if isinstance(item, ast.Return):
                    if isinstance(item.value, ast.Tuple):
                        lengths.append(len(item.value.elts))
                    else:
                        lengths.append(type(item.value).__name__)
            return lengths
    return ["missing"]


def _function_decorators(source_text: str, func_name: str) -> list[str]:
    try:
        module = ast.parse(source_text)
    except SyntaxError:
        return []
    for node in module.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return [ast.unparse(decorator) for decorator in node.decorator_list]
    return []


def check_refresh_schedule_core_phase3eb2(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-b-2: تقسيم refresh_schedule_from_reference إلى core/wrapper بعقد إرجاع ثابت."""
    school_path = app_path.with_name("school_data.py")
    if not school_path.exists():
        add(results, "3E-b-2: وجود school_data.py", "FAIL", f"غير موجود: {school_path}")
        return

    school_text = school_path.read_text(encoding="utf-8")
    core_body = function_body(school_text, "refresh_schedule_from_reference_core")
    wrapper_body = function_body(app_text, "refresh_schedule_from_reference")

    add(
        results,
        "3E-b-2: core موجودة في school_data.py",
        "PASS" if core_body else "FAIL",
        "refresh_schedule_from_reference_core موجودة." if core_body else "refresh_schedule_from_reference_core غير موجودة.",
    )
    add(
        results,
        "3E-b-2: wrapper موجودة في app.py",
        "PASS" if wrapper_body else "FAIL",
        "refresh_schedule_from_reference موجودة في app.py كـwrapper." if wrapper_body else "refresh_schedule_from_reference غير موجودة في app.py.",
    )

    if not core_body or not wrapper_body:
        return

    core_decorators = _function_decorators(school_text, "refresh_schedule_from_reference_core")
    wrapper_decorators = _function_decorators(app_text, "refresh_schedule_from_reference")
    add(
        results,
        "3E-b-2: @state_locked على core",
        "PASS" if "state_locked" in core_decorators else "FAIL",
        f"decorators: {core_decorators}",
    )
    add(
        results,
        "3E-b-2: wrapper بلا state_locked",
        "PASS" if "state_locked" not in wrapper_decorators else "FAIL",
        f"decorators: {wrapper_decorators}",
    )

    forbidden_core_tokens = ["gr.update", "gr.Warning", "gr.Info", "import gradio"]
    found_core_tokens = [token for token in forbidden_core_tokens if token in core_body]
    add(
        results,
        "3E-b-2: core لا يحتوي Gradio مباشر",
        "PASS" if not found_core_tokens else "FAIL",
        "core خام بلا gr.update/gr.Warning/gr.Info." if not found_core_tokens else f"وجد: {found_core_tokens}",
    )

    core_lengths = _function_return_tuple_lengths(school_text, "refresh_schedule_from_reference_core")
    wrapper_lengths = _function_return_tuple_lengths(app_text, "refresh_schedule_from_reference")
    add(
        results,
        "3E-b-2: core يرجع 8 قيم خام",
        "PASS" if core_lengths and all(length == 8 for length in core_lengths) else "FAIL",
        f"أطوال return داخل core: {core_lengths}",
    )
    add(
        results,
        "3E-b-2: wrapper يرجع 9 مخرجات Gradio",
        "PASS" if wrapper_lengths and all(length == 9 for length in wrapper_lengths) else "FAIL",
        f"أطوال return داخل wrapper: {wrapper_lengths}",
    )

    wrapper_calls_core = "refresh_schedule_from_reference_core" in wrapper_body
    wrapper_has_gr = "gr.update" in wrapper_body
    add(
        results,
        "3E-b-2: wrapper يستدعي core ويغلف بـgr.update",
        "PASS" if wrapper_calls_core and wrapper_has_gr else "FAIL",
        "wrapper يستدعي core ويحتوي gr.update." if wrapper_calls_core and wrapper_has_gr else f"calls_core={wrapper_calls_core}, has_gr_update={wrapper_has_gr}",
    )

    forbidden_wrapper_logic = ["pd.read_excel", "pd.read_csv", "teachers_db", "save_db", "update_reference_file_status"]
    found_wrapper_logic = [token for token in forbidden_wrapper_logic if token in wrapper_body]
    add(
        results,
        "3E-b-2: wrapper رفيع بلا منطق قراءة/حفظ",
        "PASS" if not found_wrapper_logic else "FAIL",
        "wrapper لا يحتوي قراءة Excel أو تعديل قاعدة البيانات." if not found_wrapper_logic else f"وجد: {found_wrapper_logic}",
    )

    no_reverse_import = all(marker not in school_text for marker in ["import app", "from app import"])
    add(
        results,
        "3E-b-2: school_data.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي." if no_reverse_import else "ظهر import app أو from app import داخل school_data.py.",
    )

    process_still_app = re.search(r"^def\s+process_uploaded_excel\s*\(", app_text, re.MULTILINE) is not None
    process_not_school = re.search(r"^def\s+process_uploaded_excel\s*\(", school_text, re.MULTILINE) is None
    add(
        results,
        "3E-b-2: process_uploaded_excel بقيت مؤجلة في app.py",
        "PASS" if process_still_app and process_not_school else "FAIL",
        "process_uploaded_excel باقية في app.py ولم تُنقل بعد." if process_still_app and process_not_school else f"in_app={process_still_app}, in_school={not process_not_school}",
    )

    try:
        py_compile.compile(str(school_path), doraise=True)
        add(results, "3E-b-2: py_compile school_data.py", "PASS", "school_data.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3E-b-2: py_compile school_data.py", "FAIL", f"فشل py_compile: {exc}")


def check_gradio_bound_helpers_phase3eb3(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-b-3: تقسيم delete_department_data و get_day_table_updates إلى core/wrapper."""
    school_path = app_path.with_name("school_data.py")
    schedules_path = app_path.with_name("schedules.py")
    if not school_path.exists() or not schedules_path.exists():
        add(results, "3E-b-3: وجود school_data.py و schedules.py", "FAIL", "أحد الملفين غير موجود.")
        return

    school_text = school_path.read_text(encoding="utf-8")
    schedules_text = schedules_path.read_text(encoding="utf-8")

    delete_core_body = function_body(school_text, "delete_department_data_core")
    delete_wrapper_body = function_body(app_text, "delete_department_data")
    day_core_body = function_body(schedules_text, "get_day_table_updates_core")
    day_wrapper_body = function_body(app_text, "get_day_table_updates")

    add(
        results,
        "3E-b-3: delete_department_data_core موجودة في school_data.py",
        "PASS" if delete_core_body else "FAIL",
        "delete_department_data_core موجودة." if delete_core_body else "delete_department_data_core غير موجودة.",
    )
    add(
        results,
        "3E-b-3: delete_department_data wrapper موجودة في app.py",
        "PASS" if delete_wrapper_body else "FAIL",
        "delete_department_data باقية في app.py كـwrapper." if delete_wrapper_body else "delete_department_data غير موجودة في app.py.",
    )
    add(
        results,
        "3E-b-3: get_day_table_updates_core موجودة في schedules.py",
        "PASS" if day_core_body else "FAIL",
        "get_day_table_updates_core موجودة." if day_core_body else "get_day_table_updates_core غير موجودة.",
    )
    add(
        results,
        "3E-b-3: get_day_table_updates wrapper موجودة في app.py",
        "PASS" if day_wrapper_body else "FAIL",
        "get_day_table_updates باقية في app.py كـwrapper." if day_wrapper_body else "get_day_table_updates غير موجودة في app.py.",
    )

    if delete_core_body:
        delete_core_decorators = _function_decorators(school_text, "delete_department_data_core")
        add(
            results,
            "3E-b-3: @state_locked على delete core",
            "PASS" if "state_locked" in delete_core_decorators else "FAIL",
            f"decorators: {delete_core_decorators}",
        )
        delete_core_tokens = [token for token in ["gr.update", "gr.Warning", "gr.Info", "import gradio"] if token in delete_core_body]
        add(
            results,
            "3E-b-3: delete core بلا Gradio مباشر",
            "PASS" if not delete_core_tokens else "FAIL",
            "delete core خام بلا gr.update/gr.Warning/gr.Info." if not delete_core_tokens else f"وجد: {delete_core_tokens}",
        )
        delete_core_lengths = _function_return_tuple_lengths(school_text, "delete_department_data_core")
        add(
            results,
            "3E-b-3: delete core يرجع 10 عناصر خام",
            "PASS" if delete_core_lengths and all(length == 10 for length in delete_core_lengths) else "FAIL",
            f"أطوال return داخل delete core: {delete_core_lengths}",
        )

    if delete_wrapper_body:
        delete_wrapper_decorators = _function_decorators(app_text, "delete_department_data")
        delete_wrapper_lengths = _function_return_tuple_lengths(app_text, "delete_department_data")
        add(
            results,
            "3E-b-3: delete wrapper بلا state_locked",
            "PASS" if "state_locked" not in delete_wrapper_decorators else "FAIL",
            f"decorators: {delete_wrapper_decorators}",
        )
        add(
            results,
            "3E-b-3: delete wrapper يرجع 10 مخرجات",
            "PASS" if delete_wrapper_lengths and all(length == 10 for length in delete_wrapper_lengths) else "FAIL",
            f"أطوال return داخل delete wrapper: {delete_wrapper_lengths}",
        )
        delete_wrapper_ok = "delete_department_data_core" in delete_wrapper_body and "gr.update" in delete_wrapper_body
        add(
            results,
            "3E-b-3: delete wrapper يستدعي core ويغلف بـgr.update",
            "PASS" if delete_wrapper_ok else "FAIL",
            f"calls_core={'delete_department_data_core' in delete_wrapper_body}, has_gr_update={'gr.update' in delete_wrapper_body}",
        )
        forbidden_delete_wrapper_logic = ["del teachers_db", "save_db", "teachers_to_delete"]
        found_delete_wrapper_logic = [token for token in forbidden_delete_wrapper_logic if token in delete_wrapper_body]
        add(
            results,
            "3E-b-3: delete wrapper رفيع بلا حذف/حفظ",
            "PASS" if not found_delete_wrapper_logic else "FAIL",
            "delete wrapper لا يحتوي حذف teachers_db أو save_db." if not found_delete_wrapper_logic else f"وجد: {found_delete_wrapper_logic}",
        )

    if day_core_body:
        day_core_decorators = _function_decorators(schedules_text, "get_day_table_updates_core")
        day_core_tokens = [token for token in ["gr.update", "gr.Warning", "gr.Info", "import gradio"] if token in day_core_body]
        add(
            results,
            "3E-b-3: day core بلا state_locked",
            "PASS" if "state_locked" not in day_core_decorators else "FAIL",
            f"decorators: {day_core_decorators}",
        )
        add(
            results,
            "3E-b-3: day core بلا Gradio مباشر",
            "PASS" if not day_core_tokens else "FAIL",
            "day core خام بلا gr.update/gr.Warning/gr.Info." if not day_core_tokens else f"وجد: {day_core_tokens}",
        )
        day_core_lengths = _function_return_tuple_lengths(schedules_text, "get_day_table_updates_core")
        add(
            results,
            "3E-b-3: day core يرجع 7 عناصر خام",
            "PASS" if day_core_lengths and all(length == 7 for length in day_core_lengths) else "FAIL",
            f"أطوال return داخل day core: {day_core_lengths}",
        )
        day_core_no_write = all(token not in day_core_body for token in ["save_db", "teachers_db[", "del teachers_db"])
        add(
            results,
            "3E-b-3: day core بلا كتابة حالة",
            "PASS" if day_core_no_write else "FAIL",
            "day core لا يحذف ولا يحفظ حالة عامة." if day_core_no_write else "ظهر save_db أو تعديل teachers_db داخل day core.",
        )

    if day_wrapper_body:
        day_wrapper_lengths = _function_return_tuple_lengths(app_text, "get_day_table_updates")
        day_wrapper_ok = "get_day_table_updates_core" in day_wrapper_body and "gr.update" in day_wrapper_body
        add(
            results,
            "3E-b-3: day wrapper يرجع 7 مخرجات",
            "PASS" if day_wrapper_lengths and all(length == 7 for length in day_wrapper_lengths) else "FAIL",
            f"أطوال return داخل day wrapper: {day_wrapper_lengths}",
        )
        add(
            results,
            "3E-b-3: day wrapper يستدعي core ويغلف بـgr.update",
            "PASS" if day_wrapper_ok else "FAIL",
            f"calls_core={'get_day_table_updates_core' in day_wrapper_body}, has_gr_update={'gr.update' in day_wrapper_body}",
        )
        forbidden_day_wrapper_logic = ["get_day_overview", "render_day_all_departments_html", "render_day_table_html", "load_db"]
        found_day_wrapper_logic = [token for token in forbidden_day_wrapper_logic if token in day_wrapper_body]
        add(
            results,
            "3E-b-3: day wrapper رفيع بلا منطق جدول",
            "PASS" if not found_day_wrapper_logic else "FAIL",
            "day wrapper لا يحتوي منطق جدول اليوم." if not found_day_wrapper_logic else f"وجد: {found_day_wrapper_logic}",
        )

    no_school_reverse_import = all(marker not in school_text for marker in ["import app", "from app import"])
    no_schedules_reverse_import = all(marker not in schedules_text for marker in ["import app", "from app import"])
    add(
        results,
        "3E-b-3: الوحدات النظيفة لا تستورد app.py",
        "PASS" if no_school_reverse_import and no_schedules_reverse_import else "FAIL",
        f"school_data_no_app={no_school_reverse_import}, schedules_no_app={no_schedules_reverse_import}",
    )

    process_still_app = re.search(r"^def\s+process_uploaded_excel\s*\(", app_text, re.MULTILINE) is not None
    add(
        results,
        "3E-b-3: process_uploaded_excel باقية مؤجلة في app.py",
        "PASS" if process_still_app else "FAIL",
        "process_uploaded_excel باقية في app.py تمهيدًا لـ3E-b-4." if process_still_app else "process_uploaded_excel غير موجودة في app.py.",
    )

    for module_label, module_path in [("school_data.py", school_path), ("schedules.py", schedules_path)]:
        try:
            py_compile.compile(str(module_path), doraise=True)
            add(results, f"3E-b-3: py_compile {module_label}", "PASS", f"{module_label} لا يحتوي أخطاء نحوية.")
        except Exception as exc:  # pragma: no cover
            add(results, f"3E-b-3: py_compile {module_label}", "FAIL", f"فشل py_compile: {exc}")


def check_process_uploaded_excel_phase3eb4(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3E-b-4: تقسيم process_uploaded_excel إلى core/wrapper."""
    school_path = app_path.with_name("school_data.py")
    if not school_path.exists():
        add(results, "3E-b-4: وجود school_data.py", "FAIL", f"غير موجود: {school_path}")
        return

    school_text = school_path.read_text(encoding="utf-8")
    core_body = function_body(school_text, "process_uploaded_excel_core")
    wrapper_body = function_body(app_text, "process_uploaded_excel")

    add(
        results,
        "3E-b-4: process_uploaded_excel_core موجودة في school_data.py",
        "PASS" if core_body else "FAIL",
        "process_uploaded_excel_core موجودة." if core_body else "process_uploaded_excel_core غير موجودة.",
    )
    add(
        results,
        "3E-b-4: process_uploaded_excel wrapper موجودة في app.py",
        "PASS" if wrapper_body else "FAIL",
        "process_uploaded_excel باقية في app.py كـwrapper." if wrapper_body else "process_uploaded_excel غير موجودة في app.py.",
    )

    if not core_body or not wrapper_body:
        return

    core_decorators = _function_decorators(school_text, "process_uploaded_excel_core")
    wrapper_decorators = _function_decorators(app_text, "process_uploaded_excel")
    add(
        results,
        "3E-b-4: @state_locked على process core",
        "PASS" if "state_locked" in core_decorators else "FAIL",
        f"decorators: {core_decorators}",
    )
    add(
        results,
        "3E-b-4: process wrapper بلا state_locked",
        "PASS" if "state_locked" not in wrapper_decorators else "FAIL",
        f"decorators: {wrapper_decorators}",
    )

    core_tokens = [token for token in ["gr.update", "gr.Warning", "gr.Info", "import gradio"] if token in core_body]
    add(
        results,
        "3E-b-4: process core بلا Gradio مباشر",
        "PASS" if not core_tokens else "FAIL",
        "process core خام بلا gr.update/gr.Warning/gr.Info." if not core_tokens else f"وجد: {core_tokens}",
    )

    core_lengths = _function_return_tuple_lengths(school_text, "process_uploaded_excel_core")
    wrapper_lengths = _function_return_tuple_lengths(app_text, "process_uploaded_excel")
    add(
        results,
        "3E-b-4: process core يرجع 10 عناصر خام",
        "PASS" if core_lengths and all(length == 10 for length in core_lengths) else "FAIL",
        f"أطوال return داخل process core: {core_lengths}",
    )
    add(
        results,
        "3E-b-4: process wrapper يرجع 10 مخرجات",
        "PASS" if wrapper_lengths and all(length == 10 for length in wrapper_lengths) else "FAIL",
        f"أطوال return داخل process wrapper: {wrapper_lengths}",
    )

    wrapper_ok = "process_uploaded_excel_core" in wrapper_body and "gr.update" in wrapper_body
    add(
        results,
        "3E-b-4: process wrapper يستدعي core ويغلف بـgr.update",
        "PASS" if wrapper_ok else "FAIL",
        f"calls_core={'process_uploaded_excel_core' in wrapper_body}, has_gr_update={'gr.update' in wrapper_body}",
    )

    forbidden_wrapper_logic = ["pd.read_excel", "pd.read_csv", "precheck_schedule_excel_template", "teachers_db", "save_db", "extract_class_info"]
    found_wrapper_logic = [token for token in forbidden_wrapper_logic if token in wrapper_body]
    add(
        results,
        "3E-b-4: process wrapper رفيع بلا قراءة/حفظ Excel",
        "PASS" if not found_wrapper_logic else "FAIL",
        "process wrapper لا يحتوي قراءة Excel أو تعديل قاعدة البيانات." if not found_wrapper_logic else f"وجد: {found_wrapper_logic}",
    )

    teacher_names_no_value = "gr.update(choices=teacher_names_all)" in wrapper_body
    teacher_names_with_value_none = "gr.update(choices=teacher_names_all, value=None)" in wrapper_body
    add(
        results,
        "3E-b-4: موضع teacher_names بلا value=None",
        "PASS" if teacher_names_no_value and not teacher_names_with_value_none else "FAIL",
        f"no_value={teacher_names_no_value}, with_value_none={teacher_names_with_value_none}",
    )

    no_reverse_import = all(marker not in school_text for marker in ["import app", "from app import"])
    add(
        results,
        "3E-b-4: school_data.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي." if no_reverse_import else "ظهر import app أو from app import داخل school_data.py.",
    )

    try:
        py_compile.compile(str(school_path), doraise=True)
        add(results, "3E-b-4: py_compile school_data.py", "PASS", "school_data.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3E-b-4: py_compile school_data.py", "FAIL", f"فشل py_compile: {exc}")


def check_audit_logging_phase3ha1(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3H-a-1: نقل سجل العمليات الحساسة إلى storage.py."""
    storage_path = app_path.with_name("storage.py")
    config_path = app_path.with_name("config.py")
    if not storage_path.exists():
        add(results, "3H-a-1: وجود storage.py", "FAIL", f"غير موجود: {storage_path}")
        return

    storage_text = storage_path.read_text(encoding="utf-8")
    config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    storage_has_audit = all(marker in storage_text for marker in ["def write_audit_log", "def _audit_json_safe", "AUDIT_LOG_FILE", "safe_write_json", "get_now_oman"])
    add(
        results,
        "3H-a-1: write_audit_log في storage.py",
        "PASS" if storage_has_audit else "FAIL",
        "write_audit_log ومساعدها موجودان في storage.py مع اعتماديات التخزين." if storage_has_audit else "نقل سجل العمليات إلى storage.py غير مكتمل.",
    )

    app_has_local_audit_defs = any(re.search(rf"^def\s+{fn}\s*\(", app_text, re.MULTILINE) for fn in ["write_audit_log", "_audit_json_safe"])
    add(
        results,
        "3H-a-1: app.py لا يعرّف write_audit_log محليًا",
        "PASS" if not app_has_local_audit_defs else "FAIL",
        "لا توجد تعريفات محلية لـ write_audit_log/_audit_json_safe داخل app.py." if not app_has_local_audit_defs else "ما زالت تعريفات audit موجودة داخل app.py.",
    )

    app_imports_audit = "write_audit_log" in app_text and "from storage import" in app_text
    add(
        results,
        "3H-a-1: app.py يستورد write_audit_log من storage.py",
        "PASS" if app_imports_audit else "FAIL",
        "app.py يستخدم write_audit_log المستوردة من storage.py." if app_imports_audit else "لم يظهر استيراد write_audit_log من storage.py بوضوح.",
    )

    config_has_system_name = bool(re.search(r"^SYSTEM_NAME\s*=", config_text, re.MULTILINE))
    add(
        results,
        "3H-a-1: SYSTEM_NAME متاح في config.py",
        "PASS" if config_has_system_name else "FAIL",
        "SYSTEM_NAME موجود في config.py كمصدر افتراضي عام." if config_has_system_name else "SYSTEM_NAME غير موجود في config.py.",
    )

    no_reverse_import = "import app" not in storage_text and "from app import" not in storage_text
    add(
        results,
        "3H-a-1: storage.py لا يستورد app.py بعد audit",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من storage.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل storage.py.",
    )

    try:
        py_compile.compile(str(storage_path), doraise=True)
        add(results, "3H-a-1: py_compile storage.py", "PASS", "storage.py لا يحتوي أخطاء نحوية بعد نقل audit.")
    except Exception as exc:  # pragma: no cover
        add(results, "3H-a-1: py_compile storage.py", "FAIL", f"فشل py_compile: {exc}")


def check_exemptions_phase3ha2(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3H-a-2: إنشاء exemptions.py ونقل دوال الإعفاءات النظيفة إليه."""
    exemptions_path = app_path.with_name("exemptions.py")
    if not exemptions_path.exists():
        add(results, "3H-a-2: وجود exemptions.py", "FAIL", f"غير موجود: {exemptions_path}")
        return

    exemptions_text = exemptions_path.read_text(encoding="utf-8")
    add(results, "3H-a-2: وجود exemptions.py", "PASS", f"موجود: {exemptions_path}")

    required_functions = [
        "is_teacher_exempt_for_slot",
        "normalize_exempt_slots",
        "build_exempt_slots_from_days_periods",
        "format_exempt_slots_for_display",
        "render_exemptions_log_html",
        "resolve_teacher_key_from_ui",
        "clean_teacher_name_from_ui",
    ]
    missing = [fn for fn in required_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", exemptions_text, re.MULTILINE)]
    add(
        results,
        "3H-a-2: دوال الإعفاءات النظيفة موجودة في exemptions.py",
        "PASS" if not missing else "FAIL",
        "الدوال السبع موجودة في exemptions.py." if not missing else f"ناقص: {missing}",
    )

    app_local_defs = []
    for fn in required_functions:
        app_local_defs.extend(line_numbers_for_pattern(app_text, rf"^def\s+{re.escape(fn)}\s*\("))
    add(
        results,
        "3H-a-2: app.py لا يحتوي تعريفات دوال الإعفاءات المنقولة",
        "PASS" if not app_local_defs else "FAIL",
        "الدوال المنقولة غير معرفة داخل app.py." if not app_local_defs else f"وجدت في الأسطر: {app_local_defs[:10]}",
    )

    no_reverse_import = "import app" not in exemptions_text and "from app import" not in exemptions_text
    add(
        results,
        "3H-a-2: exemptions.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من exemptions.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل exemptions.py.",
    )

    no_gradio = "gr.update" not in exemptions_text and "import gradio" not in exemptions_text and "from gradio" not in exemptions_text
    add(
        results,
        "3H-a-2: exemptions.py بلا Gradio",
        "PASS" if no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio داخل exemptions.py." if no_gradio else "ظهر اعتماد مباشر على Gradio داخل exemptions.py.",
    )

    app_import_ok = "from exemptions import" in app_text
    add(
        results,
        "3H-a-2: app.py يستورد exemptions.py",
        "PASS" if app_import_ok else "FAIL",
        "app.py يستورد دوال الإعفاءات من exemptions.py." if app_import_ok else "لم يظهر from exemptions import داخل app.py.",
    )

    save_teacher_rules_still_app = bool(re.search(r"^def\s+save_teacher_rules\s*\(", app_text, re.MULTILINE))
    save_teacher_rules_wrongly_moved = bool(re.search(r"^def\s+save_teacher_rules\s*\(", exemptions_text, re.MULTILINE))
    add(
        results,
        "3H-a-2: save_teacher_rules بقيت في app.py مؤقتًا",
        "PASS" if save_teacher_rules_still_app and not save_teacher_rules_wrongly_moved else "FAIL",
        "save_teacher_rules باقية في app.py ولم تنتقل قبل 3H-a-3." if save_teacher_rules_still_app and not save_teacher_rules_wrongly_moved else f"in_app={save_teacher_rules_still_app}, in_exemptions={save_teacher_rules_wrongly_moved}",
    )

    distribution_path = app_path.with_name("distribution.py")
    distribution_text = ""
    if distribution_path.exists():
        try:
            distribution_text = distribution_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    critical_sources = app_text + "\n" + distribution_text
    critical_callers = ["assign_logic_core", "update_available_subs_smart_core", "get_falcon_eye_candidates"]
    missing_calls = []
    for fn in critical_callers:
        body = function_body(critical_sources, fn)
        if not body or "is_teacher_exempt_for_slot" not in body:
            missing_calls.append(fn)
    add(
        results,
        "3H-a-2: دوال الترشيح ما زالت تستخدم is_teacher_exempt_for_slot",
        "PASS" if not missing_calls else "FAIL",
        "assign_logic_core/update_available_subs_smart_core/get_falcon_eye_candidates تستدعي دالة الإعفاء المركزية." if not missing_calls else f"ناقص أو غير واضح: {missing_calls}",
    )

    try:
        py_compile.compile(str(exemptions_path), doraise=True)
        add(results, "3H-a-2: py_compile exemptions.py", "PASS", "exemptions.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:  # pragma: no cover
        add(results, "3H-a-2: py_compile exemptions.py", "FAIL", f"فشل py_compile: {exc}")


def check_save_teacher_rules_phase3ha3(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3H-a-3: تقسيم save_teacher_rules إلى core/wrapper."""
    exemptions_path = app_path.with_name("exemptions.py")
    if not exemptions_path.exists():
        add(results, "3H-a-3: وجود exemptions.py", "FAIL", f"غير موجود: {exemptions_path}")
        return

    exemptions_text = exemptions_path.read_text(encoding="utf-8")
    core_exists = bool(re.search(r"^def\s+save_teacher_rules_core\s*\(", exemptions_text, re.MULTILINE))
    wrapper_exists = bool(re.search(r"^def\s+save_teacher_rules\s*\(", app_text, re.MULTILINE))

    add(
        results,
        "3H-a-3: save_teacher_rules_core موجودة في exemptions.py",
        "PASS" if core_exists else "FAIL",
        "save_teacher_rules_core موجودة في exemptions.py." if core_exists else "لم يتم العثور على save_teacher_rules_core في exemptions.py.",
    )
    add(
        results,
        "3H-a-3: save_teacher_rules wrapper باقية في app.py",
        "PASS" if wrapper_exists else "FAIL",
        "save_teacher_rules موجودة في app.py كـwrapper." if wrapper_exists else "لم يتم العثور على save_teacher_rules في app.py.",
    )

    core_decorated = bool(re.search(r"@state_locked\s*\ndef\s+save_teacher_rules_core\s*\(", exemptions_text))
    add(
        results,
        "3H-a-3: @state_locked على save_teacher_rules_core",
        "PASS" if core_decorated else "FAIL",
        "@state_locked موجود على core." if core_decorated else "@state_locked غير موجود على core.",
    )

    wrapper_start_lines = line_numbers_for_pattern(app_text, r"^def\s+save_teacher_rules\s*\(")
    wrapper_has_decorator = False
    if wrapper_start_lines:
        lines = app_text.splitlines()
        line_no = wrapper_start_lines[0]
        prev_line = lines[line_no - 2].strip() if line_no >= 2 else ""
        wrapper_has_decorator = prev_line == "@state_locked"
    add(
        results,
        "3H-a-3: wrapper بلا @state_locked",
        "PASS" if wrapper_exists and not wrapper_has_decorator else "FAIL",
        "wrapper في app.py بلا @state_locked." if wrapper_exists and not wrapper_has_decorator else "wrapper ما زال عليه @state_locked أو غير موجود.",
    )

    core_body = function_body(exemptions_text, "save_teacher_rules_core")
    wrapper_body = function_body(app_text, "save_teacher_rules")

    core_no_gradio = (
        "gr.update" not in core_body
        and "gr.Warning" not in core_body
        and "gr.Info" not in core_body
        and "import gradio" not in core_body
        and "from gradio" not in core_body
    )
    add(
        results,
        "3H-a-3: core بلا Gradio",
        "PASS" if core_exists and core_no_gradio else "FAIL",
        "save_teacher_rules_core لا يحتوي gr.update/gr.Warning/gr.Info/import gradio." if core_exists and core_no_gradio else "ظهر Gradio داخل core.",
    )

    no_reverse_import = "import app" not in exemptions_text and "from app import" not in exemptions_text
    add(
        results,
        "3H-a-3: exemptions.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد import app داخل exemptions.py." if no_reverse_import else "ظهر import app أو from app import داخل exemptions.py.",
    )

    wrapper_calls_core = "save_teacher_rules_core" in wrapper_body
    wrapper_updates_log = "gr.update(value=render_exemptions_log_html())" in wrapper_body
    add(
        results,
        "3H-a-3: wrapper يستدعي core ويحدث سجل الإعفاءات",
        "PASS" if wrapper_calls_core and wrapper_updates_log else "FAIL",
        "wrapper يستدعي save_teacher_rules_core ويرجع gr.update لسجل الإعفاءات." if wrapper_calls_core and wrapper_updates_log else f"calls_core={wrapper_calls_core}, updates_log={wrapper_updates_log}",
    )

    # فحص AST: core يرجع رسالة واحدة فقط في كل فرع، والـwrapper يرجع tuple من عنصرين.
    try:
        import ast
        tree = ast.parse(exemptions_text)
        core_node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "save_teacher_rules_core"), None)
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        raw_returns_only = bool(returns) and all(not isinstance(r.value, (ast.Tuple, ast.List)) for r in returns)
        add(
            results,
            "3H-a-3: core يرجع رسالة خام فقط",
            "PASS" if raw_returns_only else "FAIL",
            f"عدد return في core: {len(returns)}، وكلها ليست tuple/list." if raw_returns_only else "core يرجع tuple/list أو لا يحتوي return واضح.",
        )
    except Exception as exc:
        add(results, "3H-a-3: فحص AST لإرجاع core", "FAIL", f"تعذر فحص AST: {exc}")

    try:
        import ast
        tree = ast.parse(app_text)
        wrapper_node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "save_teacher_rules"), None)
        returns = [n for n in ast.walk(wrapper_node) if isinstance(n, ast.Return)] if wrapper_node else []
        wrapper_two_outputs = len(returns) == 1 and isinstance(returns[0].value, ast.Tuple) and len(returns[0].value.elts) == 2
        add(
            results,
            "3H-a-3: wrapper يرجع عنصرين فقط",
            "PASS" if wrapper_two_outputs else "FAIL",
            "wrapper يحتوي return واحدًا من عنصرين." if wrapper_two_outputs else f"عدد returns={len(returns)} أو عدد عناصر الإرجاع غير مطابق.",
        )
    except Exception as exc:
        add(results, "3H-a-3: فحص AST لإرجاع wrapper", "FAIL", f"تعذر فحص AST: {exc}")



def check_swaps_phase3ia1(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-1: نقل دوال التبادل الودي النظيفة إلى swaps.py."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-1: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    add(results, "3I-a-1: وجود swaps.py", "PASS", f"موجود: {swaps_path}")

    required_functions = [
        "build_swap_button_html",
        "extract_swap_choice_details",
        "render_swap_table_html",
    ]
    missing = [fn for fn in required_functions if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    add(
        results,
        "3I-a-1: دوال التبادل النظيفة موجودة في swaps.py",
        "PASS" if not missing else "FAIL",
        "الدوال الثلاث موجودة في swaps.py." if not missing else f"ناقص: {missing}",
    )

    duplicated_in_app = [fn for fn in required_functions if re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-1: لا توجد تعريفات مكررة في app.py",
        "PASS" if not duplicated_in_app else "FAIL",
        "الدوال المنقولة غير معرفة محليًا في app.py." if not duplicated_in_app else f"ما زالت معرفة في app.py: {duplicated_in_app}",
    )

    app_import_ok = "from swaps import" in app_text
    add(
        results,
        "3I-a-1: app.py يستورد swaps.py",
        "PASS" if app_import_ok else "FAIL",
        "app.py يستورد دوال التبادل النظيفة من swaps.py." if app_import_ok else "لم يظهر from swaps import داخل app.py.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    add(
        results,
        "3I-a-1: swaps.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد اعتماد عكسي من swaps.py إلى app.py." if no_reverse_import else "ظهر import app أو from app import داخل swaps.py.",
    )

    no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-1: swaps.py بلا Gradio",
        "PASS" if no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData داخل swaps.py." if no_gradio else "ظهر اعتماد مباشر على Gradio داخل swaps.py.",
    )

    storage_import_ok = "teachers_db" in swaps_text and "from storage import" in swaps_text
    add(
        results,
        "3I-a-1: swaps.py يعتمد على storage.py للحالة",
        "PASS" if storage_import_ok else "FAIL",
        "swaps.py يستورد teachers_db من storage.py." if storage_import_ok else "لم يظهر استيراد teachers_db من storage.py.",
    )

    confirm_swap_still_app = bool(re.search(r"^def\s+confirm_swap\s*\(", app_text, re.MULTILINE))
    confirm_swap_not_moved = not bool(re.search(r"^def\s+confirm_swap\s*\(", swaps_text, re.MULTILINE))
    add(
        results,
        "3I-a-1: confirm_swap مؤجلة في app.py",
        "PASS" if confirm_swap_still_app and confirm_swap_not_moved else "FAIL",
        "confirm_swap باقية في app.py ولم تنتقل قبل مرحلة core/wrapper الخاصة بها." if confirm_swap_still_app and confirm_swap_not_moved else f"in_app={confirm_swap_still_app}, in_swaps={not confirm_swap_not_moved}",
    )

    event_handler_still_app = bool(re.search(r"^def\s+on_swap_option_selected_from_event\s*\(", app_text, re.MULTILINE))
    event_handler_not_moved = not bool(re.search(r"^def\s+on_swap_option_selected_from_event\s*\(", swaps_text, re.MULTILINE))
    add(
        results,
        "3I-a-1: gr.SelectData handler مؤجل في app.py",
        "PASS" if event_handler_still_app and event_handler_not_moved else "FAIL",
        "on_swap_option_selected_from_event باقية في app.py لأنها تتعامل مع gr.SelectData." if event_handler_still_app and event_handler_not_moved else f"in_app={event_handler_still_app}, in_swaps={not event_handler_not_moved}",
    )

    try:
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-1: py_compile swaps.py", "PASS", "swaps.py لا يحتوي أخطاء نحوية.")
    except Exception as exc:
        add(results, "3I-a-1: py_compile swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_confirm_swap_phase3ia3(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-3: تقسيم confirm_swap إلى core/wrapper داخل swaps.py/app.py."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-3: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    core_body = function_body(swaps_text, "confirm_swap_core")
    wrapper_body = function_body(app_text, "confirm_swap")

    core_exists = bool(re.search(r"^def\s+confirm_swap_core\s*\(", swaps_text, re.MULTILINE))
    wrapper_exists = bool(re.search(r"^def\s+confirm_swap\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-3: confirm_swap_core موجودة في swaps.py",
        "PASS" if core_exists else "FAIL",
        "confirm_swap_core موجودة في swaps.py." if core_exists else "لم يتم العثور على confirm_swap_core داخل swaps.py.",
    )
    add(
        results,
        "3I-a-3: confirm_swap wrapper باقية في app.py",
        "PASS" if wrapper_exists else "FAIL",
        "confirm_swap موجودة في app.py كـwrapper." if wrapper_exists else "لم يتم العثور على confirm_swap في app.py.",
    )

    helper_names = ["extract_clean_period_number", "format_elegant_class"]
    helpers_in_swaps = [fn for fn in helper_names if re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    helpers_in_app = [fn for fn in helper_names if re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-3: دوال confirm_swap المساعدة في swaps.py",
        "PASS" if len(helpers_in_swaps) == len(helper_names) else "FAIL",
        "الدالتان المساعدتان موجودتان في swaps.py." if len(helpers_in_swaps) == len(helper_names) else f"ناقص: {[fn for fn in helper_names if fn not in helpers_in_swaps]}",
    )
    add(
        results,
        "3I-a-3: لا تكرار لدوال confirm_swap المساعدة في app.py",
        "PASS" if not helpers_in_app else "FAIL",
        "لا توجد تعريفات مكررة للدالتين المساعدتين في app.py." if not helpers_in_app else f"تعريفات مكررة: {helpers_in_app}",
    )

    core_decorated = bool(re.search(r"@state_locked\s*\ndef\s+confirm_swap_core\s*\(", swaps_text))
    add(
        results,
        "3I-a-3: @state_locked على confirm_swap_core فقط",
        "PASS" if core_decorated else "FAIL",
        "@state_locked موجودة على confirm_swap_core." if core_decorated else "@state_locked غير موجودة على confirm_swap_core.",
    )

    wrapper_decorated = bool(re.search(r"@state_locked\s*\ndef\s+confirm_swap\s*\(", app_text))
    add(
        results,
        "3I-a-3: confirm_swap wrapper بلا @state_locked",
        "PASS" if wrapper_exists and not wrapper_decorated else "FAIL",
        "wrapper بلا @state_locked." if wrapper_exists and not wrapper_decorated else "ما زالت @state_locked موجودة على wrapper.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    add(
        results,
        "3I-a-3: swaps.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد import app داخل swaps.py." if no_reverse_import else "ظهر import app أو from app import داخل swaps.py.",
    )

    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-3: swaps.py بلا Gradio بعد confirm_swap_core",
        "PASS" if swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData داخل swaps.py." if swaps_no_gradio else "ظهر اعتماد مباشر على Gradio داخل swaps.py.",
    )

    core_no_gradio = bool(core_body) and "gr.update" not in core_body and "render_swap_table_html(" not in core_body
    add(
        results,
        "3I-a-3: confirm_swap_core يرجع خامًا ولا يبني Gradio HTML",
        "PASS" if core_no_gradio else "FAIL",
        "core بلا gr.update ولا render_swap_table_html؛ يرجع الحالة والتحذير الخام." if core_no_gradio else "core يحتوي Gradio أو بناء جدول HTML.",
    )

    wrapper_ok = (
        bool(wrapper_body)
        and "confirm_swap_core(" in wrapper_body
        and "gr.update(value=render_swap_table_html(current_state) + warning)" in wrapper_body
    )
    add(
        results,
        "3I-a-3: wrapper يحوّل الحالة والتحذير إلى مخرجات Gradio",
        "PASS" if wrapper_ok else "FAIL",
        "wrapper يستدعي confirm_swap_core ثم يرجع current_state و gr.update لجدول التبادل." if wrapper_ok else "wrapper لا يطابق العقد المتوقع.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "confirm_swap":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        wrapper_two_outputs = bool(wrapper_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in wrapper_returns)
        add(
            results,
            "3I-a-3: confirm_swap wrapper يرجع عنصرين فقط",
            "PASS" if wrapper_two_outputs else "FAIL",
            "كل فروع wrapper ترجع عنصرين." if wrapper_two_outputs else "wrapper لا يرجع عنصرين في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-3: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "confirm_swap_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        core_two_raw_outputs = bool(core_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in core_returns)
        add(
            results,
            "3I-a-3: confirm_swap_core يرجع حالتين خامتين",
            "PASS" if core_two_raw_outputs else "FAIL",
            "core يرجع tuple من عنصرين: current_state و warning." if core_two_raw_outputs else "core لا يرجع tuple من عنصرين في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-3: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-3: py_compile swaps.py", "PASS", "swaps.py لا يحتوي أخطاء نحوية بعد confirm_swap_core.")
    except Exception as exc:
        add(results, "3I-a-3: py_compile swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_run_radar_safe_phase3ia4a(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-4a: تقسيم run_radar_safe ونقل دواله النظيفة إلى swaps.py."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-4a: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    core_body = function_body(swaps_text, "run_radar_safe_core")
    wrapper_body = function_body(app_text, "run_radar_safe")

    required_in_swaps = [
        "get_current_day_oman",
        "get_class_dna",
        "check_teacher_load",
        "run_radar_safe_core",
    ]
    missing = [fn for fn in required_in_swaps if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    add(
        results,
        "3I-a-4a: دوال run_radar_safe النظيفة موجودة في swaps.py",
        "PASS" if not missing else "FAIL",
        "الدوال الأربع موجودة في swaps.py." if not missing else f"ناقص: {missing}",
    )

    duplicated_in_app = [fn for fn in required_in_swaps if re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-4a: لا تكرار لدوال run_radar_safe في app.py",
        "PASS" if not duplicated_in_app else "FAIL",
        "الدوال المنقولة غير معرفة محليًا في app.py." if not duplicated_in_app else f"ما زالت معرفة في app.py: {duplicated_in_app}",
    )

    wrapper_exists = bool(re.search(r"^def\s+run_radar_safe\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-4a: run_radar_safe wrapper باقية في app.py",
        "PASS" if wrapper_exists else "FAIL",
        "run_radar_safe باقية في app.py كـwrapper." if wrapper_exists else "لم يتم العثور على run_radar_safe في app.py.",
    )

    wrapper_calls_core = bool(wrapper_body) and "run_radar_safe_core(" in wrapper_body
    wrapper_has_expected_updates = (
        bool(wrapper_body)
        and "gr.update(choices=candidates, value=None)" in wrapper_body
        and "gr.update(value=default_msg)" in wrapper_body
        and "gr.update(value=\"\")" in wrapper_body
    )
    add(
        results,
        "3I-a-4a: wrapper يستدعي core ويرجع مخرجات Gradio القديمة",
        "PASS" if wrapper_calls_core and wrapper_has_expected_updates else "FAIL",
        "wrapper يستدعي run_radar_safe_core ويرجع 3 gr.update بنفس العقد القديم." if wrapper_calls_core and wrapper_has_expected_updates else f"calls_core={wrapper_calls_core}, updates_ok={wrapper_has_expected_updates}",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+run_radar_safe_core\s*\(", swaps_text))
    add(
        results,
        "3I-a-4a: run_radar_safe_core بلا @state_locked",
        "PASS" if no_state_lock else "FAIL",
        "لا توجد @state_locked على run_radar_safe_core لأنها لا تعدل البيانات." if no_state_lock else "ظهرت @state_locked على core بلا حاجة.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-4a: swaps.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد import app داخل swaps.py." if no_reverse_import else "ظهر import app أو from app import داخل swaps.py.",
    )
    add(
        results,
        "3I-a-4a: swaps.py بلا Gradio بعد run_radar_safe_core",
        "PASS" if swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData داخل swaps.py." if swaps_no_gradio else "ظهر اعتماد مباشر على Gradio داخل swaps.py.",
    )

    storage_deps_ok = all(name in swaps_text for name in ["teachers_db", "SCHOOL_WEEK_DAYS", "get_now_oman"])
    add(
        results,
        "3I-a-4a: اعتماديات run_radar_safe من storage.py",
        "PASS" if storage_deps_ok and "from storage import" in swaps_text else "FAIL",
        "swaps.py يستورد teachers_db و SCHOOL_WEEK_DAYS و get_now_oman من storage.py." if storage_deps_ok and "from storage import" in swaps_text else "اعتماديات storage.py غير مكتملة داخل swaps.py.",
    )

    core_clean = bool(core_body) and "gr.update" not in core_body and "return gr.update" not in core_body
    add(
        results,
        "3I-a-4a: run_radar_safe_core يرجع بيانات خام فقط",
        "PASS" if core_clean else "FAIL",
        "core بلا gr.update ويرجع قائمة مرشحين خام." if core_clean else "core يحتوي gr.update أو لا يمكن قراءة جسم الدالة.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_radar_safe":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        wrapper_three_outputs = bool(wrapper_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3 for r in wrapper_returns)
        add(
            results,
            "3I-a-4a: run_radar_safe wrapper يرجع 3 عناصر",
            "PASS" if wrapper_three_outputs else "FAIL",
            "كل فروع wrapper ترجع 3 عناصر." if wrapper_three_outputs else "wrapper لا يرجع 3 عناصر في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4a: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "run_radar_safe_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        core_no_tuple_returns = bool(core_returns) and all(not isinstance(r.value, ast.Tuple) for r in core_returns)
        add(
            results,
            "3I-a-4a: run_radar_safe_core لا يرجع tuple",
            "PASS" if core_no_tuple_returns else "FAIL",
            "core يرجع قائمة واحدة في كل الفروع، لا tuple." if core_no_tuple_returns else "core يرجع tuple أو لا يحتوي return واضح.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4a: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-4a: py_compile swaps.py", "PASS", "swaps.py لا يحتوي أخطاء نحوية بعد run_radar_safe_core.")
    except Exception as exc:
        add(results, "3I-a-4a: py_compile swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_generate_wa_msg_phase3ia4b(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-4b: تقسيم generate_wa_msg إلى core/wrapper داخل swaps.py/app.py."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-4b: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    core_body = function_body(swaps_text, "generate_wa_msg_core")
    wrapper_body = function_body(app_text, "generate_wa_msg")

    core_exists = bool(re.search(r"^def\s+generate_wa_msg_core\s*\(", swaps_text, re.MULTILINE))
    add(
        results,
        "3I-a-4b: generate_wa_msg_core موجودة في swaps.py",
        "PASS" if core_exists else "FAIL",
        "generate_wa_msg_core موجودة داخل swaps.py." if core_exists else "لم يتم العثور على generate_wa_msg_core داخل swaps.py.",
    )

    duplicated_core_or_old = bool(re.search(r"^def\s+generate_wa_msg_core\s*\(", app_text, re.MULTILINE))
    wrapper_exists = bool(re.search(r"^def\s+generate_wa_msg\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-4b: wrapper موجودة في app.py بلا core مكرر",
        "PASS" if wrapper_exists and not duplicated_core_or_old else "FAIL",
        "generate_wa_msg باقية في app.py كـwrapper ولا يوجد generate_wa_msg_core مكرر هناك." if wrapper_exists and not duplicated_core_or_old else f"wrapper_exists={wrapper_exists}, duplicated_core={duplicated_core_or_old}",
    )

    wrapper_calls_core = bool(wrapper_body) and "generate_wa_msg_core(" in wrapper_body
    wrapper_updates_ok = bool(wrapper_body) and "gr.update(value=msg)" in wrapper_body and "gr.update(value=btn_html)" in wrapper_body
    add(
        results,
        "3I-a-4b: wrapper يستدعي core ويرجع مخرجات Gradio القديمة",
        "PASS" if wrapper_calls_core and wrapper_updates_ok else "FAIL",
        "wrapper يستدعي generate_wa_msg_core ويرجع gr.update للرسالة والزر." if wrapper_calls_core and wrapper_updates_ok else f"calls_core={wrapper_calls_core}, updates_ok={wrapper_updates_ok}",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+generate_wa_msg_core\s*\(", swaps_text))
    add(
        results,
        "3I-a-4b: generate_wa_msg_core بلا @state_locked",
        "PASS" if no_state_lock else "FAIL",
        "لا توجد @state_locked على generate_wa_msg_core لأنها لا تعدّل البيانات." if no_state_lock else "ظهرت @state_locked على core بلا حاجة.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-4b: swaps.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد import app داخل swaps.py." if no_reverse_import else "ظهر import app أو from app import داخل swaps.py.",
    )
    add(
        results,
        "3I-a-4b: swaps.py بلا Gradio بعد generate_wa_msg_core",
        "PASS" if swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData داخل swaps.py." if swaps_no_gradio else "ظهر اعتماد مباشر على Gradio داخل swaps.py.",
    )

    deps_ok = all(name in swaps_text for name in ["teachers_db", "urllib.parse", "extract_clean_period_number", "format_elegant_class"])
    add(
        results,
        "3I-a-4b: اعتماديات generate_wa_msg_core مكتملة",
        "PASS" if deps_ok else "FAIL",
        "اعتماديات core متوفرة داخل swaps.py/storage.py والمكتبة القياسية." if deps_ok else "اعتماديات generate_wa_msg_core غير مكتملة.",
    )

    core_clean = bool(core_body) and "gr.update" not in core_body and "return gr.update" not in core_body
    add(
        results,
        "3I-a-4b: generate_wa_msg_core يرجع بيانات خام فقط",
        "PASS" if core_clean else "FAIL",
        "core بلا gr.update ويرجع msg و btn_html خامين." if core_clean else "core يحتوي gr.update أو لا يمكن قراءة جسم الدالة.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_wa_msg":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        wrapper_two_outputs = bool(wrapper_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in wrapper_returns)
        add(
            results,
            "3I-a-4b: generate_wa_msg wrapper يرجع عنصرين",
            "PASS" if wrapper_two_outputs else "FAIL",
            "كل فروع wrapper ترجع عنصرين." if wrapper_two_outputs else "wrapper لا يرجع عنصرين في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4b: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_wa_msg_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        core_two_raw_outputs = bool(core_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in core_returns)
        add(
            results,
            "3I-a-4b: generate_wa_msg_core يرجع msg و btn_html",
            "PASS" if core_two_raw_outputs else "FAIL",
            "core يرجع tuple من عنصرين في كل الفروع." if core_two_raw_outputs else "core لا يرجع tuple من عنصرين في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4b: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-4b: py_compile swaps.py", "PASS", "swaps.py لا يحتوي أخطاء نحوية بعد generate_wa_msg_core.")
    except Exception as exc:
        add(results, "3I-a-4b: py_compile swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_get_swap_candidates_phase3ia4c(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-4c: تقسيم get_swap_candidates_for_period إلى core/wrapper."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-4c: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    core_body = function_body(swaps_text, "get_swap_candidates_for_period_core")
    wrapper_body = function_body(app_text, "get_swap_candidates_for_period")
    on_select_body = function_body(app_text, "on_swap_option_selected")

    core_exists = bool(re.search(r"^def\s+get_swap_candidates_for_period_core\s*\(", swaps_text, re.MULTILINE))
    add(
        results,
        "3I-a-4c: get_swap_candidates_for_period_core موجودة في swaps.py",
        "PASS" if core_exists else "FAIL",
        "core موجودة داخل swaps.py." if core_exists else "لم يتم العثور على core داخل swaps.py.",
    )

    wrapper_exists = bool(re.search(r"^def\s+get_swap_candidates_for_period\s*\(", app_text, re.MULTILINE))
    duplicated_core = bool(re.search(r"^def\s+get_swap_candidates_for_period_core\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-4c: wrapper موجودة في app.py بلا core مكرر",
        "PASS" if wrapper_exists and not duplicated_core else "FAIL",
        "wrapper باقية في app.py ولا يوجد core مكرر هناك." if wrapper_exists and not duplicated_core else f"wrapper_exists={wrapper_exists}, duplicated_core={duplicated_core}",
    )

    wrapper_calls_core = bool(wrapper_body) and "get_swap_candidates_for_period_core(" in wrapper_body
    wrapper_updates_ok = (
        bool(wrapper_body)
        and "gr.update(choices=candidates" in wrapper_body
        and "gr.update(value=saved_message" in wrapper_body
        and "gr.update(value=btn_value" in wrapper_body
        and "interactive=confirm_interactive" in wrapper_body
    )
    add(
        results,
        "3I-a-4c: wrapper يستدعي core ويرجع مخرجات Gradio القديمة",
        "PASS" if wrapper_calls_core and wrapper_updates_ok else "FAIL",
        "wrapper يحوّل القيم الخام إلى 4 مخرجات Gradio." if wrapper_calls_core and wrapper_updates_ok else f"calls_core={wrapper_calls_core}, updates_ok={wrapper_updates_ok}",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+get_swap_candidates_for_period_core\s*\(", swaps_text))
    add(
        results,
        "3I-a-4c: get_swap_candidates_for_period_core بلا @state_locked",
        "PASS" if no_state_lock else "FAIL",
        "لا توجد @state_locked على core لأنها لا تعدّل البيانات." if no_state_lock else "ظهرت @state_locked على core بلا حاجة.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-4c: swaps.py لا يستورد app.py",
        "PASS" if no_reverse_import else "FAIL",
        "لا يوجد import app داخل swaps.py." if no_reverse_import else "ظهر import app أو from app import داخل swaps.py.",
    )
    add(
        results,
        "3I-a-4c: swaps.py بلا Gradio بعد get_swap_candidates_for_period_core",
        "PASS" if swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData داخل swaps.py." if swaps_no_gradio else "ظهر اعتماد مباشر على Gradio داخل swaps.py.",
    )

    direct_cores_ok = (
        bool(core_body)
        and "run_radar_safe_core(" in core_body
        and "generate_wa_msg_core(" in core_body
        and "run_radar_safe(" not in core_body
        and "generate_wa_msg(" not in core_body
        and "_get_update_choices" not in core_body
        and "_get_update_value" not in core_body
    )
    add(
        results,
        "3I-a-4c: core يستدعي cores مباشرة بلا حيلة gr.update",
        "PASS" if direct_cores_ok else "FAIL",
        "core يستدعي run_radar_safe_core و generate_wa_msg_core مباشرة." if direct_cores_ok else "core ما زال يعتمد على wrappers أو دوال استخراج gr.update.",
    )

    update_choices_removed = "_get_update_choices" not in app_text and "_get_update_choices" not in swaps_text
    add(
        results,
        "3I-a-4c: حذف _get_update_choices بعد موتها",
        "PASS" if update_choices_removed else "FAIL",
        "_get_update_choices غير موجودة بعد أن أصبحت كودًا ميتًا." if update_choices_removed else "_get_update_choices ما زالت موجودة.",
    )

    update_value_is_safe = (
        "_get_update_value" not in app_text
        and "_get_update_value" not in swaps_text
    ) or (
        bool(re.search(r"^def\s+_get_update_value\s*\(", app_text, re.MULTILINE))
        and bool(on_select_body)
        and "_get_update_value" in on_select_body
    )
    add(
        results,
        "3I-a-4c/4d: حالة _get_update_value آمنة",
        "PASS" if update_value_is_safe else "FAIL",
        "_get_update_value إما باقية مؤقتًا لـ on_swap_option_selected أو حُذفت بعد 3I-a-4d." if update_value_is_safe else "_get_update_value موجودة في موضع غير متوقع أو لم تعد متوافقة مع 3I-a-4c/4d.",
    )

    core_clean = bool(core_body) and "gr.update" not in core_body and "return gr.update" not in core_body
    add(
        results,
        "3I-a-4c: get_swap_candidates_for_period_core يرجع بيانات خام فقط",
        "PASS" if core_clean else "FAIL",
        "core بلا gr.update ويرجع candidates/choice/message/button/interactivity كقيم خام." if core_clean else "core يحتوي gr.update أو لا يمكن قراءة جسم الدالة.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_swap_candidates_for_period":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        wrapper_four_outputs = bool(wrapper_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 4 for r in wrapper_returns)
        add(
            results,
            "3I-a-4c: wrapper يرجع 4 عناصر",
            "PASS" if wrapper_four_outputs else "FAIL",
            "كل فروع wrapper ترجع 4 عناصر." if wrapper_four_outputs else "wrapper لا يرجع 4 عناصر في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4c: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "get_swap_candidates_for_period_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        core_five_raw_outputs = bool(core_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 5 for r in core_returns)
        add(
            results,
            "3I-a-4c: core يرجع 5 قيم خام",
            "PASS" if core_five_raw_outputs else "FAIL",
            "core يرجع tuple من 5 قيم خام في كل الفروع." if core_five_raw_outputs else "core لا يرجع tuple من 5 قيم خام في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4c: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-4c: py_compile swaps.py", "PASS", "swaps.py لا يحتوي أخطاء نحوية بعد get_swap_candidates_for_period_core.")
    except Exception as exc:
        add(results, "3I-a-4c: py_compile swaps.py", "FAIL", f"فشل py_compile: {exc}")

def check_on_swap_option_selected_phase3ia4d(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-4d: تقسيم on_swap_option_selected إلى core/wrapper وحذف _get_update_value."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-4d: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    core_body = function_body(swaps_text, "on_swap_option_selected_core")
    wrapper_body = function_body(app_text, "on_swap_option_selected")

    core_exists = bool(re.search(r"^def\s+on_swap_option_selected_core\s*\(", swaps_text, re.MULTILINE))
    duplicated_core = bool(re.search(r"^def\s+on_swap_option_selected_core\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-4d: on_swap_option_selected_core موجودة في swaps.py فقط",
        "PASS" if core_exists and not duplicated_core else "FAIL",
        "core موجودة داخل swaps.py ولا توجد نسخة مكررة في app.py." if core_exists and not duplicated_core else f"core_exists={core_exists}, duplicated_core={duplicated_core}",
    )

    wrapper_exists = bool(re.search(r"^def\s+on_swap_option_selected\s*\(", app_text, re.MULTILINE))
    wrapper_calls_core = bool(wrapper_body) and "on_swap_option_selected_core(" in wrapper_body
    wrapper_no_old_helpers = bool(wrapper_body) and "_get_update_value" not in wrapper_body and "generate_wa_msg(" not in wrapper_body
    wrapper_updates_ok = (
        bool(wrapper_body)
        and "gr.update(value=msg_value" in wrapper_body
        and "gr.update(value=btn_value" in wrapper_body
        and "interactive=is_interactive" in wrapper_body
        and wrapper_body.count("visible=True") >= 3
    )
    add(
        results,
        "3I-a-4d: wrapper يستدعي core ويرجع مخرجات Gradio القديمة",
        "PASS" if wrapper_exists and wrapper_calls_core and wrapper_no_old_helpers and wrapper_updates_ok else "FAIL",
        "wrapper خفيف ويحوّل msg/button/interactive إلى 3 مخرجات Gradio." if wrapper_exists and wrapper_calls_core and wrapper_no_old_helpers and wrapper_updates_ok else f"wrapper_exists={wrapper_exists}, calls_core={wrapper_calls_core}, no_old_helpers={wrapper_no_old_helpers}, updates_ok={wrapper_updates_ok}",
    )

    event_handler_still_app = bool(re.search(r"^def\s+on_swap_option_selected_from_event\s*\(", app_text, re.MULTILINE))
    event_handler_not_moved = not bool(re.search(r"^def\s+on_swap_option_selected_from_event\s*\(", swaps_text, re.MULTILINE))
    add(
        results,
        "3I-a-4d: on_swap_option_selected_from_event باقية في app.py",
        "PASS" if event_handler_still_app and event_handler_not_moved else "FAIL",
        "event wrapper باقية في app.py لأنها تتعامل مع gr.SelectData." if event_handler_still_app and event_handler_not_moved else f"in_app={event_handler_still_app}, in_swaps={not event_handler_not_moved}",
    )

    core_direct_ok = (
        bool(core_body)
        and "generate_wa_msg_core(" in core_body
        and "generate_wa_msg(" not in core_body
        and "_get_update_value" not in core_body
        and "gr.update" not in core_body
        and "return SWAP_EMPTY_MSG" in core_body
    )
    add(
        results,
        "3I-a-4d: core يستدعي generate_wa_msg_core مباشرة",
        "PASS" if core_direct_ok else "FAIL",
        "core يرجع قيمًا خامًا ويستدعي generate_wa_msg_core مباشرة بلا wrapper أو gr.update." if core_direct_ok else "core لا يحقق شرط الاستدعاء المباشر أو يحتوي اعتمادًا غير نظيف.",
    )

    update_value_removed = "_get_update_value" not in app_text and "_get_update_value" not in swaps_text
    add(
        results,
        "3I-a-4d: حذف _get_update_value بعد موتها",
        "PASS" if update_value_removed else "FAIL",
        "_get_update_value غير موجودة في app.py أو swaps.py بعد نقل on_swap_option_selected." if update_value_removed else "_get_update_value ما زالت موجودة بعد 3I-a-4d.",
    )

    empty_msg_in_swaps = "SWAP_EMPTY_MSG =" in swaps_text
    app_imports_empty_msg = "SWAP_EMPTY_MSG" in re.search(r"from\s+swaps\s+import\s*\((.*?)\)", app_text, re.DOTALL).group(1) if re.search(r"from\s+swaps\s+import\s*\((.*?)\)", app_text, re.DOTALL) else False
    app_local_empty_msg = bool(re.search(r"^SWAP_EMPTY_MSG\s*=", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-4d: SWAP_EMPTY_MSG في swaps.py بلا اعتماد عكسي",
        "PASS" if empty_msg_in_swaps and app_imports_empty_msg and not app_local_empty_msg else "FAIL",
        "SWAP_EMPTY_MSG مُعرّفة في swaps.py ومستوردة في app.py بلا تعريف محلي مكرر." if empty_msg_in_swaps and app_imports_empty_msg and not app_local_empty_msg else f"in_swaps={empty_msg_in_swaps}, imported_in_app={app_imports_empty_msg}, local_in_app={app_local_empty_msg}",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+on_swap_option_selected_core\s*\(", swaps_text))
    add(
        results,
        "3I-a-4d: on_swap_option_selected_core بلا @state_locked",
        "PASS" if no_state_lock else "FAIL",
        "لا توجد @state_locked على core لأنها لا تعدّل البيانات." if no_state_lock else "ظهرت @state_locked على core بلا حاجة.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-4d: swaps.py بلا Gradio ولا import app",
        "PASS" if no_reverse_import and swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData أو import app داخل swaps.py." if no_reverse_import and swaps_no_gradio else f"no_reverse_import={no_reverse_import}, swaps_no_gradio={swaps_no_gradio}",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_swap_option_selected":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        wrapper_three_outputs = bool(wrapper_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3 for r in wrapper_returns)
        add(
            results,
            "3I-a-4d: wrapper يرجع 3 عناصر",
            "PASS" if wrapper_three_outputs else "FAIL",
            "كل فروع wrapper ترجع 3 عناصر." if wrapper_three_outputs else "wrapper لا يرجع 3 عناصر في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4d: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "on_swap_option_selected_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
                break
        core_three_raw_outputs = bool(core_returns) and all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 3 for r in core_returns)
        add(
            results,
            "3I-a-4d: core يرجع 3 قيم خام",
            "PASS" if core_three_raw_outputs else "FAIL",
            "core يرجع tuple من 3 قيم خام في كل الفروع." if core_three_raw_outputs else "core لا يرجع tuple من 3 قيم خام في كل الفروع.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-4d: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(app_path), doraise=True)
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-4d: py_compile app.py و swaps.py", "PASS", "app.py و swaps.py بلا أخطاء نحوية بعد 3I-a-4d.")
    except Exception as exc:
        add(results, "3I-a-4d: py_compile app.py و swaps.py", "FAIL", f"فشل py_compile: {exc}")

def check_swap_context_phase3ia5a(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-5a: تقسيم دوال سياق التبادل وحصص المعلم إلى core/wrapper."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-5a: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    required_cores = [
        "load_confirmed_swaps_for_context_core",
        "clear_swap_detail_ui_core",
        "get_teacher_periods_marked_core",
    ]
    missing = [fn for fn in required_cores if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    duplicated = [fn for fn in required_cores if re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-5a: cores الثلاثة موجودة في swaps.py فقط",
        "PASS" if not missing and not duplicated else "FAIL",
        "cores موجودة في swaps.py ولا توجد نسخ مكررة في app.py." if not missing and not duplicated else f"missing={missing}, duplicated={duplicated}",
    )

    wrappers = [
        "load_confirmed_swaps_for_context",
        "clear_swap_detail_ui",
        "get_teacher_periods_marked",
    ]
    missing_wrappers = [fn for fn in wrappers if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-5a: wrappers الثلاثة باقية في app.py",
        "PASS" if not missing_wrappers else "FAIL",
        "wrappers باقية بنفس أسمائها داخل app.py." if not missing_wrappers else f"ناقص: {missing_wrappers}",
    )

    load_body = function_body(app_text, "load_confirmed_swaps_for_context")
    clear_body = function_body(app_text, "clear_swap_detail_ui")
    marked_body = function_body(app_text, "get_teacher_periods_marked")
    wrappers_call_core = (
        "load_confirmed_swaps_for_context_core(" in load_body
        and "clear_swap_detail_ui_core(" in clear_body
        and "get_teacher_periods_marked_core(" in marked_body
    )
    add(
        results,
        "3I-a-5a: wrappers تستدعي cores مباشرة",
        "PASS" if wrappers_call_core else "FAIL",
        "كل wrapper يستدعي core المقابل مباشرة." if wrappers_call_core else f"load={ 'load_confirmed_swaps_for_context_core(' in load_body }, clear={ 'clear_swap_detail_ui_core(' in clear_body }, marked={ 'get_teacher_periods_marked_core(' in marked_body }",
    )

    load_updates_ok = "render_swap_table_html(state)" in load_body and "gr.update(value=" in load_body
    clear_updates_ok = (
        "gr.update(choices=choices, value=selected_value, visible=True)" in clear_body
        and "gr.update(value=message_value, visible=True)" in clear_body
        and "gr.update(value=button_html, visible=True)" in clear_body
        and "interactive=confirm_interactive" in clear_body
    )
    marked_updates_ok = "return gr.update(choices=choices, value=selected_value)" in marked_body
    add(
        results,
        "3I-a-5a: wrappers تحافظ على عقود Gradio القديمة",
        "PASS" if load_updates_ok and clear_updates_ok and marked_updates_ok else "FAIL",
        "load يرجع عنصرين، clear يرجع 4 عناصر، marked يرجع gr.update مفرد." if load_updates_ok and clear_updates_ok and marked_updates_ok else f"load={load_updates_ok}, clear={clear_updates_ok}, marked={marked_updates_ok}",
    )

    no_state_locks = not any(bool(re.search(rf"@state_locked\s*\ndef\s+{re.escape(fn)}\s*\(", swaps_text)) for fn in required_cores)
    add(
        results,
        "3I-a-5a: cores الجديدة بلا @state_locked",
        "PASS" if no_state_locks else "FAIL",
        "لا توجد @state_locked على cores لأنها لا تعدّل البيانات." if no_state_locks else "ظهرت @state_locked على أحد cores الجديدة.",
    )

    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-5a: swaps.py بلا Gradio ولا import app",
        "PASS" if no_reverse_import and swaps_no_gradio else "FAIL",
        "لا يوجد gr.update أو import gradio أو gr.SelectData أو import app داخل swaps.py." if no_reverse_import and swaps_no_gradio else f"no_reverse_import={no_reverse_import}, swaps_no_gradio={swaps_no_gradio}",
    )

    deps_ok = all(name in swaps_text for name in ["teachers_db", "swap_db", "extract_clean_period_number", "format_elegant_class"])
    add(
        results,
        "3I-a-5a: اعتماديات cores الجديدة متوفرة",
        "PASS" if deps_ok else "FAIL",
        "اعتماديات الحالة والتنسيق متوفرة في swaps.py/storage.py." if deps_ok else "تنقص إحدى الاعتماديات المطلوبة.",
    )

    try:
        app_tree = ast.parse(app_text)
        returns_by_func = {}
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name in wrappers:
                returns_by_func[node.name] = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        load_two_outputs = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in returns_by_func.get("load_confirmed_swaps_for_context", []))
        clear_four_outputs = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 4 for r in returns_by_func.get("clear_swap_detail_ui", []))
        marked_single_update = all(not isinstance(r.value, ast.Tuple) for r in returns_by_func.get("get_teacher_periods_marked", []))
        add(
            results,
            "3I-a-5a: عقود wrapper بعدد المخرجات صحيحة",
            "PASS" if load_two_outputs and clear_four_outputs and marked_single_update else "FAIL",
            "load=2، clear=4، marked=مفرد." if load_two_outputs and clear_four_outputs and marked_single_update else f"load={load_two_outputs}, clear={clear_four_outputs}, marked={marked_single_update}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-5a: تحليل AST للـwrappers", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        returns_by_func = {}
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name in required_cores:
                returns_by_func[node.name] = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        clear_five_raw = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 5 for r in returns_by_func.get("clear_swap_detail_ui_core", []))
        marked_two_raw = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in returns_by_func.get("get_teacher_periods_marked_core", []))
        load_raw_state = all(not isinstance(r.value, ast.Tuple) for r in returns_by_func.get("load_confirmed_swaps_for_context_core", []))
        add(
            results,
            "3I-a-5a: cores ترجع قيمًا خامًا بالعقود الصحيحة",
            "PASS" if clear_five_raw and marked_two_raw and load_raw_state else "FAIL",
            "load يرجع state، clear يرجع 5 قيم، marked يرجع قيمتين." if clear_five_raw and marked_two_raw and load_raw_state else f"load={load_raw_state}, clear={clear_five_raw}, marked={marked_two_raw}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-5a: تحليل AST للـcores", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(app_path), doraise=True)
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-5a: py_compile app.py و swaps.py", "PASS", "app.py و swaps.py بلا أخطاء نحوية بعد 3I-a-5a.")
    except Exception as exc:
        add(results, "3I-a-5a: py_compile app.py و swaps.py", "FAIL", f"فشل py_compile: {exc}")



def check_swap_filter_periods_phase3ia5b(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-5b: تقسيم فلترة معلمي التبادل وحصص المعلم إلى core/wrapper."""
    swaps_path = app_path.with_name("swaps.py")
    schedules_path = app_path.with_name("schedules.py")
    if not swaps_path.exists():
        add(results, "3I-a-5b: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")
    schedules_text = schedules_path.read_text(encoding="utf-8") if schedules_path.exists() else ""

    required_cores = [
        "filter_swap_teachers_safe_core",
        "get_teacher_periods_safe_core",
    ]
    missing = [fn for fn in required_cores if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    duplicated = [fn for fn in required_cores if re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-5b: cores موجودة في swaps.py فقط",
        "PASS" if not missing and not duplicated else "FAIL",
        "cores موجودة في swaps.py ولا توجد نسخ مكررة في app.py." if not missing and not duplicated else f"missing={missing}, duplicated={duplicated}",
    )

    wrappers = [
        "filter_swap_teachers_safe",
        "get_teacher_periods_safe",
    ]
    missing_wrappers = [fn for fn in wrappers if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", app_text, re.MULTILINE)]
    add(
        results,
        "3I-a-5b: wrappers باقية في app.py",
        "PASS" if not missing_wrappers else "FAIL",
        "wrappers باقية بنفس أسمائها داخل app.py." if not missing_wrappers else f"ناقص: {missing_wrappers}",
    )

    filter_body = function_body(app_text, "filter_swap_teachers_safe")
    periods_body = function_body(app_text, "get_teacher_periods_safe")
    wrappers_call_core = (
        "filter_swap_teachers_safe_core(" in filter_body
        and "get_teacher_periods_safe_core(" in periods_body
    )
    wrappers_return_single_update = (
        "return gr.update(choices=choices, value=value)" in filter_body
        and "return gr.update(choices=choices, value=value)" in periods_body
    )
    add(
        results,
        "3I-a-5b: wrappers خفيفة وتحافظ على عقد gr.update المفرد",
        "PASS" if wrappers_call_core and wrappers_return_single_update else "FAIL",
        "كل wrapper يستدعي core ويرجع gr.update واحدًا." if wrappers_call_core and wrappers_return_single_update else f"calls_core={wrappers_call_core}, single_update={wrappers_return_single_update}",
    )

    schedules_import_ok = "from schedules import get_teacher_choices" in swaps_text
    schedules_no_reverse = "import swaps" not in schedules_text and "from swaps import" not in schedules_text
    add(
        results,
        "3I-a-5b: اعتماد schedules.py آمن",
        "PASS" if schedules_import_ok and schedules_no_reverse else "FAIL",
        "swaps.py يستورد get_teacher_choices من schedules.py ولا يوجد اعتماد عكسي." if schedules_import_ok and schedules_no_reverse else f"schedules_import_ok={schedules_import_ok}, schedules_no_reverse={schedules_no_reverse}",
    )

    no_state_locks = not any(bool(re.search(rf"@state_locked\s*\ndef\s+{re.escape(fn)}\s*\(", swaps_text)) for fn in required_cores)
    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-5b: cores بلا @state_locked و swaps.py بلا Gradio/import app",
        "PASS" if no_state_locks and no_reverse_import and swaps_no_gradio else "FAIL",
        "لا توجد @state_locked ولا اعتماد Gradio أو app داخل swaps.py." if no_state_locks and no_reverse_import and swaps_no_gradio else f"no_state_locks={no_state_locks}, no_reverse_import={no_reverse_import}, swaps_no_gradio={swaps_no_gradio}",
    )

    deps_ok = all(name in swaps_text for name in ["teachers_db", "format_elegant_class", "get_teacher_choices"])
    add(
        results,
        "3I-a-5b: اعتماديات cores متوفرة",
        "PASS" if deps_ok else "FAIL",
        "teachers_db و format_elegant_class و get_teacher_choices متوفرة للـcores." if deps_ok else "تنقص إحدى الاعتماديات المطلوبة.",
    )

    try:
        app_tree = ast.parse(app_text)
        returns_by_func = {}
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name in wrappers:
                returns_by_func[node.name] = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        filter_single = all(not isinstance(r.value, ast.Tuple) for r in returns_by_func.get("filter_swap_teachers_safe", []))
        periods_single = all(not isinstance(r.value, ast.Tuple) for r in returns_by_func.get("get_teacher_periods_safe", []))
        add(
            results,
            "3I-a-5b: wrappers ترجع عنصرًا واحدًا",
            "PASS" if filter_single and periods_single else "FAIL",
            "filter و periods يرجعان gr.update مفردًا." if filter_single and periods_single else f"filter={filter_single}, periods={periods_single}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-5b: تحليل AST للـwrappers", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        returns_by_func = {}
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name in required_cores:
                returns_by_func[node.name] = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        filter_two_raw = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in returns_by_func.get("filter_swap_teachers_safe_core", []))
        periods_two_raw = all(isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2 for r in returns_by_func.get("get_teacher_periods_safe_core", []))
        add(
            results,
            "3I-a-5b: cores ترجع قيمتين خامًا",
            "PASS" if filter_two_raw and periods_two_raw else "FAIL",
            "filter_core و periods_core يرجعان (choices, value)." if filter_two_raw and periods_two_raw else f"filter={filter_two_raw}, periods={periods_two_raw}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-5b: تحليل AST للـcores", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(app_path), doraise=True)
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-5b: py_compile app.py و swaps.py", "PASS", "app.py و swaps.py بلا أخطاء نحوية بعد 3I-a-5b.")
    except Exception as exc:
        add(results, "3I-a-5b: py_compile app.py و swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_export_swaps_excel_phase3ia6a(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-6a: تقسيم export_confirmed_swaps_excel إلى core/wrapper."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-6a: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")

    required_cores = ["export_confirmed_swaps_excel_core", "format_period_label"]
    missing = [fn for fn in required_cores if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    duplicated_core = bool(re.search(r"^def\s+export_confirmed_swaps_excel_core\s*\(", app_text, re.MULTILINE))
    duplicated_helper = bool(re.search(r"^def\s+format_period_label\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-6a: core والتنسيق موجودان في swaps.py فقط",
        "PASS" if not missing and not duplicated_core and not duplicated_helper else "FAIL",
        "export_confirmed_swaps_excel_core و format_period_label في swaps.py ولا توجد نسخ مكررة في app.py." if not missing and not duplicated_core and not duplicated_helper else f"missing={missing}, duplicated_core={duplicated_core}, duplicated_helper={duplicated_helper}",
    )

    wrapper_body = function_body(app_text, "export_confirmed_swaps_excel")
    wrapper_ok = (
        "export_confirmed_swaps_excel_core()" in wrapper_body
        and "return gr.update(value=filename)" in wrapper_body
    )
    add(
        results,
        "3I-a-6a: wrapper يحافظ على عقد gr.update المفرد",
        "PASS" if wrapper_ok else "FAIL",
        "wrapper يستدعي core ويرجع gr.update(value=filename) فقط." if wrapper_ok else "wrapper لا يطابق العقد المتوقع.",
    )

    core_body = function_body(swaps_text, "export_confirmed_swaps_excel_core")
    relative_filename_ok = "filename = f\"سجل_التبادلات_الودية_المعتمدة_" in core_body and "EXPORTS_DIR" not in core_body
    excel_writer_ok = "pd.ExcelWriter(filename, engine='openpyxl')" in core_body or 'pd.ExcelWriter(filename, engine="openpyxl")' in core_body
    add(
        results,
        "3I-a-6a: الحفاظ على اسم ملف Excel النسبي",
        "PASS" if relative_filename_ok and excel_writer_ok else "FAIL",
        "core يكتب نفس اسم الملف النسبي القديم ولا يستخدم EXPORTS_DIR." if relative_filename_ok and excel_writer_ok else f"relative_filename_ok={relative_filename_ok}, excel_writer_ok={excel_writer_ok}",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+export_confirmed_swaps_excel_core\s*\(", swaps_text))
    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-6a: core بلا @state_locked و swaps.py بلا Gradio/import app",
        "PASS" if no_state_lock and no_reverse_import and swaps_no_gradio else "FAIL",
        "لا توجد @state_locked ولا اعتماد Gradio أو app داخل swaps.py." if no_state_lock and no_reverse_import and swaps_no_gradio else f"no_state_lock={no_state_lock}, no_reverse_import={no_reverse_import}, swaps_no_gradio={swaps_no_gradio}",
    )

    deps_ok = all(name in swaps_text for name in ["import pandas as pd", "PatternFill", "Font", "Alignment", "swap_db", "get_now_oman"])
    add(
        results,
        "3I-a-6a: اعتماديات Excel متوفرة في swaps.py",
        "PASS" if deps_ok else "FAIL",
        "pandas و openpyxl styles و swap_db و get_now_oman متوفرة للـcore." if deps_ok else "تنقص إحدى اعتماديات تصدير Excel.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "export_confirmed_swaps_excel":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        wrapper_single = bool(wrapper_returns) and all(not isinstance(r.value, ast.Tuple) for r in wrapper_returns)
        add(
            results,
            "3I-a-6a: wrapper يرجع عنصرًا واحدًا",
            "PASS" if wrapper_single else "FAIL",
            "export_confirmed_swaps_excel يرجع gr.update مفردًا كما في العقد القديم." if wrapper_single else "wrapper لا يرجع عنصرًا مفردًا.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-6a: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "export_confirmed_swaps_excel_core":
                core_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        core_single = bool(core_returns) and all(not isinstance(r.value, ast.Tuple) for r in core_returns)
        returns_none_or_filename = all(
            isinstance(r.value, (ast.Constant, ast.Name)) for r in core_returns
        )
        add(
            results,
            "3I-a-6a: core يرجع قيمة خامة مفردة",
            "PASS" if core_single and returns_none_or_filename else "FAIL",
            "core يرجع None أو filename فقط دون tuple أو gr.update." if core_single and returns_none_or_filename else f"core_single={core_single}, returns_none_or_filename={returns_none_or_filename}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-6a: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(app_path), doraise=True)
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-6a: py_compile app.py و swaps.py", "PASS", "app.py و swaps.py بلا أخطاء نحوية بعد 3I-a-6a.")
    except Exception as exc:
        add(results, "3I-a-6a: py_compile app.py و swaps.py", "FAIL", f"فشل py_compile: {exc}")



def check_generate_swap_table_image_phase3ia6b(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3I-a-6b: تقسيم generate_swap_table_image إلى core/wrapper."""
    swaps_path = app_path.with_name("swaps.py")
    if not swaps_path.exists():
        add(results, "3I-a-6b: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return

    swaps_text = swaps_path.read_text(encoding="utf-8")

    required = ["generate_swap_table_image_core", "get_date_of_weekday"]
    missing = [fn for fn in required if not re.search(rf"^def\s+{re.escape(fn)}\s*\(", swaps_text, re.MULTILINE)]
    duplicated_core = bool(re.search(r"^def\s+generate_swap_table_image_core\s*\(", app_text, re.MULTILINE))
    add(
        results,
        "3I-a-6b: core ودالة التاريخ موجودان في swaps.py",
        "PASS" if not missing and not duplicated_core else "FAIL",
        "generate_swap_table_image_core و get_date_of_weekday متوفران في swaps.py ولا توجد core مكررة في app.py." if not missing and not duplicated_core else f"missing={missing}, duplicated_core={duplicated_core}",
    )

    app_date_def = bool(re.search(r"^def\s+get_date_of_weekday\s*\(", app_text, re.MULTILINE))
    app_imports_date = "get_date_of_weekday," in app_text and "from swaps import" in app_text
    add(
        results,
        "3I-a-6b Fix: get_date_of_weekday مصدر واحد",
        "PASS" if not app_date_def and app_imports_date else "FAIL",
        "get_date_of_weekday لم تعد معرفة محليًا في app.py وتُستخدم من swaps.py كمصدر واحد." if not app_date_def and app_imports_date else f"app_date_def={app_date_def}, app_imports_date={app_imports_date}",
    )

    wrapper_body = function_body(app_text, "generate_swap_table_image")
    wrapper_ok = (
        "generate_swap_table_image_core(" in wrapper_body
        and "return gr.update(value=filename)" in wrapper_body
        and "SYSTEM_NAME" in wrapper_body
        and "SYSTEM_SUBTITLE" in wrapper_body
        and "THEME_COLOR" in wrapper_body
        and "ACCENT_COLOR" in wrapper_body
    )
    add(
        results,
        "3I-a-6b: wrapper يمرر الهوية الديناميكية ويرجع gr.update مفردًا",
        "PASS" if wrapper_ok else "FAIL",
        "wrapper يمرر SYSTEM_NAME/SYSTEM_SUBTITLE/THEME_COLOR/ACCENT_COLOR للـcore ويرجع gr.update(value=filename)." if wrapper_ok else "wrapper لا يحافظ على تمرير الهوية الديناميكية أو عقد gr.update المفرد.",
    )

    core_body = function_body(swaps_text, "generate_swap_table_image_core")
    relative_image_ok = "filename = os.path.join(" in core_body and "SWAP_IMG_DIR" in core_body and "swap_table_" in core_body
    no_exports_dir = "EXPORTS_DIR" not in core_body
    add(
        results,
        "3I-a-6b: الحفاظ على مسار/اسم صورة التبادل",
        "PASS" if relative_image_ok and no_exports_dir else "FAIL",
        "core يحفظ الصورة في SWAP_IMG_DIR بنفس نمط الاسم swap_table_ ولا يستخدم EXPORTS_DIR." if relative_image_ok and no_exports_dir else f"relative_image_ok={relative_image_ok}, no_exports_dir={no_exports_dir}",
    )

    deps_ok = all(token in swaps_text for token in [
        "from PIL import Image, ImageDraw, ImageFont",
        "from config import APP_DIR",
        "SWAP_IMG_DIR",
        "ensure_data_directories",
        "image_font_path",
        "font_path",
        "get_now_oman",
    ])
    add(
        results,
        "3I-a-6b: اعتماديات الصورة متوفرة في swaps.py",
        "PASS" if deps_ok else "FAIL",
        "PIL و APP_DIR و SWAP_IMG_DIR و ensure_data_directories والخطوط متوفرة للـcore." if deps_ok else "تنقص إحدى اعتماديات توليد الصورة.",
    )

    no_state_lock = not bool(re.search(r"@state_locked\s*\ndef\s+generate_swap_table_image_core\s*\(", swaps_text))
    no_reverse_import = "import app" not in swaps_text and "from app import" not in swaps_text
    swaps_no_gradio = "gr.update" not in swaps_text and "import gradio" not in swaps_text and "from gradio" not in swaps_text and "gr.SelectData" not in swaps_text
    add(
        results,
        "3I-a-6b: core بلا @state_locked و swaps.py بلا Gradio/import app",
        "PASS" if no_state_lock and no_reverse_import and swaps_no_gradio else "FAIL",
        "لا توجد @state_locked ولا اعتماد Gradio أو app داخل swaps.py." if no_state_lock and no_reverse_import and swaps_no_gradio else f"no_state_lock={no_state_lock}, no_reverse_import={no_reverse_import}, swaps_no_gradio={swaps_no_gradio}",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_returns = []
        for node in ast.walk(app_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_swap_table_image":
                wrapper_returns = [r for r in ast.walk(node) if isinstance(r, ast.Return)]
        wrapper_single = bool(wrapper_returns) and all(not isinstance(r.value, ast.Tuple) for r in wrapper_returns)
        add(
            results,
            "3I-a-6b: wrapper يرجع عنصرًا واحدًا",
            "PASS" if wrapper_single else "FAIL",
            "generate_swap_table_image يرجع gr.update مفردًا كما في العقد القديم." if wrapper_single else "wrapper لا يرجع عنصرًا مفردًا.",
        )
    except SyntaxError as exc:
        add(results, "3I-a-6b: تحليل AST للـwrapper", "FAIL", f"تعذر تحليل app.py: {exc}")

    try:
        swaps_tree = ast.parse(swaps_text)
        core_returns = []
        for node in ast.walk(swaps_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_swap_table_image_core":
                core_returns = [child for child in node.body if isinstance(child, ast.Return)]
                for child in node.body:
                    if isinstance(child, ast.If):
                        core_returns.extend(r for r in child.body + child.orelse if isinstance(r, ast.Return))
                    if isinstance(child, ast.Try):
                        core_returns.extend(r for r in child.body + child.orelse + child.finalbody if isinstance(r, ast.Return))
                        for handler in child.handlers:
                            core_returns.extend(r for r in handler.body if isinstance(r, ast.Return))
        core_single = bool(core_returns) and all(not isinstance(r.value, ast.Tuple) for r in core_returns)
        returns_raw = all(isinstance(r.value, (ast.Constant, ast.Name)) for r in core_returns)
        add(
            results,
            "3I-a-6b: core يرجع قيمة خامة مفردة",
            "PASS" if core_single and returns_raw else "FAIL",
            "core يرجع None أو filename فقط دون tuple أو gr.update." if core_single and returns_raw else f"core_single={core_single}, returns_raw={returns_raw}",
        )
    except SyntaxError as exc:
        add(results, "3I-a-6b: تحليل AST للـcore", "FAIL", f"تعذر تحليل swaps.py: {exc}")

    try:
        py_compile.compile(str(app_path), doraise=True)
        py_compile.compile(str(swaps_path), doraise=True)
        add(results, "3I-a-6b: py_compile app.py و swaps.py", "PASS", "app.py و swaps.py بلا أخطاء نحوية بعد 3I-a-6b.")
    except Exception as exc:
        add(results, "3I-a-6b: py_compile app.py و swaps.py", "FAIL", f"فشل py_compile: {exc}")


def check_distribution_phase3ja1(app_path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-a1: إنشاء distribution.py ونقل الدوال النظيفة دون Gradio أو تكرار."""
    distribution_path = app_path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-a1: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    add(results, "3J-a1: وجود distribution.py", "PASS", f"موجود: {distribution_path}")

    moved_functions = [
        "get_falcon_eye_candidates",
        "format_sub_display",
        "format_sub_display_for_image",
        "normalize_absent_names",
        "build_generation_signature",
        "same_generation_context",
        "get_empty_generation_state",
        "get_existing_absents_for_context",
        "detect_conflicted_absence_slots",
        "build_absence_conflict_warning_html",
        "get_teacher_schedule_choices",
        "resolve_teacher_display_value",
        "resolve_teacher_display_values",
        "get_dynamic_header",
        "get_initial_header",
    ]

    missing_in_distribution = [
        name for name in moved_functions
        if not re.search(rf"^def\s+{re.escape(name)}\s*\(", distribution_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a1: الدوال النظيفة موجودة في distribution.py",
        "PASS" if not missing_in_distribution else "FAIL",
        "كل الدوال الـ15 موجودة في distribution.py." if not missing_in_distribution else f"ناقص: {missing_in_distribution}",
    )

    duplicated_in_app = [
        name for name in moved_functions
        if re.search(rf"^def\s+{re.escape(name)}\s*\(", app_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a1: لا توجد نسخ مكررة في app.py",
        "PASS" if not duplicated_in_app else "FAIL",
        "لا توجد تعريفات محلية للدوال المنقولة داخل app.py." if not duplicated_in_app else f"مكررة في app.py: {duplicated_in_app}",
    )

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    offenders = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            offenders.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-a1: distribution.py نظيف من Gradio و app.py",
        "PASS" if not offenders else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not offenders else "; ".join(offenders),
    )

    has_import = "from distribution import" in app_text
    add(
        results,
        "3J-a1: app.py يستورد من distribution.py",
        "PASS" if has_import else "FAIL",
        "يوجد from distribution import داخل app.py." if has_import else "لا يوجد استيراد من distribution.py داخل app.py.",
    )

    required_imports = [
        "teachers_db",
        "daily_db",
        "SCHOOL_WEEK_DAYS",
        "resolve_effective_dept",
        "format_teacher_name",
        "from exemptions import is_teacher_exempt_for_slot",
        "get_date_of_weekday",
        "get_current_day_oman",
        "get_class_dna",
        "check_teacher_load",
    ]
    missing_imports = [marker for marker in required_imports if marker not in distribution_text]
    add(
        results,
        "3J-a1: اعتماديات distribution.py الصريحة موجودة",
        "PASS" if not missing_imports else "FAIL",
        "كل استيرادات 3J-a1 المطلوبة موجودة." if not missing_imports else f"ناقص: {missing_imports}",
    )

    locked_defs = []
    try:
        tree = ast.parse(distribution_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in node.decorator_list):
                    locked_defs.append(node.name)
    except SyntaxError as exc:
        add(results, "3J-a1: تحليل AST للـdistribution.py", "FAIL", f"تعذر التحليل: {exc}")
        return

    allowed_locked_defs = {"assign_logic_core", "rollback_auto_assignments_for_absentees_core", "cancel_teacher_absence_core", "process_admin_action_core", "update_manual_count_core", "reset_monthly_balances_core", "add_manual_staff_core", "delete_single_teacher_core"}
    unexpected_locked_defs = [name for name in locked_defs if name not in allowed_locked_defs]
    add(
        results,
        "3J-a1: لا توجد @state_locked في الدوال النظيفة",
        "PASS" if not unexpected_locked_defs else "FAIL",
        "لا توجد @state_locked غير مبررة داخل distribution.py." if not unexpected_locked_defs else f"دوال مقفلة دون حاجة: {unexpected_locked_defs}",
    )

    try:
        py_compile.compile(str(distribution_path), doraise=True)
        add(results, "3J-a1: py_compile distribution.py", "PASS", "distribution.py بلا أخطاء نحوية.")
    except Exception as exc:
        add(results, "3J-a1: py_compile distribution.py", "FAIL", f"فشل py_compile: {exc}")

def check_distribution_phase3ja2fix(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-a2-fix: نقل الدوال النظيفة المتبقية التي يعتمد عليها refresh_ui_on_change."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-a2-fix: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    moved_functions = [
        "detect_absence_assignment_conflicts_for_context",
        "generate_styled_html_table",
        "generate_whatsapp_html",
    ]

    missing_in_distribution = [
        name for name in moved_functions
        if not re.search(rf"^def\s+{re.escape(name)}\s*\(", distribution_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a2-fix: الدوال الثلاث المتبقية موجودة في distribution.py",
        "PASS" if not missing_in_distribution else "FAIL",
        "الدوال الثلاث موجودة في distribution.py." if not missing_in_distribution else f"ناقص: {missing_in_distribution}",
    )

    duplicated_in_app = [
        name for name in moved_functions
        if re.search(rf"^def\s+{re.escape(name)}\s*\(", app_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a2-fix: لا توجد نسخ مكررة للدوال الثلاث في app.py",
        "PASS" if not duplicated_in_app else "FAIL",
        "لا توجد تعريفات محلية للدوال الثلاث داخل app.py." if not duplicated_in_app else f"مكررة في app.py: {duplicated_in_app}",
    )

    required_imports = [
        "import urllib.parse",
        "format_elegant_class",
    ]
    missing_imports = [marker for marker in required_imports if marker not in distribution_text]
    add(
        results,
        "3J-a2-fix: اعتماديات الدوال الثلاث موجودة",
        "PASS" if not missing_imports else "FAIL",
        "اعتماديات urllib.parse و format_elegant_class موجودة." if not missing_imports else f"ناقص: {missing_imports}",
    )

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    offenders = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            offenders.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-a2-fix: distribution.py لا يزال نظيفًا من Gradio و app.py",
        "PASS" if not offenders else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py بعد نقل الدوال الثلاث." if not offenders else "; ".join(offenders),
    )

    has_distribution_import = all(name in app_text for name in moved_functions)
    add(
        results,
        "3J-a2-fix: app.py يستورد الدوال الثلاث من distribution.py",
        "PASS" if has_distribution_import else "FAIL",
        "أسماء الدوال الثلاث موجودة في استيراد app.py من distribution.py." if has_distribution_import else "بعض أسماء الدوال الثلاث غير موجودة في app.py.",
    )

    locked_defs = []
    try:
        tree = ast.parse(distribution_text)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in moved_functions:
                if any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in node.decorator_list):
                    locked_defs.append(node.name)
    except SyntaxError as exc:
        add(results, "3J-a2-fix: تحليل AST للـdistribution.py", "FAIL", f"تعذر التحليل: {exc}")
        return

    add(
        results,
        "3J-a2-fix: الدوال الثلاث بلا @state_locked",
        "PASS" if not locked_defs else "FAIL",
        "الدوال الثلاث قراءة/تنسيق فقط ولا تحتوي @state_locked." if not locked_defs else f"دوال مقفلة دون حاجة: {locked_defs}",
    )

    heavy_must_stay = [
        "refresh_ui_on_change",
        "assign_logic",
        "update_available_subs_smart",
        "draw_schedule_image",
    ]
    heavy_in_distribution = [
        name for name in heavy_must_stay
        if re.search(rf"^def\s+{re.escape(name)}\s*\(", distribution_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a2-fix: الدوال الثقيلة ما زالت خارج distribution.py",
        "PASS" if not heavy_in_distribution else "FAIL",
        "refresh_ui_on_change والدوال الثقيلة لم تُنقل في هذه المرحلة." if not heavy_in_distribution else f"نُقلت بالخطأ: {heavy_in_distribution}",
    )

def summarize(results: list[CheckResult]) -> tuple[int, int, int, int]:
    fail = sum(r.status == "FAIL" for r in results)
    warn = sum(r.status == "WARN" for r in results)
    passed = sum(r.status == "PASS" for r in results)
    info = sum(r.status == "INFO" for r in results)
    return passed, warn, fail, info


def print_results(results: list[CheckResult]) -> None:
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️", "INFO": "ℹ️"}
    print("\n=== Masar Safety Check ===\n")
    for r in results:
        print(f"{icon.get(r.status, '•')} [{r.status}] {r.name}")
        print(f"    {r.detail}")
    passed, warn, fail, info = summarize(results)
    print("\n=== Summary ===")
    print(f"PASS: {passed} | WARN: {warn} | FAIL: {fail} | INFO: {info}")
    if fail:
        print("\nالنتيجة: فشل الفحص. لا ترفع الملف قبل معالجة الأخطاء. لا تجعل GitHub دفتر اعترافات للكوارث.")
    elif warn:
        print("\nالنتيجة: ناجح مع تحذيرات. راجع التحذيرات يدويًا قبل الرفع.")
    else:
        print("\nالنتيجة: ناجح. الملف اجتاز شبكة الأمان الأساسية.")



def check_distribution_phase3ja3(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-a3: تقسيم refresh_ui_on_change إلى core/wrapper مع عقد 27 عنصرًا."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-a3: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    core_in_distribution = re.search(r"^def\s+refresh_ui_on_change_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+refresh_ui_on_change_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_app = re.search(r"^def\s+refresh_ui_on_change\s*\(", app_text, flags=re.MULTILINE) is not None

    add(
        results,
        "3J-a3: core موجودة في distribution.py فقط",
        "PASS" if core_in_distribution and not core_in_app else "FAIL",
        "refresh_ui_on_change_core موجودة في distribution.py ولا توجد نسخة في app.py." if core_in_distribution and not core_in_app else "core غير موجودة أو مكررة في app.py.",
    )
    add(
        results,
        "3J-a3: wrapper باقية في app.py",
        "PASS" if wrapper_in_app else "FAIL",
        "refresh_ui_on_change باقية في app.py كـwrapper." if wrapper_in_app else "wrapper غير موجودة في app.py.",
    )

    core_body = function_body(distribution_text, "refresh_ui_on_change_core")
    wrapper_body = function_body(app_text, "refresh_ui_on_change")

    forbidden = []
    for pattern, desc in [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
    ]:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{desc} في الأسطر {lines[:10]}")
    add(
        results,
        "3J-a3: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا import app." if not forbidden else "; ".join(forbidden),
    )

    required_markers = [
        "refresh_ui_on_change_core",
        "get_day_table_updates_core",
        "get_updated_balance",
        "get_updated_absences",
        "get_updated_shortcomings",
        "load_db",
        "load_daily_db",
    ]
    missing_markers = [marker for marker in required_markers if marker not in distribution_text]
    add(
        results,
        "3J-a3: اعتماديات refresh core موجودة",
        "PASS" if not missing_markers else "FAIL",
        "اعتماديات core الأساسية موجودة في distribution.py." if not missing_markers else f"ناقص: {missing_markers}",
    )

    wrapper_uses_core = "refresh_ui_on_change_core" in wrapper_body
    wrapper_guard = "expected 27" in wrapper_body and "len(refresh_values)" in wrapper_body
    add(
        results,
        "3J-a3: wrapper يستدعي core ويحرس عقد 27",
        "PASS" if wrapper_uses_core and wrapper_guard else "FAIL",
        "wrapper يستدعي core ويتحقق من عدد 27 مخرجًا." if wrapper_uses_core and wrapper_guard else "wrapper لا يستدعي core أو لا يحرس عدد المخرجات.",
    )

    update_outputs_ok = re.search(r"update_outputs\s*=\s*\[", app_text) is not None and "error_updates = [gr.update()] * 27" in app_text
    add(
        results,
        "3J-a3: update_outputs/error_updates محفوظة",
        "PASS" if update_outputs_ok else "FAIL",
        "update_outputs موجودة و error_updates ما زالت 27." if update_outputs_ok else "لم يتم تأكيد update_outputs أو error_updates=27.",
    )

    try:
        app_tree = ast.parse(app_text)
        wrapper_node = next((n for n in app_tree.body if isinstance(n, ast.FunctionDef) and n.name == "refresh_ui_on_change"), None)
        return_counts = []
        if wrapper_node:
            for n in ast.walk(wrapper_node):
                if isinstance(n, ast.Return):
                    if isinstance(n.value, ast.Tuple):
                        return_counts.append(len(n.value.elts))
                    else:
                        return_counts.append(1)
        ok_returns = return_counts == [27]
        add(
            results,
            "3J-a3: wrapper يرجع 27 عنصرًا",
            "PASS" if ok_returns else "FAIL",
            f"أعداد عناصر return في wrapper: {return_counts}",
        )
    except Exception as exc:
        add(results, "3J-a3: تحليل AST للـwrapper", "FAIL", f"تعذر التحليل: {exc}")

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in dist_tree.body if isinstance(n, ast.FunctionDef) and n.name == "refresh_ui_on_change_core"), None)
        locked = bool(core_node and core_node.decorator_list)
        add(
            results,
            "3J-a3: core بلا @state_locked",
            "PASS" if not locked else "FAIL",
            "core بلا decorators لأنها قراءة/عرض فقط." if not locked else "core تحتوي decorator غير متوقع.",
        )
    except Exception as exc:
        add(results, "3J-a3: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")

    direct_wrapper_dependency = "get_day_table_updates(" in core_body
    direct_core_dependency = "get_day_table_updates_core" in core_body
    add(
        results,
        "3J-a3: core يستخدم get_day_table_updates_core لا wrapper",
        "PASS" if direct_core_dependency and not direct_wrapper_dependency else "FAIL",
        "core يستدعي get_day_table_updates_core مباشرة." if direct_core_dependency and not direct_wrapper_dependency else "core قد يستدعي wrapper أو لا يستدعي core المطلوب.",
    )

    heavy_names = ["draw_schedule_image"]
    moved_heavy = [
        name for name in heavy_names
        if re.search(rf"^def\s+{re.escape(name)}\s*\(", distribution_text, flags=re.MULTILINE)
    ]
    add(
        results,
        "3J-a3: الدوال الثقيلة الأخرى بقيت خارج distribution.py",
        "PASS" if not moved_heavy else "FAIL",
        "الدوال الثقيلة الأخرى لم تُنقل في هذه المرحلة." if not moved_heavy else f"نُقلت بالخطأ: {moved_heavy}",
    )


def check_distribution_phase3jb1(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-b1: تقسيم update_available_subs_smart إلى core/wrapper."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-b1: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    core_in_distribution = re.search(r"^def\s+update_available_subs_smart_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_in_app = re.search(r"^def\s+update_available_subs_smart\s*\(", app_text, flags=re.MULTILINE) is not None
    duplicated_wrapper = re.search(r"^def\s+update_available_subs_smart\s*\(", distribution_text, flags=re.MULTILINE) is not None

    add(
        results,
        "3J-b1: core موجودة في distribution.py",
        "PASS" if core_in_distribution else "FAIL",
        "update_available_subs_smart_core موجودة في distribution.py." if core_in_distribution else "core غير موجودة في distribution.py.",
    )
    add(
        results,
        "3J-b1: wrapper باقية في app.py",
        "PASS" if wrapper_in_app else "FAIL",
        "update_available_subs_smart باقية في app.py كـwrapper." if wrapper_in_app else "wrapper غير موجودة في app.py.",
    )
    add(
        results,
        "3J-b1: لا توجد wrapper مكررة في distribution.py",
        "PASS" if not duplicated_wrapper else "FAIL",
        "لا توجد update_available_subs_smart كاملة في distribution.py." if not duplicated_wrapper else "الدالة wrapper موجودة خطأ في distribution.py.",
    )

    forbidden = []
    for pattern, desc in [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{desc} في الأسطر {lines[:10]}")
    add(
        results,
        "3J-b1: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا import app." if not forbidden else "; ".join(forbidden),
    )

    core_body = function_body(distribution_text, "update_available_subs_smart_core")
    wrapper_body = function_body(app_text, "update_available_subs_smart")
    required_markers = [
        "clean_teacher_name_from_ui",
        "get_date_of_weekday",
        "daily_db",
        "teachers_db",
        "is_teacher_exempt_for_slot",
        "get_falcon_eye_candidates",
        "check_teacher_load",
    ]
    missing = [marker for marker in required_markers if marker not in core_body and marker not in distribution_text]
    add(
        results,
        "3J-b1: اعتماديات core موجودة",
        "PASS" if not missing else "FAIL",
        "اعتماديات الترشيح الذكي موجودة." if not missing else f"ناقص: {missing}",
    )

    wrapper_uses_core = "update_available_subs_smart_core" in wrapper_body
    wrapper_single_update = wrapper_body.count("gr.update") == 1 and "choices=choices" in wrapper_body and "interactive=interactive" in wrapper_body
    add(
        results,
        "3J-b1: wrapper يرجع gr.update مفردًا",
        "PASS" if wrapper_uses_core and wrapper_single_update else "FAIL",
        "wrapper يستدعي core ويرجع gr.update واحدًا بعقد choices/value/interactive." if wrapper_uses_core and wrapper_single_update else "wrapper لا يطابق العقد المفرد.",
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in dist_tree.body if isinstance(n, ast.FunctionDef) and n.name == "update_available_subs_smart_core"), None)
        locked = bool(core_node and core_node.decorator_list)
        required_return_markers = [
            "return [], None, False",
            "return [msg], msg, False",
            "return opts, None, True",
            "return [\"إشراف إداري\"], None, True",
        ]
        missing_returns = [marker for marker in required_return_markers if marker not in core_body]
        no_gradio_in_core = "gr.update" not in core_body
        add(
            results,
            "3J-b1: core بلا @state_locked وترجع قيمًا خامة",
            "PASS" if (not locked and no_gradio_in_core and not missing_returns) else "FAIL",
            "core بلا decorators وبلا gr.update وتستخدم عقد choices/value/interactive الخام." if (not locked and no_gradio_in_core and not missing_returns) else f"decorators={locked}, no_gradio={no_gradio_in_core}, missing={missing_returns}",
        )
    except Exception as exc:
        add(results, "3J-b1: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")


def check_assign_prereqs_phase3jc1fix(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-c1-fix: تجهيز اعتماديات assign_logic قبل تقسيمها."""
    storage_path = path.with_name("storage.py")
    distribution_path = path.with_name("distribution.py")
    if not storage_path.exists():
        add(results, "3J-c1-fix: وجود storage.py", "FAIL", f"غير موجود: {storage_path}")
        return

    try:
        storage_text = storage_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        storage_text = storage_path.read_text(encoding="utf-8-sig")

    distribution_text = ""
    if distribution_path.exists():
        try:
            distribution_text = distribution_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    has_state = re.search(r"^last_assigned_teachers\s*=\s*\[\s*\]", storage_text, flags=re.MULTILINE) is not None
    imported_in_app = "last_assigned_teachers" in app_text and "from storage import" in app_text
    add(
        results,
        "3J-c1-fix: last_assigned_teachers في storage.py",
        "PASS" if has_state and imported_in_app else "FAIL",
        "last_assigned_teachers معرفة في storage.py ومستوردة في app.py." if has_state and imported_in_app else f"has_state={has_state}, imported_in_app={imported_in_app}",
    )

    app_local_state_def = line_numbers_for_pattern(app_text, r"^last_assigned_teachers\s*=")
    app_indented_reassign = line_numbers_for_pattern(app_text, r"^\s+last_assigned_teachers\s*=")
    distribution_reassign = line_numbers_for_pattern(distribution_text, r"^\s*last_assigned_teachers\s*=") if distribution_text else []
    add(
        results,
        "3J-c1-fix: منع إعادة تعيين last_assigned_teachers خارج storage.py",
        "PASS" if not app_local_state_def and not app_indented_reassign and not distribution_reassign else "FAIL",
        "لا توجد إعادة تعيين مباشرة في app.py أو distribution.py." if not app_local_state_def and not app_indented_reassign and not distribution_reassign else f"app top={app_local_state_def[:10]}, app indented={app_indented_reassign[:10]}, distribution={distribution_reassign[:10]}",
    )

    no_global_last = "global last_assigned_teachers" not in app_text and "global last_assigned_teachers" not in distribution_text
    combined_state_text = app_text + "\n" + distribution_text
    clear_count = combined_state_text.count("last_assigned_teachers.clear()")
    extend_count = combined_state_text.count("last_assigned_teachers.extend(")
    add(
        results,
        "3J-c1-fix: last_assigned_teachers يستخدم in-place mutation",
        "PASS" if no_global_last and clear_count >= 3 and extend_count >= 1 else "FAIL",
        f"global ممنوع={not no_global_last}, clear={clear_count}, extend={extend_count}.",
    )

    storage_has_queue = re.search(r"^def\s+_queue_audit_change\s*\(", storage_text, flags=re.MULTILINE) is not None
    storage_has_flush = re.search(r"^def\s+_flush_audit_changes\s*\(", storage_text, flags=re.MULTILINE) is not None
    app_has_queue_def = re.search(r"^def\s+_queue_audit_change\s*\(", app_text, flags=re.MULTILINE) is not None
    app_has_flush_def = re.search(r"^def\s+_flush_audit_changes\s*\(", app_text, flags=re.MULTILINE) is not None
    app_imports_audit_helpers = "_queue_audit_change" in app_text and "_flush_audit_changes" in app_text and "from storage import" in app_text
    add(
        results,
        "3J-c1-fix: audit queue helpers في storage.py فقط",
        "PASS" if storage_has_queue and storage_has_flush and not app_has_queue_def and not app_has_flush_def and app_imports_audit_helpers else "FAIL",
        "_queue_audit_change/_flush_audit_changes موجودتان في storage.py ومستورَدتان دون تعريف محلي في app.py." if storage_has_queue and storage_has_flush and not app_has_queue_def and not app_has_flush_def and app_imports_audit_helpers else f"storage_queue={storage_has_queue}, storage_flush={storage_has_flush}, app_queue_def={app_has_queue_def}, app_flush_def={app_has_flush_def}, imported={app_imports_audit_helpers}",
    )

    no_app_import_in_storage = "import app" not in storage_text and "from app import" not in storage_text
    audit_uses_write = "write_audit_log(" in function_body(storage_text, "_flush_audit_changes")
    add(
        results,
        "3J-c1-fix: storage.py بلا اعتماد عكسي و audit يستخدم write_audit_log",
        "PASS" if no_app_import_in_storage and audit_uses_write else "FAIL",
        "storage.py لا يستورد app.py و_flush_audit_changes يستدعي write_audit_log." if no_app_import_in_storage and audit_uses_write else f"no_app_import={no_app_import_in_storage}, audit_uses_write={audit_uses_write}",
    )


def check_assign_logic_phase3jc2(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-c2: تقسيم assign_logic إلى core/wrapper بالخيار C."""
    distribution_path = path.with_name("distribution.py")
    storage_path = path.with_name("storage.py")
    if not distribution_path.exists():
        add(results, "3J-c2: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")
    try:
        storage_text = storage_path.read_text(encoding="utf-8") if storage_path.exists() else ""
    except UnicodeDecodeError:
        storage_text = storage_path.read_text(encoding="utf-8-sig")

    core_exists = re.search(r"^def\s+assign_logic_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+assign_logic\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+assign_logic_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_distribution = re.search(r"^def\s+assign_logic\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(
        results,
        "3J-c2: core/wrapper في المواضع الصحيحة",
        "PASS" if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else "FAIL",
        "assign_logic_core في distribution.py وassign_logic wrapper في app.py دون تكرار." if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else f"core_exists={core_exists}, wrapper_exists={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_distribution={wrapper_in_distribution}",
    )

    core_body = function_body(distribution_text, "assign_logic_core")
    wrapper_body = function_body(app_text, "assign_logic")

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    forbidden = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-c2: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden),
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "assign_logic_core"), None)
        has_locked = bool(core_node and any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in core_node.decorator_list))
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        returns_dict = bool(returns) and all(isinstance(r.value, ast.Dict) for r in returns)
    except Exception as exc:
        add(results, "3J-c2: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")
        has_locked = False
        returns_dict = False
    add(
        results,
        "3J-c2: core مقفلة وترجع dict خامًا",
        "PASS" if has_locked and returns_dict else "FAIL",
        "assign_logic_core عليها @state_locked وترجع dict خامًا." if has_locked and returns_dict else f"has_locked={has_locked}, returns_dict={returns_dict}",
    )

    wrapper_has_locked = "@state_locked\ndef assign_logic" in app_text or "@state_locked\r\ndef assign_logic" in app_text
    wrapper_markers = [
        "assign_logic_core(",
        "refresh_ui_on_change(",
        'result["refresh_dept"]',
        'result["refresh_day"]',
        'result["refresh_is_admin"]',
        'result.get("refresh_current_abs")',
    ]
    missing_wrapper = [m for m in wrapper_markers if m not in wrapper_body]
    add(
        results,
        "3J-c2: wrapper يستدعي core ثم refresh_ui_on_change",
        "PASS" if not wrapper_has_locked and not missing_wrapper else "FAIL",
        "wrapper بلا @state_locked ويستدعي core ثم refresh_ui_on_change بالقيم الأربع." if not wrapper_has_locked and not missing_wrapper else f"wrapper_locked={wrapper_has_locked}, missing={missing_wrapper}",
    )

    required_core_markers = [
        "daily_db.clear()",
        "daily_db.extend(",
        "processed_absences.add(",
        "last_assigned_teachers.clear()",
        "last_assigned_teachers.extend(",
        "save_db()",
        "save_daily_db()",
        "_queue_audit_change(",
        "_flush_audit_changes(",
        "is_teacher_exempt_for_slot(",
    ]
    missing_core = [m for m in required_core_markers if m not in core_body]
    add(
        results,
        "3J-c2: منطق state/audit محفوظ داخل core",
        "PASS" if not missing_core else "FAIL",
        "core يحتوي منطق daily_db/processed_absences/last_assigned/audit والحفظ." if not missing_core else f"ناقص: {missing_core}",
    )

    combined = app_text + "\n" + distribution_text
    direct_reassign = line_numbers_for_pattern(combined, r"^\s+last_assigned_teachers\s*=")
    top_reassign_app = line_numbers_for_pattern(app_text, r"^last_assigned_teachers\s*=")
    top_reassign_dist = line_numbers_for_pattern(distribution_text, r"^last_assigned_teachers\s*=")
    add(
        results,
        "3J-c2: لا إعادة تعيين last_assigned_teachers",
        "PASS" if not direct_reassign and not top_reassign_app and not top_reassign_dist else "FAIL",
        "لا توجد إعادة تعيين مباشرة لـ last_assigned_teachers." if not direct_reassign and not top_reassign_app and not top_reassign_dist else f"direct={direct_reassign[:10]}, app_top={top_reassign_app[:10]}, dist_top={top_reassign_dist[:10]}",
    )

    required_imports = [
        "processed_absences",
        "last_assigned_teachers",
        "save_db",
        "save_daily_db",
        "state_locked",
        "_queue_audit_change",
        "_flush_audit_changes",
        "random",
    ]
    missing_imports = [m for m in required_imports if m not in distribution_text]
    add(
        results,
        "3J-c2: اعتماديات assign_logic_core موجودة",
        "PASS" if not missing_imports else "FAIL",
        "اعتماديات core الأساسية موجودة في distribution.py." if not missing_imports else f"ناقص: {missing_imports}",
    )

    no_local_audit_in_app = not re.search(r"^def\s+_queue_audit_change\s*\(", app_text, flags=re.MULTILINE) and not re.search(r"^def\s+_flush_audit_changes\s*\(", app_text, flags=re.MULTILINE)
    audit_in_storage = "def _queue_audit_change" in storage_text and "def _flush_audit_changes" in storage_text
    add(
        results,
        "3J-c2: audit helpers ما زالت في storage.py",
        "PASS" if no_local_audit_in_app and audit_in_storage else "FAIL",
        "audit helpers في storage.py ولا توجد تعريفات محلية في app.py." if no_local_audit_in_app and audit_in_storage else f"no_local={no_local_audit_in_app}, in_storage={audit_in_storage}",
    )


def check_cancel_teacher_absence_phase3jc3(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-c3: تقسيم cancel_teacher_absence إلى core/wrapper بالخيار C."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-c3: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    core_exists = re.search(r"^def\s+cancel_teacher_absence_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+cancel_teacher_absence\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+cancel_teacher_absence_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_distribution = re.search(r"^def\s+cancel_teacher_absence\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(
        results,
        "3J-c3: core/wrapper في المواضع الصحيحة",
        "PASS" if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else "FAIL",
        "cancel_teacher_absence_core في distribution.py وcancel_teacher_absence wrapper في app.py دون تكرار." if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else f"core_exists={core_exists}, wrapper_exists={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_distribution={wrapper_in_distribution}",
    )

    core_body = function_body(distribution_text, "cancel_teacher_absence_core")
    wrapper_body = function_body(app_text, "cancel_teacher_absence")

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    forbidden = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-c3: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden),
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "cancel_teacher_absence_core"), None)
        has_locked = bool(core_node and any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in core_node.decorator_list))
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        returns_dict = bool(returns) and all(isinstance(r.value, ast.Dict) for r in returns)
    except Exception as exc:
        add(results, "3J-c3: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")
        has_locked = False
        returns_dict = False
    add(
        results,
        "3J-c3: core مقفلة وترجع dict خامًا",
        "PASS" if has_locked and returns_dict else "FAIL",
        "cancel_teacher_absence_core عليها @state_locked وترجع dict خامًا." if has_locked and returns_dict else f"has_locked={has_locked}, returns_dict={returns_dict}",
    )

    wrapper_has_locked = "@state_locked\ndef cancel_teacher_absence" in app_text or "@state_locked\r\ndef cancel_teacher_absence" in app_text
    wrapper_markers = [
        "cancel_teacher_absence_core(",
        "refresh_ui_on_change(",
        'result["refresh_dept"]',
        'result["refresh_day"]',
        'result["refresh_is_admin"]',
        'result.get("refresh_current_abs")',
    ]
    missing_wrapper = [m for m in wrapper_markers if m not in wrapper_body]
    add(
        results,
        "3J-c3: wrapper يستدعي core ثم refresh_ui_on_change",
        "PASS" if not wrapper_has_locked and not missing_wrapper else "FAIL",
        "wrapper بلا @state_locked ويستدعي core ثم refresh_ui_on_change بالقيم الأربع." if not wrapper_has_locked and not missing_wrapper else f"wrapper_locked={wrapper_has_locked}, missing={missing_wrapper}",
    )

    required_core_markers = [
        "daily_db.clear()",
        "daily_db.extend(",
        "processed_absences.remove(",
        "teachers_db[",
        "save_db()",
        "save_daily_db()",
        "_queue_audit_change(",
        "_flush_audit_changes(",
        "clean_teacher_name_from_ui(",
        "get_date_of_weekday(",
    ]
    missing_core = [m for m in required_core_markers if m not in core_body]
    add(
        results,
        "3J-c3: منطق إلغاء الغياب/audit محفوظ داخل core",
        "PASS" if not missing_core else "FAIL",
        "core يحتوي منطق daily_db/teachers_db/processed_absences/audit والحفظ." if not missing_core else f"ناقص: {missing_core}",
    )

    heavy_still_outside = all(marker not in distribution_text for marker in [
        "def draw_schedule_image",
    ])
    add(
        results,
        "3J-c3: الدوال الثقيلة الأخرى بقيت خارج distribution.py",
        "PASS" if heavy_still_outside else "FAIL",
        "draw_schedule_image لم تُنقل في هذه المرحلة." if heavy_still_outside else "وجدت دوال ثقيلة غير مستهدفة داخل distribution.py.",
    )

    no_last_assigned = "last_assigned_teachers" not in core_body
    add(
        results,
        "3J-c3: cancel core لا يلمس last_assigned_teachers",
        "PASS" if no_last_assigned else "FAIL",
        "cancel_teacher_absence_core لا يلمس last_assigned_teachers كما هو متوقع." if no_last_assigned else "وجد last_assigned_teachers داخل core.",
    )



def check_process_admin_action_phase3jd1(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-d1: تقسيم process_admin_action إلى core/wrapper بالخيار C."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-d1: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return

    try:
        distribution_text = distribution_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        distribution_text = distribution_path.read_text(encoding="utf-8-sig")

    core_exists = re.search(r"^def\s+process_admin_action_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+process_admin_action\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+process_admin_action_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_distribution = re.search(r"^def\s+process_admin_action\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(
        results,
        "3J-d1: core/wrapper في المواضع الصحيحة",
        "PASS" if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else "FAIL",
        "process_admin_action_core في distribution.py وprocess_admin_action wrapper في app.py دون تكرار." if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else f"core_exists={core_exists}, wrapper_exists={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_distribution={wrapper_in_distribution}",
    )

    core_body = function_body(distribution_text, "process_admin_action_core")
    wrapper_body = function_body(app_text, "process_admin_action")

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    forbidden = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-d1: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden),
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "process_admin_action_core"), None)
        has_locked = bool(core_node and any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in core_node.decorator_list))
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        returns_dict = bool(returns) and all(isinstance(r.value, ast.Dict) for r in returns)
    except Exception as exc:
        add(results, "3J-d1: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")
        has_locked = False
        returns_dict = False
    add(
        results,
        "3J-d1: core مقفلة وترجع dict خامًا",
        "PASS" if has_locked and returns_dict else "FAIL",
        "process_admin_action_core عليها @state_locked وترجع dict خامًا." if has_locked and returns_dict else f"has_locked={has_locked}, returns_dict={returns_dict}",
    )

    wrapper_has_locked = "@state_locked\ndef process_admin_action" in app_text or "@state_locked\r\ndef process_admin_action" in app_text
    wrapper_markers = [
        "process_admin_action_core(",
        "refresh_ui_on_change(",
        'result["refresh_dept"]',
        'result["refresh_day"]',
        'result["refresh_is_admin"]',
        'result.get("refresh_current_abs")',
    ]
    missing_wrapper = [m for m in wrapper_markers if m not in wrapper_body]
    add(
        results,
        "3J-d1: wrapper يستدعي core ثم refresh_ui_on_change",
        "PASS" if not wrapper_has_locked and not missing_wrapper else "FAIL",
        "wrapper بلا @state_locked ويستدعي core ثم refresh_ui_on_change بالقيم الأربع." if not wrapper_has_locked and not missing_wrapper else f"wrapper_locked={wrapper_has_locked}, missing={missing_wrapper}",
    )

    required_core_markers = [
        "teachers_db[",
        "daily_db",
        "save_db()",
        "save_daily_db()",
        "_queue_audit_change(",
        "_flush_audit_changes(",
        "clean_teacher_name_from_ui(",
        "get_date_of_weekday(",
        'action_type == "penalty"',
        'action_type == "tabadul"',
        'action_type == "normal"',
    ]
    missing_core = [m for m in required_core_markers if m not in core_body]
    add(
        results,
        "3J-d1: منطق الإجراء الإداري/audit محفوظ داخل core",
        "PASS" if not missing_core else "FAIL",
        "core يحتوي منطق penalty/tabadul/normal وتعديل الأرصدة والحفظ/audit." if not missing_core else f"ناقص: {missing_core}",
    )

    no_last_assigned = "last_assigned_teachers" not in core_body
    add(
        results,
        "3J-d1: admin core لا يلمس last_assigned_teachers",
        "PASS" if no_last_assigned else "FAIL",
        "process_admin_action_core لا يلمس last_assigned_teachers كما هو متوقع." if no_last_assigned else "وجد last_assigned_teachers داخل core.",
    )

    still_outside = all(marker not in distribution_text for marker in [
        "def draw_schedule_image",
        "def run_main_generation",
        "def run_full_regeneration",
    ])
    add(
        results,
        "3J-d1: الدوال الثقيلة غير المستهدفة بقيت خارج distribution.py",
        "PASS" if still_outside else "FAIL",
        "draw_schedule_image/run_generation لم تُنقل في هذه المرحلة." if still_outside else "وجدت دوال ثقيلة غير مستهدفة داخل distribution.py.",
    )


def check_update_manual_count_phase3jd2(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-d2: تقسيم update_manual_count إلى core/wrapper بحمولة خام من 8 عناصر."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-d2: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return
    distribution_text = read_text(distribution_path)

    core_exists = re.search(r"^def\s+update_manual_count_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+update_manual_count\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+update_manual_count_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_distribution = re.search(r"^def\s+update_manual_count\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(
        results,
        "3J-d2: core/wrapper في المواضع الصحيحة",
        "PASS" if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else "FAIL",
        "update_manual_count_core في distribution.py وupdate_manual_count wrapper في app.py دون تكرار." if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else f"core_exists={core_exists}, wrapper_exists={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_distribution={wrapper_in_distribution}",
    )

    core_body = function_body(distribution_text, "update_manual_count_core")
    wrapper_body = function_body(app_text, "update_manual_count")

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    forbidden = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-d2: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden),
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "update_manual_count_core"), None)
        has_locked = bool(core_node and any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in core_node.decorator_list))
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        returns_dict = bool(returns) and all(isinstance(r.value, ast.Dict) or (isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == "build_payload") for r in returns)
    except Exception as exc:
        add(results, "3J-d2: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")
        has_locked = False
        returns_dict = False
    add(
        results,
        "3J-d2: core مقفلة وترجع حمولة خام",
        "PASS" if has_locked and returns_dict else "FAIL",
        "update_manual_count_core عليها @state_locked وترجع payload خامًا." if has_locked and returns_dict else f"has_locked={has_locked}, returns_raw={returns_dict}",
    )

    wrapper_has_locked = "@state_locked\ndef update_manual_count" in app_text or "@state_locked\r\ndef update_manual_count" in app_text
    wrapper_markers = [
        "update_manual_count_core(",
        'gr.update(value=raw["balance"])',
        'gr.update(value=raw["absences"])',
        'gr.update(value=raw["shortcomings"])',
        'gr.update(value=raw["day_overview"])',
        'raw["message"]',
        'gr.update(**raw["abs_update"])',
        'gr.update(**raw["teacher_update_1"])',
        'gr.update(**raw["teacher_update_2"])',
    ]
    missing_wrapper = [m for m in wrapper_markers if m not in wrapper_body]
    add(
        results,
        "3J-d2: wrapper يغلف 8 مخرجات مع HTML خام",
        "PASS" if not wrapper_has_locked and not missing_wrapper else "FAIL",
        "wrapper بلا @state_locked ويغلف 7 gr.update مع إبقاء message خامًا." if not wrapper_has_locked and not missing_wrapper else f"wrapper_locked={wrapper_has_locked}, missing={missing_wrapper}",
    )

    required_core_markers = [
        "get_permissions_from_flags(",
        'permissions["can_edit_vault_basic"]',
        'permissions["can_edit_sensitive_teacher_data"]',
        "teachers_db[name]",
        'teachers_db[name]["cover_count"]',
        'teachers_db[name]["absent_count"]',
        'teachers_db[name]["shortcoming_count"]',
        "write_audit_log(",
        "save_db()",
        "get_updated_balance(",
        "get_updated_absences(",
        "get_updated_shortcomings(",
        "get_day_overview(",
        "get_teacher_choices(",
        "get_absentee_choices(",
        "phone_clean = re.sub",
    ]
    missing_core = [m for m in required_core_markers if m not in core_body]
    add(
        results,
        "3J-d2: منطق الخزنة والصلاحيات محفوظ داخل core",
        "PASS" if not missing_core else "FAIL",
        "core يحتوي منطق الصلاحيات وتعديل الحقول الستة والحفظ/audit وتحديث الجداول." if not missing_core else f"ناقص: {missing_core}",
    )

    forbidden_state = re.search(r"\b(daily_db|processed_absences|last_assigned_teachers)\b", core_body) is not None
    add(
        results,
        "3J-d2: manual count لا يلمس حالة التوزيع اليومية",
        "PASS" if not forbidden_state else "FAIL",
        "update_manual_count_core لا يلمس daily_db/processed_absences/last_assigned_teachers." if not forbidden_state else "وجدت حالة يومية داخل core.",
    )

    still_outside = all(marker not in distribution_text for marker in [
        "def draw_schedule_image",
        "def run_main_generation",
        "def run_full_regeneration",
    ])
    add(
        results,
        "3J-d2: الدوال الثقيلة غير المستهدفة بقيت خارج distribution.py",
        "PASS" if still_outside else "FAIL",
        "draw_schedule_image/run_generation لم تُنقل في هذه المرحلة." if still_outside else "وجدت دوال ثقيلة غير مستهدفة داخل distribution.py.",
    )


def check_reset_monthly_balances_phase3jd3(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-d3: تقسيم reset_monthly_balances إلى core/wrapper بحمولة خام من 5 عناصر."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-d3: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return
    distribution_text = read_text(distribution_path)

    core_exists = re.search(r"^def\s+reset_monthly_balances_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+reset_monthly_balances\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+reset_monthly_balances_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_distribution = re.search(r"^def\s+reset_monthly_balances\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(
        results,
        "3J-d3: core/wrapper في المواضع الصحيحة",
        "PASS" if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else "FAIL",
        "reset_monthly_balances_core في distribution.py وreset_monthly_balances wrapper في app.py دون تكرار." if core_exists and wrapper_exists and not core_in_app and not wrapper_in_distribution else f"core_exists={core_exists}, wrapper_exists={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_distribution={wrapper_in_distribution}",
    )

    core_body = function_body(distribution_text, "reset_monthly_balances_core")
    wrapper_body = function_body(app_text, "reset_monthly_balances")

    forbidden_patterns = [
        (r"gr\.update", "gr.update"),
        (r"import\s+gradio", "import gradio"),
        (r"gr\.SelectData", "gr.SelectData"),
        (r"import\s+app", "import app"),
        (r"from\s+app\s+import", "from app import"),
    ]
    forbidden = []
    for pattern, label in forbidden_patterns:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label}: {lines[:10]}")
    add(
        results,
        "3J-d3: distribution.py بلا Gradio ولا app.py",
        "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden),
    )

    try:
        dist_tree = ast.parse(distribution_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "reset_monthly_balances_core"), None)
        has_locked = bool(core_node and any(getattr(dec, "id", "") == "state_locked" or getattr(getattr(dec, "func", None), "id", "") == "state_locked" for dec in core_node.decorator_list))
        returns = [n for n in ast.walk(core_node) if isinstance(n, ast.Return)] if core_node else []
        returns_raw = bool(returns) and all(isinstance(r.value, ast.Dict) or (isinstance(r.value, ast.Call) and getattr(r.value.func, "id", "") == "build_payload") for r in returns)
    except Exception as exc:
        add(results, "3J-d3: تحليل AST للـcore", "FAIL", f"تعذر التحليل: {exc}")
        has_locked = False
        returns_raw = False
    add(
        results,
        "3J-d3: core مقفلة وترجع حمولة خام",
        "PASS" if has_locked and returns_raw else "FAIL",
        "reset_monthly_balances_core عليها @state_locked وترجع payload خامًا." if has_locked and returns_raw else f"has_locked={has_locked}, returns_raw={returns_raw}",
    )

    wrapper_has_locked = "@state_locked\ndef reset_monthly_balances" in app_text or "@state_locked\r\ndef reset_monthly_balances" in app_text
    wrapper_markers = [
        "reset_monthly_balances_core(",
        'gr.update(value=raw["balance"])',
        'gr.update(value=raw["absences"])',
        'gr.update(value=raw["shortcomings"])',
        'gr.update(value=raw["day_overview"])',
        'raw["message"]',
    ]
    missing_wrapper = [m for m in wrapper_markers if m not in wrapper_body]
    add(
        results,
        "3J-d3: wrapper يغلف 5 مخرجات مع HTML خام",
        "PASS" if not wrapper_has_locked and not missing_wrapper else "FAIL",
        "wrapper بلا @state_locked ويغلف 4 gr.update مع إبقاء message خامًا." if not wrapper_has_locked and not missing_wrapper else f"wrapper_locked={wrapper_has_locked}, missing={missing_wrapper}",
    )

    required_core_markers = [
        "get_permissions(",
        'permissions["can_close_month"]',
        'teachers_db[t]["cover_count"] = 0',
        'teachers_db[t]["absent_count"] = 0',
        'teachers_db[t]["absence_dates"] = []',
        'teachers_db[t]["shortcoming_count"] = 0',
        "daily_db.clear()",
        "processed_absences.clear()",
        "last_assigned_teachers.clear()",
        "save_db()",
        "save_daily_db()",
        "write_audit_log(",
        "get_updated_balance(",
        "get_updated_absences(",
        "get_updated_shortcomings(",
        "get_day_overview(",
    ]
    missing_core = [m for m in required_core_markers if m not in core_body]
    add(
        results,
        "3J-d3: منطق إقفال الشهر محفوظ داخل core",
        "PASS" if not missing_core else "FAIL",
        "core يحتوي الصلاحيات والتصفير والحفظ/audit وتحديث الجداول." if not missing_core else f"ناقص: {missing_core}",
    )

    reassignment = re.search(r"last_assigned_teachers\s*=", core_body) is not None
    add(
        results,
        "3J-d3: last_assigned_teachers لا يعاد تعيينه",
        "PASS" if not reassignment and "last_assigned_teachers.clear()" in core_body else "FAIL",
        "يتم التصفير عبر clear() فقط." if not reassignment and "last_assigned_teachers.clear()" in core_body else "وجدت إعادة تعيين أو غاب clear().",
    )

    still_outside = all(marker not in distribution_text for marker in [
        "def draw_schedule_image",
        "def run_main_generation",
        "def run_full_regeneration",
    ])
    add(
        results,
        "3J-d3: الدوال الثقيلة الأخرى بقيت خارج distribution.py",
        "PASS" if still_outside else "FAIL",
        "draw_schedule_image/run_generation لم تُنقل في هذه المرحلة." if still_outside else "وجدت دوال ثقيلة غير مستهدفة داخل distribution.py.",
    )

def parse_expected_symbols(raw: str | None) -> dict[str, int]:
    if not raw:
        return dict(EXPECTED_SYMBOL_COUNTS)
    expected = dict(EXPECTED_SYMBOL_COUNTS)
    # صيغة: ❌=52,🤝=9,🦅=5,⚠️=22
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"صيغة غير صحيحة للرموز: {part}")
        symbol, value = part.split("=", 1)
        expected[symbol.strip()] = int(value.strip())
    return expected



def check_staff_management_phase3jd4(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-d4: تقسيم add_manual_staff و delete_single_teacher إلى core/wrapper."""
    distribution_path = path.with_name("distribution.py")
    if not distribution_path.exists():
        add(results, "3J-d4: وجود distribution.py", "FAIL", f"غير موجود: {distribution_path}")
        return
    distribution_text = read_text(distribution_path)

    add_core_exists = re.search(r"^def\s+add_manual_staff_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add_wrapper_exists = re.search(r"^def\s+add_manual_staff\s*\(", app_text, flags=re.MULTILINE) is not None
    add_core_in_app = re.search(r"^def\s+add_manual_staff_core\s*\(", app_text, flags=re.MULTILINE) is not None
    add_wrapper_in_distribution = re.search(r"^def\s+add_manual_staff\s*\(", distribution_text, flags=re.MULTILINE) is not None
    del_core_exists = re.search(r"^def\s+delete_single_teacher_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    del_wrapper_exists = re.search(r"^def\s+delete_single_teacher\s*\(", app_text, flags=re.MULTILINE) is not None
    del_core_in_app = re.search(r"^def\s+delete_single_teacher_core\s*\(", app_text, flags=re.MULTILINE) is not None
    del_wrapper_in_distribution = re.search(r"^def\s+delete_single_teacher\s*\(", distribution_text, flags=re.MULTILINE) is not None
    placement_ok = all([
        add_core_exists, add_wrapper_exists, not add_core_in_app, not add_wrapper_in_distribution,
        del_core_exists, del_wrapper_exists, not del_core_in_app, not del_wrapper_in_distribution,
    ])
    add(results, "3J-d4: core/wrapper في المواضع الصحيحة", "PASS" if placement_ok else "FAIL",
        "add/delete cores في distribution.py وwrappers في app.py دون تكرار." if placement_ok else f"add_core={add_core_exists}, add_wrapper={add_wrapper_exists}, del_core={del_core_exists}, del_wrapper={del_wrapper_exists}")

    forbidden = []
    for pattern, label in [(r"gr\.update", "gr.update"), (r"import\s+gradio", "import gradio"), (r"gr\.SelectData", "gr.SelectData"), (r"import\s+app", "import app")]:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label} في الأسطر {lines[:5]}")
    add(results, "3J-d4: distribution.py بلا Gradio ولا app.py", "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا app.py." if not forbidden else "; ".join(forbidden))

    try:
        dist_tree = ast.parse(distribution_text)
        add_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "add_manual_staff_core"), None)
        del_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "delete_single_teacher_core"), None)
        def has_state_locked(node):
            return bool(node and any((isinstance(d, ast.Name) and d.id == "state_locked") or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "state_locked") for d in node.decorator_list))
        def returns_dict(node):
            return bool(node and any(isinstance(n, ast.Return) and isinstance(n.value, ast.Dict) for n in ast.walk(node)))
        locked_payload_ok = has_state_locked(add_node) and has_state_locked(del_node) and returns_dict(add_node) and returns_dict(del_node)
    except Exception as exc:
        locked_payload_ok = False
        add(results, "3J-d4: تحليل AST للـcores", "FAIL", f"تعذر التحليل: {exc}")
    add(results, "3J-d4: cores مقفلة وترجع payload خام", "PASS" if locked_payload_ok else "FAIL",
        "add/delete cores عليها @state_locked وترجع payload خامًا." if locked_payload_ok else "فشل تحقق @state_locked أو payload.")

    wrappers_body = (function_body(app_text, "add_manual_staff") or "") + "\n" + (function_body(app_text, "delete_single_teacher") or "")
    try:
        app_tree = ast.parse(app_text)
        app_add_node = next((n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "add_manual_staff"), None)
        app_del_node = next((n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "delete_single_teacher"), None)
        wrapper_locked = has_state_locked(app_add_node) or has_state_locked(app_del_node)
    except Exception:
        wrapper_locked = True
    wrapper_calls_core = "add_manual_staff_core(" in wrappers_body and "delete_single_teacher_core(" in wrappers_body
    wrapper_has_raw_html = 'raw["message"]' in wrappers_body
    add(results, "3J-d4: wrappers خفيفة وتحافظ على HTML الخام", "PASS" if wrapper_calls_core and wrapper_has_raw_html and not wrapper_locked else "FAIL",
        "wrappers تستدعي cores وتعيد message خامًا بلا @state_locked." if wrapper_calls_core and wrapper_has_raw_html and not wrapper_locked else f"calls_core={wrapper_calls_core}, raw_html={wrapper_has_raw_html}, wrapper_locked={wrapper_locked}")

    add_core_body = function_body(distribution_text, "add_manual_staff_core") or ""
    del_core_body = function_body(distribution_text, "delete_single_teacher_core") or ""
    no_audit = "write_audit_log" not in add_core_body and "write_audit_log" not in del_core_body and "_queue_audit_change" not in add_core_body and "_queue_audit_change" not in del_core_body
    add(results, "3J-d4: عدم إضافة audit غير موجود أصلاً", "PASS" if no_audit else "FAIL",
        "لم تُضف سجلات audit للإضافة/الحذف، حفاظًا على السلوك الأصلي." if no_audit else "وجد audit داخل add/delete cores.")

    no_global_teachers = "global teachers_db" not in add_core_body and "global teachers_db" not in del_core_body
    delete_element_only = "del teachers_db[name]" in del_core_body and "del teachers_db" not in del_core_body.replace("del teachers_db[name]", "")
    add(results, "3J-d4: تعديل teachers_db في المكان", "PASS" if no_global_teachers and delete_element_only else "FAIL",
        "لا global teachers_db، والحذف عنصر من القاموس فقط." if no_global_teachers and delete_element_only else f"no_global={no_global_teachers}, delete_element_only={delete_element_only}")

    still_outside = all(marker not in distribution_text for marker in [
        "def draw_schedule_image(", "def run_main_generation(", "def run_full_regeneration(",
    ])
    add(results, "3J-d4: الدوال الثقيلة الأخرى بقيت خارج distribution.py", "PASS" if still_outside else "FAIL",
        "draw_schedule_image/run_generation لم تُنقل في هذه المرحلة." if still_outside else "وجدت دوال ثقيلة غير مستهدفة داخل distribution.py.")



def check_draw_schedule_image_phase3je1(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-e1: نقل draw_schedule_image كـcore إلى swaps.py مع إبقاء wrapper في app.py."""
    swaps_path = path.with_name("swaps.py")
    distribution_path = path.with_name("distribution.py")
    if not swaps_path.exists():
        add(results, "3J-e1: وجود swaps.py", "FAIL", f"غير موجود: {swaps_path}")
        return
    swaps_text = read_text(swaps_path)
    distribution_text = read_text(distribution_path) if distribution_path.exists() else ""

    core_exists = re.search(r"^def\s+draw_schedule_image_core\s*\(", swaps_text, flags=re.MULTILINE) is not None
    wrapper_exists = re.search(r"^def\s+draw_schedule_image\s*\(", app_text, flags=re.MULTILINE) is not None
    core_in_app = re.search(r"^def\s+draw_schedule_image_core\s*\(", app_text, flags=re.MULTILINE) is not None
    wrapper_in_swaps = re.search(r"^def\s+draw_schedule_image\s*\(", swaps_text, flags=re.MULTILINE) is not None
    core_in_distribution = "def draw_schedule_image_core" in distribution_text
    placement_ok = core_exists and wrapper_exists and not core_in_app and not wrapper_in_swaps and not core_in_distribution
    add(results, "3J-e1: draw_schedule_image core/wrapper في المواضع الصحيحة", "PASS" if placement_ok else "FAIL",
        "core في swaps.py وwrapper في app.py دون تكرار أو نقل إلى distribution.py." if placement_ok else f"core={core_exists}, wrapper={wrapper_exists}, core_in_app={core_in_app}, wrapper_in_swaps={wrapper_in_swaps}, core_in_distribution={core_in_distribution}")

    forbidden = []
    for pattern, label in [(r"gr\.update", "gr.update"), (r"import\s+gradio", "import gradio"), (r"gr\.SelectData", "gr.SelectData"), (r"import\s+app", "import app")]:
        lines = line_numbers_for_pattern(swaps_text, pattern)
        if lines:
            forbidden.append(f"{label} في الأسطر {lines[:5]}")
    add(results, "3J-e1: swaps.py بلا Gradio ولا app.py", "PASS" if not forbidden else "FAIL",
        "لا يحتوي swaps.py على Gradio ولا import app." if not forbidden else "; ".join(forbidden))

    try:
        app_tree = ast.parse(app_text)
        swaps_tree = ast.parse(swaps_text)
        app_node = next((n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "draw_schedule_image"), None)
        core_node = next((n for n in ast.walk(swaps_tree) if isinstance(n, ast.FunctionDef) and n.name == "draw_schedule_image_core"), None)
        def has_state_locked(node):
            return bool(node and any((isinstance(d, ast.Name) and d.id == "state_locked") or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "state_locked") for d in node.decorator_list))
        def direct_returns(node):
            if not node:
                return []
            values = []
            for child in node.body:
                if isinstance(child, ast.Return):
                    values.append(ast.unparse(child.value) if child.value is not None else "None")
            return values
        wrapper_locked = has_state_locked(app_node)
        core_locked = has_state_locked(core_node)
        core_direct_returns = direct_returns(core_node)
    except Exception as exc:
        wrapper_locked = True
        core_locked = True
        core_direct_returns = []
        add(results, "3J-e1: تحليل AST للدالة", "FAIL", f"تعذر التحليل: {exc}")

    wrapper_body = function_body(app_text, "draw_schedule_image") or ""
    wrapper_ok = (not wrapper_locked) and "draw_schedule_image_core(" in wrapper_body and "gr.update" not in wrapper_body
    add(results, "3J-e1: wrapper خفيف بلا قفل ولا Gradio", "PASS" if wrapper_ok else "FAIL",
        "wrapper يستدعي core مباشرة ويرجع filename كما في العقد القديم." if wrapper_ok else f"wrapper_locked={wrapper_locked}, calls_core={'draw_schedule_image_core(' in wrapper_body}, has_gr={'gr.update' in wrapper_body}")

    core_body = function_body(swaps_text, "draw_schedule_image_core") or ""
    core_contract_ok = (not core_locked) and core_direct_returns == ["filename"]
    add(results, "3J-e1: core بلا قفل ويرجع filename فقط", "PASS" if core_contract_ok else "FAIL",
        "core غير مقفلة وترجع سلسلة filename خامًا فقط." if core_contract_ok else f"core_locked={core_locked}, direct_returns={core_direct_returns}")

    no_state_mutation = all(marker not in core_body for marker in [
        "teachers_db", "daily_db", "processed_absences", "last_assigned_teachers", "save_db(", "save_daily_db(", "save_swap_db(", "write_audit_log(",
    ])
    add(results, "3J-e1: core لا تعدل حالة عامة", "PASS" if no_state_mutation else "FAIL",
        "draw_schedule_image_core تقرأ df فقط وتولّد ملف صورة." if no_state_mutation else "وجدت مؤشرات تعديل حالة داخل core.")

    deps_ok = all(marker in core_body for marker in ["ensure_data_directories()", "IMG_DIR", "get_date_of_weekday(day_name)", "Image.new", "ImageDraw.Draw", "ImageFont.truetype"])
    add(results, "3J-e1: اعتماديات الصورة والمسار محفوظة", "PASS" if deps_ok else "FAIL",
        "core تستخدم IMG_DIR وensure_data_directories وPIL وget_date_of_weekday." if deps_ok else "بعض اعتماديات الصورة أو المسار غير ظاهرة داخل core.")

    app_no_duplicate_fonts = "candidate_font_paths = [" not in app_text and "image_font_candidate_paths = [" not in app_text
    app_imports_font_paths = "font_path" in app_text and "image_font_path" in app_text and "from swaps import" in app_text
    add(results, "3J-e1: مصدر الخطوط موحد من swaps.py", "PASS" if app_no_duplicate_fonts and app_imports_font_paths else "FAIL",
        "حُذف منطق البحث المكرر عن الخطوط من app.py ويستورد المسارات من swaps.py." if app_no_duplicate_fonts and app_imports_font_paths else f"no_duplicate={app_no_duplicate_fonts}, imports_paths={app_imports_font_paths}")


def check_generation_orchestration_phase3je2fix(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-e2-fix: تشغيل التوليد يستدعي assign_logic_core مباشرة بلا refresh مزدوج."""
    distribution_path = path.with_name("distribution.py")
    distribution_text = read_text(distribution_path) if distribution_path.exists() else ""

    imports_core = "assign_logic_core" in app_text and "from distribution import" in app_text
    core_exists = re.search(r"^def\s+assign_logic_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    add(results, "3J-e2-fix: assign_logic_core متاحة للتوليد", "PASS" if imports_core and core_exists else "FAIL",
        "assign_logic_core مستوردة في app.py وموجودة في distribution.py." if imports_core and core_exists else f"imports_core={imports_core}, core_exists={core_exists}")

    def check_generation_func(func_name: str) -> None:
        body = function_body(app_text, func_name) or ""
        exists = bool(body)
        calls_core = "assign_logic_core(" in body
        calls_wrapper = re.search(r"(?<!_)\bassign_logic\s*\(", body) is not None
        refresh_count = body.count("refresh_ui_on_change(")
        locked = False
        try:
            tree = ast.parse(app_text)
            node = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
            locked = bool(node and any(
                (isinstance(d, ast.Name) and d.id == "state_locked")
                or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "state_locked")
                for d in node.decorator_list
            ))
        except Exception:
            locked = True

        ok = exists and calls_core and not calls_wrapper and refresh_count == 2 and not locked
        add(results, f"3J-e2-fix: {func_name} يستدعي core بلا wrapper", "PASS" if ok else "FAIL",
            f"{func_name} يستدعي assign_logic_core مباشرة ويحافظ على refresh_ui_on_change للفرعين فقط." if ok else f"exists={exists}, calls_core={calls_core}, calls_wrapper={calls_wrapper}, refresh_count={refresh_count}, locked={locked}")

    check_generation_func("run_main_generation")
    check_generation_func("run_full_regeneration")

    ui_helpers_expected = [
        "def generate_image_only(",
        "def clear_generated_image(",
        "def force_refresh_data(",
        "def toggle_cross_dept(",
        "def get_leader_action_button_updates(",
    ]
    helpers_still_in_app = all(marker in app_text for marker in ui_helpers_expected)
    helpers_not_in_distribution = not any(marker in distribution_text for marker in ui_helpers_expected)
    add(results, "3J-e2-fix: دوال UI الصغيرة باقية في app.py", "PASS" if helpers_still_in_app and helpers_not_in_distribution else "FAIL",
        "دوال generate/clear/force/toggle/buttons بقيت في app.py لأنها UI-bound." if helpers_still_in_app and helpers_not_in_distribution else f"helpers_in_app={helpers_still_in_app}, helpers_not_in_distribution={helpers_not_in_distribution}")


def check_rollback_auto_assignments_phase3je3(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3J-e3: rollback_auto_assignments_for_absentees أصبحت core فقط داخل distribution.py."""
    distribution_path = path.with_name("distribution.py")
    distribution_text = read_text(distribution_path) if distribution_path.exists() else ""

    core_exists = re.search(r"^def\s+rollback_auto_assignments_for_absentees_core\s*\(", distribution_text, flags=re.MULTILINE) is not None
    old_wrapper_exists = re.search(r"^def\s+rollback_auto_assignments_for_absentees\s*\(", app_text, flags=re.MULTILINE) is not None
    app_imports_core = "rollback_auto_assignments_for_absentees_core" in app_text and "from distribution import" in app_text
    add(results, "3J-e3: rollback core فقط في distribution.py", "PASS" if core_exists and not old_wrapper_exists and app_imports_core else "FAIL",
        "core موجودة في distribution.py والاسم القديم غير معرف في app.py مع استيراد core." if core_exists and not old_wrapper_exists and app_imports_core else f"core_exists={core_exists}, old_wrapper_exists={old_wrapper_exists}, app_imports_core={app_imports_core}")

    forbidden = []
    for pattern, label in [(r"gr\.update", "gr.update"), (r"import\s+gradio", "import gradio"), (r"gr\.SelectData", "gr.SelectData"), (r"import\s+app", "import app")]:
        lines = line_numbers_for_pattern(distribution_text, pattern)
        if lines:
            forbidden.append(f"{label} في الأسطر {lines[:5]}")
    add(results, "3J-e3: distribution.py بلا Gradio ولا app.py", "PASS" if not forbidden else "FAIL",
        "لا يحتوي distribution.py على Gradio ولا import app." if not forbidden else "; ".join(forbidden))

    try:
        dist_tree = ast.parse(distribution_text)
        app_tree = ast.parse(app_text)
        core_node = next((n for n in ast.walk(dist_tree) if isinstance(n, ast.FunctionDef) and n.name == "rollback_auto_assignments_for_absentees_core"), None)
        run_full_node = next((n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "run_full_regeneration"), None)
        def has_state_locked(node):
            return bool(node and any(
                (isinstance(d, ast.Name) and d.id == "state_locked")
                or (isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "state_locked")
                for d in node.decorator_list
            ))
        core_locked = has_state_locked(core_node)
        core_returns_none = bool(core_node) and all(isinstance(n, ast.Return) and n.value is None for n in ast.walk(core_node) if isinstance(n, ast.Return))
        run_full_locked = has_state_locked(run_full_node)
    except Exception as exc:
        core_locked = False
        core_returns_none = False
        run_full_locked = True
        add(results, "3J-e3: تحليل AST", "FAIL", f"تعذر التحليل: {exc}")

    add(results, "3J-e3: core مقفلة وترجع None فقط", "PASS" if core_locked and core_returns_none else "FAIL",
        "rollback core عليها @state_locked وتبقى دالة أثر جانبي بلا قيمة إرجاع." if core_locked and core_returns_none else f"core_locked={core_locked}, core_returns_none={core_returns_none}")

    core_body = function_body(distribution_text, "rollback_auto_assignments_for_absentees_core") or ""
    state_ok = all(marker in core_body for marker in ["daily_db.clear()", "daily_db.extend(kept_rows)", "teachers_db[old_sub][\"cover_count\"]", "save_db()", "save_daily_db()"])
    no_forbidden_state = all(marker not in core_body for marker in ["processed_absences", "last_assigned_teachers", "teachers_db =", "daily_db ="])
    add(results, "3J-e3: تعديل الحالة محفوظ in-place", "PASS" if state_ok and no_forbidden_state else "FAIL",
        "core تعدل teachers_db/daily_db في المكان ولا تلمس processed_absences أو last_assigned_teachers." if state_ok and no_forbidden_state else f"state_ok={state_ok}, no_forbidden_state={no_forbidden_state}")

    audit_ok = all(marker in core_body for marker in ["_queue_audit_change", "_flush_audit_changes", "actor_name", "actor_role"])
    add(results, "3J-e3: audit محفوظ داخل core", "PASS" if audit_ok else "FAIL",
        "core يحافظ على audit queue/flush كما في الأصل." if audit_ok else "لم تظهر مؤشرات audit المطلوبة داخل core.")

    run_full_body = function_body(app_text, "run_full_regeneration") or ""
    calls_core = "rollback_auto_assignments_for_absentees_core(" in run_full_body
    calls_old = re.search(r"(?<!_rollback_auto_assignments_for_absentees_)\brollback_auto_assignments_for_absentees\s*\(", run_full_body) is not None
    idx_rb = run_full_body.find("rollback_auto_assignments_for_absentees_core(")
    idx_assign = run_full_body.find("assign_logic_core(", idx_rb + 1) if idx_rb >= 0 else -1
    idx_refresh = run_full_body.find("refresh_ui_on_change(", idx_assign + 1) if idx_assign >= 0 else -1
    order_ok = idx_rb >= 0 and idx_assign > idx_rb and idx_refresh > idx_assign
    add(results, "3J-e3: run_full_regeneration يستدعي rollback core بالترتيب الصحيح", "PASS" if calls_core and not calls_old and order_ok and not run_full_locked else "FAIL",
        "الترتيب محفوظ: rollback_core ثم assign_logic_core ثم refresh_ui_on_change، بلا قفل على run_full." if calls_core and not calls_old and order_ok and not run_full_locked else f"calls_core={calls_core}, calls_old={calls_old}, order_ok={order_ok}, run_full_locked={run_full_locked}")

    other_calls = len(re.findall(r"rollback_auto_assignments_for_absentees_core\s*\(", app_text))
    add(results, "3J-e3: نقطة استدعاء rollback محدودة", "PASS" if other_calls == 1 else "FAIL",
        "الاسم يظهر مرة واحدة داخل run_full_regeneration، والاستيراد لا يُحسب كاستدعاء." if other_calls == 1 else f"عدد استدعاءات rollback core في app.py: {other_calls}")

def check_potential_dead_code_admin_excel_phase3j_final(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """وثّق إزالة دوال Excel القديمة ككود ميت مؤكد بعد 3K-dead-code-cleanup."""
    try:
        tree = ast.parse(app_text)
    except SyntaxError as exc:
        add(results, "3K-dead-code-cleanup: فحص دوال Excel القديمة", "INFO", f"تعذر تحليل app.py: {exc}")
        return

    removed_names = {"process_admin_excel", "process_phone_excel"}
    defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    call_counts: dict[str, int] = {name: 0 for name in removed_names}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in call_counts:
            call_counts[node.func.id] += 1

    still_defined = sorted(name for name in removed_names if name in defined)
    still_called = sorted(name for name, count in call_counts.items() if count > 0)
    if still_defined or still_called:
        add(
            results,
            "3K-dead-code-cleanup: إزالة process_admin_excel/process_phone_excel",
            "FAIL",
            f"لا تزال موجودة أو مستدعاة: defined={still_defined}, called={still_called}",
        )
        return

    add(
        results,
        "3K-dead-code-cleanup: إزالة process_admin_excel/process_phone_excel",
        "INFO",
        "تمت إزالة الدالتين ككود ميت مؤكد بعد فحص 3K-dead-code-cleanup-pre؛ غيابهما هو السلوك الصحيح ولا يُعد FAIL.",
    )


def check_data_center_reference_refresh_phase3k(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص Phase 3K: فصل تحديث الإداريين والأرقام المرجعية إلى core/wrapper."""
    school_data_path = path.with_name("school_data.py")
    if not school_data_path.exists():
        add(results, "3K-data-center-core: وجود school_data.py", "FAIL", f"غير موجود: {school_data_path}")
        return

    school_text = read_text(school_data_path)
    admins_core = "refresh_admins_from_reference_core"
    admins_wrapper = "refresh_admins_from_reference"
    phones_core = "refresh_phones_from_reference_core"
    phones_wrapper = "refresh_phones_from_reference"

    placement_ok = all([
        re.search(rf"^def\s+{admins_core}\s*\(", school_text, flags=re.MULTILINE) is not None,
        re.search(rf"^def\s+{phones_core}\s*\(", school_text, flags=re.MULTILINE) is not None,
        re.search(rf"^def\s+{admins_wrapper}\s*\(", app_text, flags=re.MULTILINE) is not None,
        re.search(rf"^def\s+{phones_wrapper}\s*\(", app_text, flags=re.MULTILINE) is not None,
        re.search(rf"^def\s+{admins_core}\s*\(", app_text, flags=re.MULTILINE) is None,
        re.search(rf"^def\s+{phones_core}\s*\(", app_text, flags=re.MULTILINE) is None,
    ])
    add(results, "3K-data-center-core: core/wrapper في المواضع الصحيحة", "PASS" if placement_ok else "FAIL",
        "cores في school_data.py والـwrappers في app.py دون تكرار." if placement_ok else "فشل موضع core/wrapper لتحديث الإداريين أو الأرقام.")

    try:
        app_tree = ast.parse(app_text)
        school_tree = ast.parse(school_text)
        app_funcs = {n.name: n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef)}
        school_funcs = {n.name: n for n in ast.walk(school_tree) if isinstance(n, ast.FunctionDef)}

        def state_locked_count(node: ast.FunctionDef | None) -> int:
            if not node:
                return -1
            count = 0
            for dec in node.decorator_list:
                if isinstance(dec, ast.Name) and dec.id == "state_locked":
                    count += 1
                elif isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "state_locked":
                    count += 1
            return count

        def tuple_return_lengths(node: ast.FunctionDef | None) -> list[int]:
            if not node:
                return []
            lengths: list[int] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple):
                    lengths.append(len(child.value.elts))
            return lengths

        admin_core_node = school_funcs.get(admins_core)
        phone_core_node = school_funcs.get(phones_core)
        admin_wrapper_node = app_funcs.get(admins_wrapper)
        phone_wrapper_node = app_funcs.get(phones_wrapper)

        locks_ok = (
            state_locked_count(admin_core_node) == 1
            and state_locked_count(phone_core_node) == 1
            and state_locked_count(admin_wrapper_node) == 0
            and state_locked_count(phone_wrapper_node) == 0
        )
        admin_core_lengths = tuple_return_lengths(admin_core_node)
        phone_core_lengths = tuple_return_lengths(phone_core_node)
        admin_wrapper_lengths = tuple_return_lengths(admin_wrapper_node)
        phone_wrapper_lengths = tuple_return_lengths(phone_wrapper_node)
        contracts_ok = (
            admin_core_lengths and all(length == 8 for length in admin_core_lengths)
            and phone_core_lengths and all(length == 4 for length in phone_core_lengths)
            and admin_wrapper_lengths == [8]
            and phone_wrapper_lengths == [4]
        )
    except Exception as exc:
        locks_ok = False
        contracts_ok = False
        admin_core_lengths = phone_core_lengths = admin_wrapper_lengths = phone_wrapper_lengths = []
        add(results, "3K-data-center-core: تحليل AST", "FAIL", f"تعذر التحليل: {exc}")

    add(results, "3K-data-center-core: القفل على core فقط", "PASS" if locks_ok else "FAIL",
        "@state_locked مرة واحدة على كل core وصفر على wrappers." if locks_ok else "فشل تحقق القفل: يجب أن يكون على core فقط وبلا تكرار.")
    add(results, "3K-data-center-core: عقود الإرجاع 8 و4 محفوظة", "PASS" if contracts_ok else "FAIL",
        "admins core/wrapper = 8 عناصر، phones core/wrapper = 4 عناصر." if contracts_ok else f"admins_core={admin_core_lengths}, admins_wrapper={admin_wrapper_lengths}, phones_core={phone_core_lengths}, phones_wrapper={phone_wrapper_lengths}")

    admin_core_body = function_body(school_text, admins_core) or ""
    phone_core_body = function_body(school_text, phones_core) or ""
    cores_forbidden = any(marker in (admin_core_body + phone_core_body) for marker in ["gr.update", "gr.Warning", "gr.Info", "import gradio", "gr.SelectData"])
    school_no_app = re.search(r"(^|\n)\s*(from\s+app\s+import|import\s+app)\b", school_text) is None
    add(results, "3K-data-center-core: cores خامة بلا Gradio ولا app.py", "PASS" if not cores_forbidden and school_no_app else "FAIL",
        "cores لا تحتوي gr.update/Warning/Info ولا يوجد اعتماد عكسي على app.py." if not cores_forbidden and school_no_app else f"cores_forbidden={cores_forbidden}, school_no_app={school_no_app}")

    admin_wrapper_body = function_body(app_text, admins_wrapper) or ""
    phone_wrapper_body = function_body(app_text, phones_wrapper) or ""
    wrappers_ok = (
        f"{admins_core}(" in admin_wrapper_body
        and f"{phones_core}(" in phone_wrapper_body
        and "gr.update" in admin_wrapper_body
        and "gr.update" in phone_wrapper_body
        and "state_locked" not in admin_wrapper_body.split("def", 1)[0]
        and "state_locked" not in phone_wrapper_body.split("def", 1)[0]
    )
    add(results, "3K-data-center-core: wrappers تستدعي cores وتغلف Gradio", "PASS" if wrappers_ok else "FAIL",
        "wrappers تستدعي cores وتحافظ على تغليف gr.update بالاسم القديم." if wrappers_ok else "wrapper لا يستدعي core أو لا يغلف مخرجات Gradio كما هو متوقع.")

    binding_admin_ok = re.search(r"refresh_admin_reference_btn\.click\s*\(\s*refresh_admins_from_reference\s*,", app_text, flags=re.DOTALL) is not None
    binding_phones_ok = re.search(r"refresh_phones_reference_btn\.click\s*\(\s*refresh_phones_from_reference\s*,", app_text, flags=re.DOTALL) is not None
    add(results, "3K-data-center-core: ربط أزرار مركز البيانات محفوظ", "PASS" if binding_admin_ok and binding_phones_ok else "FAIL",
        "الربط المباشر بالاسم بقي كما هو بلا lambda أو تغيير أسماء." if binding_admin_ok and binding_phones_ok else f"admin_binding={binding_admin_ok}, phones_binding={binding_phones_ok}")

    red_lines_clean = all(marker not in app_text for marker in ["school_data_tab.select(", "select_tab_js(\"مركز البيانات\"", "select_tab_js('مركز البيانات'"])
    add(results, "3K-data-center-core: القاعدة الحمراء لمركز البيانات محفوظة", "PASS" if red_lines_clean else "FAIL",
        "لم يرجع school_data_tab.select أو select_tab_js لمركز البيانات." if red_lines_clean else "وجد نمط ممنوع في ربط مركز البيانات.")


def check_school_settings_core_phase3k(path, app_text, results):
    """فحص 3K-school-settings-core: فصل save_school_operational_settings إلى core/wrapper."""

    sd_path = path.parent / "school_data.py"
    if not sd_path.exists():
        add(results, "3K-school-settings: school_data.py موجود", "FAIL", "")
        return
    sd_text = sd_path.read_text(encoding="utf-8")

    # 1. وجود core في school_data.py
    core_exists = re.search(r"^def\s+save_school_operational_settings_core\s*\(", sd_text, flags=re.MULTILINE) is not None
    add(results, "3K-school-settings: save_school_operational_settings_core في school_data.py", "PASS" if core_exists else "FAIL", "")
    if not core_exists:
        return

    # 2. @state_locked مرة واحدة على core
    sd_tree = ast.parse(sd_text)
    core_nodes = [n for n in ast.walk(sd_tree) if isinstance(n, ast.FunctionDef) and n.name == "save_school_operational_settings_core"]
    core_node = core_nodes[0]
    core_decs = [d.id for d in core_node.decorator_list if isinstance(d, ast.Name)]
    has_one_lock = core_decs == ["state_locked"]
    add(results, "3K-school-settings: @state_locked مرة واحدة على core", "PASS" if has_one_lock else "FAIL", f"ديكوريتورات: {core_decs}")

    # 3. core بلا gr.update فعلي (في الكود لا التعليقات/docstring)
    core_body_lines = sd_text.splitlines()[core_node.lineno - 1 : core_node.end_lineno]
    # فحص gr.update عبر AST لتجنب التعليقات وdocstrings
    gr_count = 0
    for ast_node in ast.walk(core_node):
        if isinstance(ast_node, ast.Attribute) and ast_node.attr == "update":
            if isinstance(ast_node.value, ast.Name) and ast_node.value.id == "gr":
                gr_count += 1
    add(results, "3K-school-settings: core بلا gr.update فعلي", "PASS" if gr_count == 0 else "FAIL", f"count={gr_count}")

    # 4. لا إعادة تعيين خطرة لـSCHOOL_CONFIG
    core_body = "\n".join(core_body_lines)
    dangerous = "SCHOOL_CONFIG = dict(" in core_body
    add(results, "3K-school-settings: core بلا SCHOOL_CONFIG = dict(…) الخطرة", "PASS" if not dangerous else "FAIL", "لا يوجد" if not dangerous else "موجود!")

    # 5. school_data.py لا يستورد app.py
    no_import_app = "import app" not in sd_text and "from app" not in sd_text
    add(results, "3K-school-settings: school_data.py لا يستورد app.py", "PASS" if no_import_app else "FAIL", "")

    # 6. wrapper في app.py بلا @state_locked
    app_tree = ast.parse(app_text)
    wrapper_nodes = [n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "save_school_operational_settings"]
    wrapper_exists = len(wrapper_nodes) > 0
    add(results, "3K-school-settings: save_school_operational_settings wrapper في app.py", "PASS" if wrapper_exists else "FAIL", "")
    if not wrapper_exists:
        return
    wrapper_node = wrapper_nodes[0]
    wrapper_decs = [d.id for d in wrapper_node.decorator_list if isinstance(d, ast.Name)]
    wrapper_no_lock = "state_locked" not in wrapper_decs
    add(results, "3K-school-settings: wrapper بلا @state_locked", "PASS" if wrapper_no_lock else "FAIL", f"ديكوريتورات: {wrapper_decs}")

    # 7. wrapper يرجع 4 عناصر
    wrapper_returns = [len(stmt.value.elts) for stmt in ast.walk(wrapper_node) if isinstance(stmt, ast.Return) and isinstance(stmt.value, ast.Tuple)]
    correct = all(c == 4 for c in wrapper_returns) and len(wrapper_returns) > 0
    add(results, "3K-school-settings: wrapper يرجع 4 عناصر", "PASS" if correct else "FAIL", f"{wrapper_returns}")

    # 8. wrapper يستدعي core
    wrapper_body = "\n".join(app_text.splitlines()[wrapper_node.lineno - 1 : wrapper_node.end_lineno])
    calls_core = "save_school_operational_settings_core(" in wrapper_body
    add(results, "3K-school-settings: wrapper يستدعي save_school_operational_settings_core", "PASS" if calls_core else "FAIL", "")


def check_identity_reference_fix_phase3k(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3K-identity-reference-fix: إصلاح كسر مرجع SCHOOL_CONFIG داخل _apply_school_identity_globals."""

    app_tree = ast.parse(app_text)

    # 1. تأكيد وجود الدوال الأربع في app.py
    identity_funcs = ["save_school_identity_settings", "reset_school_identity_settings",
                      "_identity_full_output", "_apply_school_identity_globals"]
    found = {n.name for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef)}
    for fn in identity_funcs:
        exists = fn in found
        add(results, f"3K-identity-fix: {fn} باقية في app.py", "PASS" if exists else "FAIL", "")

    # 2. تأكيد غياب دوال الهوية من school_data.py
    sd_path = path.parent / "school_data.py"
    if sd_path.exists():
        sd_text = sd_path.read_text(encoding="utf-8")
        for fn in ["save_school_identity_settings", "reset_school_identity_settings", "_identity_full_output"]:
            in_sd = re.search(rf"^def\s+{re.escape(fn)}\s*\(", sd_text, flags=re.MULTILINE) is not None
            add(results, f"3K-identity-fix: {fn} لم تُنقَل إلى school_data.py", "PASS" if not in_sd else "FAIL", "")
        # تأكيد بقاء save_school_operational_settings_core من المرحلة السابقة
        core_still = re.search(r"^def\s+save_school_operational_settings_core\s*\(", sd_text, flags=re.MULTILINE) is not None
        add(results, "3K-identity-fix: save_school_operational_settings_core ما زالت في school_data.py", "PASS" if core_still else "FAIL", "")

    # 3. فحص _apply_school_identity_globals بالـAST بدقة
    globals_nodes = [n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "_apply_school_identity_globals"]
    if not globals_nodes:
        add(results, "3K-identity-fix: _apply_school_identity_globals موجودة", "FAIL", "")
        return

    func_node = globals_nodes[0]
    func_lines = app_text.splitlines()[func_node.lineno - 1 : func_node.end_lineno]
    func_body = "\n".join(func_lines)

    # 3a. لا توجد إعادة تعيين مباشرة لـSCHOOL_CONFIG
    dangerous = False
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCHOOL_CONFIG":
                    dangerous = True
    add(results, "3K-identity-fix: لا إعادة تعيين SCHOOL_CONFIG داخل _apply_school_identity_globals", "PASS" if not dangerous else "FAIL",
        "لا توجد" if not dangerous else "موجودة! خطر كسر المرجع")

    # 3b. لا يوجد SCHOOL_CONFIG = dict(…) نصياً
    dict_assign = "SCHOOL_CONFIG = dict(" in func_body
    add(results, "3K-identity-fix: لا SCHOOL_CONFIG = dict(…) نصياً", "PASS" if not dict_assign else "FAIL", "")

    # 3c. وجود SCHOOL_CONFIG.clear()
    has_clear = "SCHOOL_CONFIG.clear()" in func_body
    add(results, "3K-identity-fix: SCHOOL_CONFIG.clear() موجودة", "PASS" if has_clear else "FAIL", "")

    # 3d. وجود SCHOOL_CONFIG.update(…)
    has_update = re.search(r"SCHOOL_CONFIG\.update\s*\(", func_body) is not None
    add(results, "3K-identity-fix: SCHOOL_CONFIG.update(…) موجودة", "PASS" if has_update else "FAIL", "")

    # 3e. global SCHOOL_CONFIG حُذف (لم يعد ضرورياً بعد التعديل)
    has_global_school = any(
        isinstance(n, ast.Global) and "SCHOOL_CONFIG" in n.names
        for n in ast.walk(func_node)
    )
    add(results, "3K-identity-fix: global SCHOOL_CONFIG أُزيل من _apply_school_identity_globals", "PASS" if not has_global_school else "WARN",
        "غير موجود (صحيح)" if not has_global_school else "لا يزال موجوداً (غير خطر لكن غير ضروري)")


def check_identity_core_phase3k(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """فحص 3K-identity-core: فصل save/reset_school_identity_settings إلى core/wrapper."""

    sd_path = path.parent / "school_data.py"
    if not sd_path.exists():
        add(results, "3K-identity-core: school_data.py موجود", "FAIL", "")
        return
    sd_text = sd_path.read_text(encoding="utf-8")
    sd_tree = ast.parse(sd_text)
    app_tree = ast.parse(app_text)

    # 1. وجود الـcores في school_data.py
    for core_name in ["save_school_identity_settings_core", "reset_school_identity_settings_core"]:
        exists = any(n.name == core_name for n in ast.walk(sd_tree) if isinstance(n, ast.FunctionDef))
        add(results, f"3K-identity-core: {core_name} في school_data.py", "PASS" if exists else "FAIL", "")

    # 2. وجود wrappers في app.py بلا @state_locked
    for wrapper_name in ["save_school_identity_settings", "reset_school_identity_settings"]:
        nodes = [n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == wrapper_name]
        if not nodes:
            add(results, f"3K-identity-core: {wrapper_name} wrapper في app.py", "FAIL", "")
            continue
        node = nodes[0]
        decs = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
        no_lock = "state_locked" not in decs
        add(results, f"3K-identity-core: {wrapper_name} wrapper بلا @state_locked", "PASS" if no_lock else "FAIL", f"decs={decs}")
        # يستدعي core
        body = "\n".join(app_text.splitlines()[node.lineno-1:node.end_lineno])
        calls_core = f"{wrapper_name}_core(" in body
        add(results, f"3K-identity-core: {wrapper_name} يستدعي core", "PASS" if calls_core else "FAIL", "")

    # 3. cores عليها @state_locked مرة واحدة وصفر gr.update
    for core_name in ["save_school_identity_settings_core", "reset_school_identity_settings_core"]:
        nodes = [n for n in ast.walk(sd_tree) if isinstance(n, ast.FunctionDef) and n.name == core_name]
        if not nodes:
            continue
        core_node = nodes[0]
        core_decs = [d.id for d in core_node.decorator_list if isinstance(d, ast.Name)]
        one_lock = core_decs == ["state_locked"]
        add(results, f"3K-identity-core: {core_name} @state_locked مرة واحدة", "PASS" if one_lock else "FAIL", f"decs={core_decs}")
        gr_count = sum(1 for n in ast.walk(core_node) if isinstance(n, ast.Attribute) and n.attr == "update" and isinstance(n.value, ast.Name) and n.value.id == "gr")
        add(results, f"3K-identity-core: {core_name} بلا gr.update", "PASS" if gr_count == 0 else "FAIL", f"count={gr_count}")

    # 4. school_data.py لا يستورد app.py
    no_import_app = "import app" not in sd_text and "from app" not in sd_text
    add(results, "3K-identity-core: school_data.py لا يستورد app.py", "PASS" if no_import_app else "FAIL", "")

    # 5. بقاء _identity_full_output و _apply_school_identity_globals و _current_identity_config في app.py
    for fn_name in ["_identity_full_output", "_apply_school_identity_globals", "_current_identity_config"]:
        in_app = any(n.name == fn_name for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef))
        in_sd = re.search(rf"^def\s+{re.escape(fn_name)}\s*\(", sd_text, flags=re.MULTILINE) is not None
        add(results, f"3K-identity-core: {fn_name} باقية في app.py", "PASS" if in_app else "FAIL", "")
        add(results, f"3K-identity-core: {fn_name} لم تُنقَل لـschool_data.py", "PASS" if not in_sd else "FAIL", "")

    # 6. _identity_full_output لا تزال ترجع 17 عنصراً
    id_nodes = [n for n in ast.walk(app_tree) if isinstance(n, ast.FunctionDef) and n.name == "_identity_full_output"]
    if id_nodes:
        id_node = id_nodes[0]
        ret_counts = [len(s.value.elts) for s in ast.walk(id_node) if isinstance(s, ast.Return) and isinstance(s.value, ast.Tuple)]
        correct_17 = ret_counts == [17]
        add(results, "3K-identity-core: _identity_full_output ترجع 17 عنصراً", "PASS" if correct_17 else "FAIL", f"{ret_counts}")

    # 7. دوال مساعدة منقولة موجودة في school_data.py
    for helper in ["_save_uploaded_identity_logo", "_is_valid_identity_logo_value", "_normalize_identity_text", "_normalize_hex_color"]:
        exists = re.search(rf"^def\s+{re.escape(helper)}\s*\(", sd_text, flags=re.MULTILINE) is not None
        add(results, f"3K-identity-core: {helper} موجودة في school_data.py", "PASS" if exists else "FAIL", "")

    # 8. لا SCHOOL_CONFIG = dict(…) في cores
    no_dangerous = "SCHOOL_CONFIG = dict(" not in sd_text
    add(results, "3K-identity-core: لا SCHOOL_CONFIG = dict(…) في school_data.py", "PASS" if no_dangerous else "FAIL", "")




def check_auth_core_phase3l(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """3L-auth-core: فصل منطق حسابات الدخول إلى auth.py مع بقاء wrappers في app.py."""
    auth_path = path.with_name("auth.py")
    if not auth_path.exists():
        add(results, "3L-auth-core: وجود auth.py", "FAIL", "auth.py غير موجود")
        return

    auth_text = read_text(auth_path)
    try:
        app_tree = ast.parse(app_text)
        auth_tree = ast.parse(auth_text)
    except SyntaxError as exc:
        add(results, "3L-auth-core: AST", "FAIL", str(exc))
        return

    core_names = [
        "save_auth_account_profile_core",
        "change_own_account_pin_core",
        "owner_reset_account_pin_core",
        "owner_toggle_account_status_core",
    ]
    wrapper_names = [
        "save_auth_account_profile",
        "change_own_account_pin",
        "owner_reset_account_pin",
        "owner_toggle_account_status",
    ]
    expected_returns = {
        "save_auth_account_profile": 4,
        "change_own_account_pin": 4,
        "owner_reset_account_pin": 5,
        "owner_toggle_account_status": 4,
    }

    def functions(tree):
        return {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}

    app_funcs = functions(app_tree)
    auth_funcs = functions(auth_tree)

    def deco_names(node):
        names = []
        for deco in getattr(node, "decorator_list", []):
            if isinstance(deco, ast.Name):
                names.append(deco.id)
            elif isinstance(deco, ast.Attribute):
                names.append(deco.attr)
            elif isinstance(deco, ast.Call):
                func = deco.func
                names.append(getattr(func, "id", getattr(func, "attr", "")))
        return names

    def calls_name(node, name):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Name) and func.id == name:
                    return True
                if isinstance(func, ast.Attribute) and func.attr == name:
                    return True
        return False

    def has_gr_ui_call(node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                func = sub.func
                if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    if func.value.id == "gr" and func.attr in {"update", "Warning", "Info"}:
                        return True
        return False

    def return_counts(node):
        counts = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return):
                value = sub.value
                if isinstance(value, ast.Tuple):
                    counts.append(len(value.elts))
                else:
                    counts.append(None)
        return counts

    for core_name in core_names:
        add(results, f"3L-auth-core: وجود {core_name} في auth.py", "PASS" if core_name in auth_funcs else "FAIL", "")

    for wrapper_name in wrapper_names:
        add(results, f"3L-auth-core: وجود wrapper {wrapper_name} في app.py", "PASS" if wrapper_name in app_funcs else "FAIL", "")

    for core_name in core_names:
        node = auth_funcs.get(core_name)
        count = deco_names(node).count("state_locked") if node else 0
        add(results, f"3L-auth-core: {core_name} عليه @state_locked مرة واحدة", "PASS" if count == 1 else "FAIL", f"count={count}")

    for wrapper_name in wrapper_names:
        node = app_funcs.get(wrapper_name)
        count = deco_names(node).count("state_locked") if node else 0
        add(results, f"3L-auth-core: wrapper {wrapper_name} بلا @state_locked", "PASS" if count == 0 else "FAIL", f"count={count}")

    for core_name in core_names:
        node = auth_funcs.get(core_name)
        clean = bool(node) and not has_gr_ui_call(node)
        add(results, f"3L-auth-core: {core_name} بلا gr.update/Warning/Info", "PASS" if clean else "FAIL", "")

    for wrapper_name, core_name in zip(wrapper_names, core_names):
        node = app_funcs.get(wrapper_name)
        calls_core = bool(node) and calls_name(node, core_name)
        add(results, f"3L-auth-core: wrapper {wrapper_name} يستدعي {core_name}", "PASS" if calls_core else "FAIL", "")

    for wrapper_name in wrapper_names:
        node = app_funcs.get(wrapper_name)
        counts = return_counts(node) if node else []
        expected = expected_returns[wrapper_name]
        ok = bool(counts) and all(count == expected for count in counts)
        add(results, f"3L-auth-core: wrapper {wrapper_name} يرجع {expected} عناصر", "PASS" if ok else "FAIL", f"returns={counts}")


def check_final_safety_note_phase3m(path: Path, app_text: str, results: list[CheckResult]) -> None:
    """Phase 3M final safety note: document intentional final exceptions."""
    try:
        app_tree = ast.parse(app_text)
    except SyntaxError as exc:
        add(results, "3M-final-safety-note: تحليل app.py", "FAIL", str(exc))
        return

    app_funcs = {node.name: node for node in app_tree.body if isinstance(node, ast.FunctionDef)}

    def decorator_names(node: ast.FunctionDef | None) -> list[str]:
        names: list[str] = []
        if node is None:
            return names
        for deco in getattr(node, "decorator_list", []):
            if isinstance(deco, ast.Name):
                names.append(deco.id)
            elif isinstance(deco, ast.Attribute):
                names.append(deco.attr)
            elif isinstance(deco, ast.Call):
                func = deco.func
                names.append(getattr(func, "id", getattr(func, "attr", "")))
        return names

    clear_node = app_funcs.get("clear_all_data")
    add(results, "3M-final-safety-note: clear_all_data موجودة في app.py", "PASS" if clear_node else "FAIL", "system reset مؤجلة عمدًا")

    clear_lock_count = decorator_names(clear_node).count("state_locked") if clear_node else 0
    add(results, "3M-final-safety-note: clear_all_data تحمل @state_locked", "PASS" if clear_lock_count == 1 else "FAIL", f"count={clear_lock_count}")

    locked_funcs = [name for name, node in app_funcs.items() if "state_locked" in decorator_names(node)]
    add(results, "3M-final-safety-note: clear_all_data هي الدالة الوحيدة المقفلة في app.py", "PASS" if locked_funcs == ["clear_all_data"] else "FAIL", f"locked={locked_funcs}")

    school_path = path.with_name("school_data.py")
    legacy_ref_funcs = {"save_admin_reference_file", "save_phones_reference_file", "save_schedule_reference_file"}
    if not school_path.exists():
        add(results, "3M-final-safety-note: school_data.py موجود", "FAIL", str(school_path))
    else:
        school_text = read_text(school_path)
        try:
            school_tree = ast.parse(school_text)
            school_funcs = {node.name: node for node in school_tree.body if isinstance(node, ast.FunctionDef)}
            gr_update_locations: list[str] = []
            for func_name, func_node in school_funcs.items():
                for sub in ast.walk(func_node):
                    if isinstance(sub, ast.Call):
                        call_func = sub.func
                        if isinstance(call_func, ast.Attribute) and isinstance(call_func.value, ast.Name):
                            if call_func.value.id == "gr" and call_func.attr == "update":
                                gr_update_locations.append(func_name)
            unique_locations = sorted(set(gr_update_locations))
            confined = bool(unique_locations) and set(unique_locations).issubset(legacy_ref_funcs)
            add(results, "3M-final-safety-note: gr.update في school_data.py محصور في دوال 3E-a المرجعية", "PASS" if confined else "FAIL", f"functions={unique_locations}")
        except SyntaxError as exc:
            add(results, "3M-final-safety-note: تحليل school_data.py", "FAIL", str(exc))

    add(results, "3M-final-safety-note: clear_all_data مؤجلة عمدًا", "INFO", "system reset شاملة بعقد كبير؛ لا تُفصل الآن.")
    add(results, "3M-final-safety-note: gr.update في school_data.py موروث وموثق", "INFO", "محصور في دوال حفظ الملفات المرجعية من 3E-a ومقبول مؤقتًا.")

def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Masar safety checker")
    parser.add_argument("source", nargs="?", default="app.py", help="مسار ملف app.py أو نسخة منظومة مسار")
    parser.add_argument("--json", action="store_true", help="إخراج النتيجة بصيغة JSON")
    parser.add_argument("--warn-as-fail", action="store_true", help="اعتبار التحذيرات فشلًا")
    parser.add_argument("--expected-symbols", help="تجاوز أعداد الرموز، مثال: ❌=52,🤝=9,🦅=5,⚠️=22")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.source).resolve()
    results: list[CheckResult] = []

    if not path.exists():
        add(results, "وجود الملف", "FAIL", f"الملف غير موجود: {path}")
        print_results(results)
        return 1

    app_text = read_text(path)
    style_text = collect_style_text(path)
    extra_module_texts = []
    for module_name in ("school_data.py", "schedules.py", "balances.py", "exemptions.py", "swaps.py", "distribution.py", "storage.py", "auth.py"):
        module_path = path.with_name(module_name)
        if module_path.exists():
            try:
                extra_module_texts.append(module_path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                extra_module_texts.append(module_path.read_text(encoding="utf-8-sig"))
    code_text = app_text + "\n" + "\n".join(extra_module_texts)
    combined_text = code_text + "\n" + style_text

    add(results, "ملف الفحص", "INFO", str(path))
    add(results, "عدد الأسطر", "INFO", str(len(app_text.splitlines())))
    if style_text:
        add(results, "CSS خارجي", "INFO", "تم العثور على ملف CSS خارجي وضمّه للفحص.")

    expected_symbols = parse_expected_symbols(args.expected_symbols)

    check_syntax(path, results)
    check_forbidden_patterns(app_text, results)
    check_required_markers(combined_text, app_text, results)
    check_symbol_counts_across_modules(path, results, expected_symbols)
    check_excel_and_periods(code_text, results)
    check_error_updates(app_text, results)
    check_exemption_centralization(code_text, results)
    check_day_filter_isolation(app_text, results)
    check_shared_pin(app_text, results)
    check_css_markers(combined_text, results)
    check_external_css_extraction(path, app_text, style_text, results)
    check_config_phase3a(path, app_text, results)
    check_storage_phase3b(path, app_text, results)
    check_auth_phase3d(path, app_text, results)
    check_state_phase3e_pre(path, app_text, results)
    check_school_data_phase3ea(path, app_text, results)
    check_data_center_reference_refresh_phase3k(path, app_text, results)
    check_schedules_phase3fa(path, app_text, results)
    check_balances_phase3ga(path, app_text, results)
    check_time_helper_phase3eb1(path, app_text, results)
    check_refresh_schedule_core_phase3eb2(path, app_text, results)
    check_gradio_bound_helpers_phase3eb3(path, app_text, results)
    check_process_uploaded_excel_phase3eb4(path, app_text, results)
    check_audit_logging_phase3ha1(path, app_text, results)
    check_exemptions_phase3ha2(path, app_text, results)
    check_save_teacher_rules_phase3ha3(path, app_text, results)
    check_swaps_phase3ia1(path, app_text, results)
    check_confirm_swap_phase3ia3(path, app_text, results)
    check_run_radar_safe_phase3ia4a(path, app_text, results)
    check_generate_wa_msg_phase3ia4b(path, app_text, results)
    check_get_swap_candidates_phase3ia4c(path, app_text, results)
    check_on_swap_option_selected_phase3ia4d(path, app_text, results)
    check_swap_context_phase3ia5a(path, app_text, results)
    check_swap_filter_periods_phase3ia5b(path, app_text, results)
    check_export_swaps_excel_phase3ia6a(path, app_text, results)
    check_generate_swap_table_image_phase3ia6b(path, app_text, results)
    check_distribution_phase3ja1(path, app_text, results)
    check_distribution_phase3ja2fix(path, app_text, results)
    check_distribution_phase3ja3(path, app_text, results)
    check_distribution_phase3jb1(path, app_text, results)
    check_assign_prereqs_phase3jc1fix(path, app_text, results)
    check_assign_logic_phase3jc2(path, app_text, results)
    check_cancel_teacher_absence_phase3jc3(path, app_text, results)
    check_process_admin_action_phase3jd1(path, app_text, results)
    check_update_manual_count_phase3jd2(path, app_text, results)
    check_reset_monthly_balances_phase3jd3(path, app_text, results)
    check_staff_management_phase3jd4(path, app_text, results)
    check_draw_schedule_image_phase3je1(path, app_text, results)
    check_generation_orchestration_phase3je2fix(path, app_text, results)
    check_rollback_auto_assignments_phase3je3(path, app_text, results)
    check_potential_dead_code_admin_excel_phase3j_final(path, app_text, results)
    check_school_settings_core_phase3k(path, app_text, results)
    check_identity_reference_fix_phase3k(path, app_text, results)
    check_identity_core_phase3k(path, app_text, results)
    check_auth_core_phase3l(path, app_text, results)
    check_final_safety_note_phase3m(path, app_text, results)

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        print_results(results)

    passed, warn, fail, _ = summarize(results)
    if fail:
        return 1
    if warn and args.warn_as_fail:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
