# -*- coding: utf-8 -*-
"""اختبارات خفيفة لدوال التبادل المساعدة في swaps.py."""

from __future__ import annotations

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