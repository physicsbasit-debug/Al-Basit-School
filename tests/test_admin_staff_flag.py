# -*- coding: utf-8 -*-
"""اختبارات 5A-admin-staff-flag: فصل role النصي عن الحكم الوظيفي is_admin_staff."""

from __future__ import annotations

import json

import pandas as pd

import balances
import distribution
import exemptions
import schedules
import storage

DAY_NAME = "الأحد"


def _teacher(dept="العلوم", role="معلم", is_admin_staff=False, cover_count=0, absent_count=0, shortcoming_count=0, schedule=None):
    return {
        "dept": dept,
        "role": role,
        "is_admin_staff": is_admin_staff,
        "cover_count": cover_count,
        "absent_count": absent_count,
        "shortcoming_count": shortcoming_count,
        "phone": "",
        "specialty": "",
        "exempt_days": [],
        "exempt_periods": [],
        "exempt_slots": [],
        "absence_dates": [],
        DAY_NAME: dict(schedule or {}),
        "الإثنين": {},
        "الثلاثاء": {},
        "الأربعاء": {},
        "الخميس": {},
    }


def _reset_shared_state():
    storage.teachers_db.clear()
    distribution.daily_db.clear()
    distribution.processed_absences.clear()
    distribution.last_assigned_teachers.clear()


def test_load_db_migrates_is_admin_staff_without_overwriting_existing_value(tmp_path, monkeypatch):
    db_file = tmp_path / "school_balances.json"
    db_file.write_text(
        json.dumps(
            {
                "إداري قديم": {"dept": "الهيئة الإدارية", "role": "مسمى حر"},
                "دعم داخل قسم": {"dept": "العلوم", "role": "فني مختبر علوم"},
                "معلم عادي": {"dept": "العلوم", "role": "معلم"},
                "قيمة محفوظة": {"dept": "الهيئة الإدارية", "role": "مسمى حر", "is_admin_staff": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(storage, "DB_FILE", str(db_file))
    storage.load_db()

    assert storage.teachers_db["إداري قديم"]["is_admin_staff"] is True
    assert storage.teachers_db["دعم داخل قسم"]["is_admin_staff"] is True
    assert storage.teachers_db["معلم عادي"]["is_admin_staff"] is False
    assert storage.teachers_db["قيمة محفوظة"]["is_admin_staff"] is False

    _reset_shared_state()


def test_is_admin_staff_excluded_from_teacher_lists_day_overview_and_balances():
    _reset_shared_state()
    storage.teachers_db["فني علوم"] = _teacher(role="فني مختبر علوم", is_admin_staff=True, cover_count=5, absent_count=2, shortcoming_count=1)
    storage.teachers_db["معلم علوم"] = _teacher(role="معلم", is_admin_staff=False, cover_count=1, absent_count=1, shortcoming_count=1)

    assert "فني علوم" not in " ".join(schedules.get_teacher_choices("الكل"))
    assert "فني علوم" not in " ".join(schedules.get_absentee_choices("الكل"))
    assert "فني علوم" not in " ".join(distribution.get_teacher_schedule_choices("الكل"))

    day_overview = schedules.get_day_overview(DAY_NAME, "الكل")
    assert "فني علوم" not in day_overview.to_string()
    assert "معلم علوم" in day_overview.to_string()

    assert "فني علوم" not in balances.get_updated_balance("الكل")
    assert "فني علوم" not in balances.get_updated_absences("الكل")
    assert "فني علوم" not in balances.get_updated_shortcomings("الكل")

    _reset_shared_state()


def test_is_admin_staff_controls_distribution_and_admin_supervision_lists(monkeypatch):
    _reset_shared_state()
    storage.teachers_db["الغائب"] = _teacher(schedule={"1": "تاسع 1"})
    storage.teachers_db["دعم داخل قسم"] = _teacher(role="فني مختبر علوم", is_admin_staff=True)
    storage.teachers_db["إداري مسمى حر"] = _teacher(dept="قسم خاص", role="أخصائي قواعد بيانات", is_admin_staff=True)

    monkeypatch.setattr(distribution, "get_date_of_weekday", lambda day: "2026-07-05")
    monkeypatch.setattr(distribution, "save_db", lambda: None)
    monkeypatch.setattr(distribution, "save_daily_db", lambda: None)
    monkeypatch.setattr(distribution, "_flush_audit_changes", lambda *args, **kwargs: None)

    distribution.assign_logic_core(["الغائب"], DAY_NAME, "العلوم", 3, False, True)
    rows = [row for row in distribution.daily_db if row.get("المعلم الغائب") == "الغائب"]
    assert rows[0]["المعلم البديل"] == "إشراف إداري"

    choices, value, interactive = distribution.update_available_subs_smart_core(
        "الغائب", "الحصة 1", "الهيئة الإدارية", DAY_NAME, pd.DataFrame(rows), True
    )
    assert interactive is True
    assert any("إداري مسمى حر" in choice for choice in choices)

    _reset_shared_state()


def test_exemptions_reject_admin_staff_and_manual_staff_saves_flag(monkeypatch):
    _reset_shared_state()
    monkeypatch.setattr(distribution, "save_db", lambda: None)
    monkeypatch.setattr(exemptions, "save_db", lambda: None)

    raw = distribution.add_manual_staff_core(
        "فني جديد", "الهيئة الإدارية", "91234567", "فني مختبر علوم", True, "الكل", is_owner=True
    )
    assert "تم إضافة" in raw["message"]
    assert storage.teachers_db["فني جديد"]["role"] == "فني مختبر علوم"
    assert storage.teachers_db["فني جديد"]["is_admin_staff"] is True

    message = exemptions.save_teacher_rules_core("فني جديد", [DAY_NAME], [1], is_admin=True, is_owner=True)
    assert "لا يمكن تسجيل حالات إعفاء" in message

    _reset_shared_state()


def test_owner_can_update_is_admin_staff_from_vault(monkeypatch):
    _reset_shared_state()
    monkeypatch.setattr(distribution, "save_db", lambda: None)
    monkeypatch.setattr(distribution, "write_audit_log", lambda *args, **kwargs: None)

    storage.teachers_db["موظف مرن"] = _teacher(role="معلم", is_admin_staff=False)
    distribution.update_manual_count_core(
        "موظف مرن",
        None,
        None,
        None,
        None,
        None,
        "أخصائي أنظمة مدرسية",
        True,
        "الكل",
        DAY_NAME,
        None,
        [],
        is_owner=True,
    )

    assert storage.teachers_db["موظف مرن"]["role"] == "أخصائي أنظمة مدرسية"
    assert storage.teachers_db["موظف مرن"]["is_admin_staff"] is True

    _reset_shared_state()
