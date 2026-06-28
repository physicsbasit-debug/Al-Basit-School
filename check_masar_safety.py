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
import json
import py_compile
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable


EXPECTED_SYMBOL_COUNTS = {
    "❌": 56,
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


def check_symbol_counts(text: str, results: list[CheckResult], expected: dict[str, int]) -> None:
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
        ("assign_logic", 1),
        ("update_available_subs_smart", 1),
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


def parse_expected_symbols(raw: str | None) -> dict[str, int]:
    if not raw:
        return dict(EXPECTED_SYMBOL_COUNTS)
    expected = dict(EXPECTED_SYMBOL_COUNTS)
    # صيغة: ❌=56,🤝=9,🦅=5,⚠️=22
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"صيغة غير صحيحة للرموز: {part}")
        symbol, value = part.split("=", 1)
        expected[symbol.strip()] = int(value.strip())
    return expected


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Masar safety checker")
    parser.add_argument("source", nargs="?", default="app.py", help="مسار ملف app.py أو نسخة منظومة مسار")
    parser.add_argument("--json", action="store_true", help="إخراج النتيجة بصيغة JSON")
    parser.add_argument("--warn-as-fail", action="store_true", help="اعتبار التحذيرات فشلًا")
    parser.add_argument("--expected-symbols", help="تجاوز أعداد الرموز، مثال: ❌=56,🤝=9,🦅=5,⚠️=22")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.source).resolve()
    results: list[CheckResult] = []

    if not path.exists():
        add(results, "وجود الملف", "FAIL", f"الملف غير موجود: {path}")
        print_results(results)
        return 1

    app_text = read_text(path)
    style_text = collect_style_text(path)
    combined_text = app_text + "\n" + style_text

    add(results, "ملف الفحص", "INFO", str(path))
    add(results, "عدد الأسطر", "INFO", str(len(app_text.splitlines())))
    if style_text:
        add(results, "CSS خارجي", "INFO", "تم العثور على ملف CSS خارجي وضمّه للفحص.")

    expected_symbols = parse_expected_symbols(args.expected_symbols)

    check_syntax(path, results)
    check_forbidden_patterns(app_text, results)
    check_required_markers(combined_text, app_text, results)
    check_symbol_counts(app_text, results, expected_symbols)
    check_excel_and_periods(app_text, results)
    check_error_updates(app_text, results)
    check_exemption_centralization(app_text, results)
    check_day_filter_isolation(app_text, results)
    check_shared_pin(app_text, results)
    check_css_markers(combined_text, results)

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
