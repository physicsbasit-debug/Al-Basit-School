# -*- coding: utf-8 -*-
"""اختبارات أولية لـ school_data cores الخاصة بإعدادات المدرسة."""

from __future__ import annotations

import json

import school_data
import storage
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


def test_reset_school_identity_settings_success_restores_defaults_and_preserves_operational_config(tmp_path, monkeypatch):
    school_config_file = tmp_path / "school_config.json"

    monkeypatch.setattr(storage, "SCHOOL_CONFIG_FILE", str(school_config_file))
    monkeypatch.setattr(school_data, "SCHOOL_CONFIG_FILE", str(school_config_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))

    custom_config = dict(DEFAULT_SCHOOL_CONFIG)
    custom_config.update(
        {
            "ministry_name": "وزارة معدلة",
            "directorate_prefix": "مديرية معدلة",
            "system_name": "نظام معدل",
            "system_subtitle": "عنوان فرعي معدل",
            "developer_credit": "اعتماد معدل",
            "school_name": "مدرسة اختبار معدلة",
            "directorate_region": "محافظة اختبار",
            "logo_url": "https://example.com/custom-logo.png",
            "theme_color": "#111111",
            "theme_color_2": "#222222",
            "accent_color": "#333333",
            "periods_per_day": 8,
        }
    )
    school_config_file.write_text(
        json.dumps(custom_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    config, status_html, apply_globals = school_data.reset_school_identity_settings_core(
        is_owner=True,
    )

    assert apply_globals is True
    assert "تمت استعادة الهوية الافتراضية" in status_html

    for key in school_data.FIXED_IDENTITY_KEYS + school_data.IDENTITY_CONFIG_KEYS:
        assert config[key] == DEFAULT_SCHOOL_CONFIG[key]

    assert config["periods_per_day"] == 8

    with open(school_config_file, "r", encoding="utf-8") as config_file:
        saved_config = json.load(config_file)

    for key in school_data.FIXED_IDENTITY_KEYS + school_data.IDENTITY_CONFIG_KEYS:
        assert saved_config[key] == DEFAULT_SCHOOL_CONFIG[key]

    assert saved_config["periods_per_day"] == 8

