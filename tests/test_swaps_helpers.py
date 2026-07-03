# -*- coding: utf-8 -*-
"""اختبارات خفيفة لدوال التبادل المساعدة في swaps.py."""

from __future__ import annotations

import swaps

from swaps import extract_clean_period_number, format_elegant_class


def test_extract_clean_period_number_plain_digit():
    assert extract_clean_period_number("3") == "3"


def test_extract_clean_period_number_from_arabic_label():
    assert extract_clean_period_number("الحصة 3") == "3"


def test_extract_clean_period_number_empty_string_returns_empty_string():
    assert extract_clean_period_number("") == ""


def test_format_elegant_class_returns_string_for_simple_class():
    result = format_elegant_class("تاسع 1")
    assert isinstance(result, str)
    assert result


def test_run_radar_safe_core_returns_perfect_swap_candidate(monkeypatch):
    monkeypatch.setattr(swaps, "get_current_day_oman", lambda: "الأحد")

    teachers = {
        "أ. طالب التبادل": {
            "dept": "العلوم",
            "role": "معلم",
            "الأحد": {"2": "تاسع 1"},
            "الإثنين": {},
        },
        "أ. المرشح المثالي": {
            "dept": "العلوم",
            "role": "معلم",
            "phone": "91234567",
            "الأحد": {},
            "الإثنين": {"4": "تاسع 1"},
        },
        "أ. إداري مستبعد": {
            "dept": "الهيئة الإدارية",
            "role": "إداري",
            "الأحد": {},
            "الإثنين": {"3": "تاسع 1"},
        },
    }
    swaps.teachers_db.clear()
    swaps.teachers_db.update(teachers)

    try:
        result = swaps.run_radar_safe_core("أ. طالب التبادل", "الحصة 2", "الأحد")

        assert isinstance(result, list)
        assert result
        assert any("🟢 تبادل مثالي" in item for item in result)
        assert any("أ. المرشح المثالي" in item for item in result)
        assert any("يغطيك (الأحد ح2)" in item for item in result)
        assert any("وتغطيه (الإثنين ح4)" in item for item in result)
        assert all("أ. إداري مستبعد" not in item for item in result)
        assert "خطأ داخلي" not in result
    finally:
        swaps.teachers_db.clear()

