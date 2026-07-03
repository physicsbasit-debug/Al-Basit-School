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


def test_save_school_operational_settings_success_changes_periods_and_writes_real_audit_log(tmp_path, monkeypatch):
    school_config_file = tmp_path / "school_config.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(storage, "SCHOOL_CONFIG_FILE", str(school_config_file))
    monkeypatch.setattr(school_data, "SCHOOL_CONFIG_FILE", str(school_config_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))

    initial_config = dict(DEFAULT_SCHOOL_CONFIG)
    initial_config["periods_per_day"] = 7
    school_config_file.write_text(
        json.dumps(initial_config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = school_data.save_school_operational_settings_core(
        8,
        is_owner=True,
        actor_name="مالك الاختبار",
        actor_role="صاحب النظام",
    )

    assert set(result.keys()) == {
        "periods_value",
        "message",
        "summary_config",
        "status_config",
    }
    assert result["periods_value"] == 8
    assert "تم حفظ عدد الحصص اليومية" in result["message"]
    assert result["summary_config"]["periods_per_day"] == 8
    assert result["status_config"]["periods_per_day"] == 8

    with open(school_config_file, "r", encoding="utf-8") as config_file:
        saved_config = json.load(config_file)
    assert saved_config["periods_per_day"] == 8

    assert audit_log_file.exists()
    with open(audit_log_file, "r", encoding="utf-8") as audit_file:
        audit_records = json.load(audit_file)

    assert len(audit_records) == 1
    record = audit_records[0]
    assert record["action"] == "تعديل إعداد عدد الحصص اليومية"
    assert record["actor_name"] == "مالك الاختبار"
    assert record["actor_role"] == "صاحب النظام"
    assert record["target_teacher"] == ""
    assert record["old_value"] == 7
    assert record["new_value"] == 8
    assert "تحديث عدد الحصص اليومية" in record["details"]
    assert record["source"]


def test_save_school_identity_settings_success_saves_identity_without_logo_upload(tmp_path, monkeypatch):
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
            "school_name": "مدرسة قديمة",
            "directorate_region": "محافظة قديمة",
            "logo_url": "https://example.com/old-logo.png",
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

    new_config, status_html, apply_globals = school_data.save_school_identity_settings_core(
        school_name="مدرسة اختبار",
        directorate_region="محافظة اختبار",
        logo_url="https://example.com/logo.png",
        logo_upload=None,
        theme_color="#004D40",
        theme_color_2="#00695C",
        accent_color="#FFCA28",
        is_owner=True,
    )

    assert apply_globals is True
    assert "تم حفظ" in status_html
    assert "هوية" in status_html

    assert new_config["school_name"] == "مدرسة اختبار"
    assert new_config["directorate_region"] == "محافظة اختبار"
    assert new_config["logo_url"] == "https://example.com/logo.png"
    assert new_config["theme_color"] == "#004d40"
    assert new_config["theme_color_2"] == "#00695c"
    assert new_config["accent_color"] == "#ffca28"
    assert new_config["periods_per_day"] == 8

    for key in school_data.FIXED_IDENTITY_KEYS:
        assert new_config[key] == DEFAULT_SCHOOL_CONFIG[key]

    with open(school_config_file, "r", encoding="utf-8") as config_file:
        saved_config = json.load(config_file)

    assert saved_config["school_name"] == "مدرسة اختبار"
    assert saved_config["directorate_region"] == "محافظة اختبار"
    assert saved_config["logo_url"] == "https://example.com/logo.png"
    assert saved_config["theme_color"] == "#004d40"
    assert saved_config["theme_color_2"] == "#00695c"
    assert saved_config["accent_color"] == "#ffca28"
    assert saved_config["periods_per_day"] == 8

    for key in school_data.FIXED_IDENTITY_KEYS:
        assert saved_config[key] == DEFAULT_SCHOOL_CONFIG[key]

