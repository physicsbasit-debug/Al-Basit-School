# -*- coding: utf-8 -*-
"""اختبارات أولية لـ school_data cores الخاصة بإعدادات المدرسة."""

from __future__ import annotations

import school_data
from config import DEFAULT_SCHOOL_CONFIG


def _default_config():
    return dict(DEFAULT_SCHOOL_CONFIG)


def test_save_school_operational_settings_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(school_data, "load_school_config", _default_config)
    result = school_data.save_school_operational_settings_core(
        7,
        is_owner=False,
    )
    assert result["periods_value"] in (7, 8)
    assert "رفض الحفظ" in result["message"]


def test_save_school_identity_settings_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(school_data, "load_school_config", _default_config)
    config, status_html, apply_globals = school_data.save_school_identity_settings_core(
        "مدرسة اختبار",
        "جنوب الباطنة",
        "https://example.com/logo.png",
        None,
        "#004d40",
        "#00695c",
        "#ffca28",
        is_owner=False,
    )
    assert isinstance(config, dict)
    assert apply_globals is False
    assert "رفض الحفظ" in status_html


def test_reset_school_identity_settings_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(school_data, "load_school_config", _default_config)
    config, status_html, apply_globals = school_data.reset_school_identity_settings_core(
        is_owner=False,
    )
    assert isinstance(config, dict)
    assert apply_globals is False
    assert "رفض الاستعادة" in status_html


def test_save_school_identity_settings_rejects_invalid_hex_colors(monkeypatch):
    monkeypatch.setattr(school_data, "load_school_config", _default_config)
    config, status_html, apply_globals = school_data.save_school_identity_settings_core(
        "مدرسة اختبار",
        "جنوب الباطنة",
        "https://example.com/logo.png",
        None,
        "not-a-color",
        "#00695c",
        "#ffca28",
        is_owner=True,
    )
    assert isinstance(config, dict)
    assert apply_globals is False
    assert "HEX" in status_html
