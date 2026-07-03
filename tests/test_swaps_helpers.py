# -*- coding: utf-8 -*-
"""اختبارات خفيفة لدوال التبادل المساعدة في swaps.py."""

from __future__ import annotations

import swaps
import storage

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


def test_confirm_swap_core_success_saves_swap_and_writes_real_audit_log(tmp_path, monkeypatch):
    swap_db_file = tmp_path / "confirmed_swaps.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(storage, "SWAP_DB_FILE", str(swap_db_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))

    teachers = {
        "أ. طالب التبادل": {
            "dept": "العلوم",
            "role": "معلم",
            "phone": "91111111",
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
    }
    choice = "🟢 تبادل مثالي | البديل: أ. المرشح المثالي | يغطيك (الأحد ح2) وتغطيه (الإثنين ح4)"
    msg_text = "رسالة واتساب اختبارية للتبادل الودي"

    swaps.teachers_db.clear()
    swaps.teachers_db.update(teachers)
    swaps.swap_db.clear()

    try:
        result_state, warning = swaps.confirm_swap_core(
            "أ. طالب التبادل",
            "الحصة 2",
            choice,
            "الأحد",
            msg_text,
            state={},
            actor_name="مالك الاختبار",
            actor_role="صاحب النظام",
        )

        assert warning == ""
        assert isinstance(result_state, dict)
        assert len(result_state) == 1
        assert "2" in result_state

        state_swap = result_state["2"]
        assert state_swap["requester"] == "أ. طالب التبادل"
        assert state_swap["candidate"] == "أ. المرشح المثالي"
        assert state_swap["comp_day"] == "الإثنين"
        assert state_swap["comp_period"] == "الحصة 4"
        assert state_swap["class"] == "1 - مادة تاسع"
        assert state_swap["message"] == msg_text

        swap_key = "أ. طالب التبادل|الأحد|2"
        assert swap_key in swaps.swap_db
        saved_swap = swaps.swap_db[swap_key]
        assert saved_swap["day"] == "الأحد"
        assert saved_swap["period"] == "2"
        assert saved_swap["requester"] == "أ. طالب التبادل"
        assert saved_swap["candidate"] == "أ. المرشح المثالي"
        assert saved_swap["comp_day"] == "الإثنين"
        assert saved_swap["comp_period"] == "الحصة 4"
        assert saved_swap["class"] == "1 - مادة تاسع"
        assert saved_swap["message"] == msg_text
        assert saved_swap["updated_at"]

        assert swap_db_file.exists()
        assert audit_log_file.exists()

        import json

        with open(swap_db_file, "r", encoding="utf-8") as swaps_file:
            saved_payload = json.load(swaps_file)
        assert saved_payload == swaps.swap_db

        with open(audit_log_file, "r", encoding="utf-8") as audit_file:
            audit_records = json.load(audit_file)

        assert len(audit_records) == 1
        record = audit_records[0]
        assert record["action"] == "اعتماد تبادل ودي"
        assert record["actor_name"] == "مالك الاختبار"
        assert record["actor_role"] == "صاحب النظام"
        assert record["target_teacher"] == "أ. طالب التبادل"
        assert record["old_value"] == ""
        assert record["new_value"]["day"] == "الأحد"
        assert record["new_value"]["period"] == "2"
        assert record["new_value"]["class"] == "1 - مادة تاسع"
        assert record["new_value"]["candidate"] == "أ. المرشح المثالي"
        assert record["new_value"]["comp_day"] == "الإثنين"
        assert record["new_value"]["comp_period"] == "الحصة 4"
        assert "اعتماد تبادل ودي بين أ. طالب التبادل و أ. المرشح المثالي" in record["details"]
        assert record["source"]
    finally:
        swaps.teachers_db.clear()
        swaps.swap_db.clear()

