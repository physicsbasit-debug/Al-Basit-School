# -*- coding: utf-8 -*-
"""اختبارات خفيفة لدوال school_data.py المساعدة."""

from __future__ import annotations

from school_data import (
    _is_valid_identity_logo_value,
    _normalize_hex_color,
    validate_reference_filename,
)


def test_normalize_hex_color_accepts_valid_hex():
    assert _normalize_hex_color("#004d40", "#111111") == "#004d40"


def test_normalize_hex_color_uses_fallback_for_invalid_color():
    assert _normalize_hex_color("red", "#004d40") == "#004d40"


def test_is_valid_identity_logo_value_rejects_empty_value():
    assert _is_valid_identity_logo_value("") is False


def test_is_valid_identity_logo_value_accepts_https_url():
    assert _is_valid_identity_logo_value("https://example.com/logo.png") is True


def test_validate_reference_filename_accepts_expected_keyword_and_rejects_other_names():
    is_valid, message = validate_reference_filename("إداريين.xlsx", ["إداريين"])
    assert is_valid is True
    assert message == ""

    is_valid, message = validate_reference_filename("عشوائي.xlsx", ["إداريين"])
    assert is_valid is False
    assert message
