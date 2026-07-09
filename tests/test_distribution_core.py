# -*- coding: utf-8 -*-
"""اختبارات أولية لقلب التوزيع في distribution.py."""

from __future__ import annotations

import pytest
import pandas as pd

import distribution

DAY_NAME = "الأحد"
TARGET_DATE = "2026-07-05"
DEPT = "العلوم"


def _teacher(dept=DEPT, role="معلم", cover_count=0, absent_count=0, schedule=None, is_admin_staff=False):
    return {
        "dept": dept,
        "role": role,
        "is_admin_staff": is_admin_staff,
        "cover_count": cover_count,
        "absent_count": absent_count,
        DAY_NAME: dict(schedule or {}),
        "absence_dates": [],
    }


def _absent_teacher(name="المعلم الغائب"):
    distribution.teachers_db[name] = _teacher(schedule={"1": "تاسع 1"})
    return name


def _daily_rows_for(absent_name):
    return [
        row
        for row in distribution.daily_db
        if row.get("date") == TARGET_DATE and row.get("المعلم الغائب") == absent_name
    ]


@pytest.fixture(autouse=True)
def isolated_distribution_state(monkeypatch):
    distribution.teachers_db.clear()
    distribution.daily_db.clear()
    distribution.processed_absences.clear()
    distribution.last_assigned_teachers.clear()

    monkeypatch.setattr(distribution, "get_date_of_weekday", lambda day: TARGET_DATE)
    monkeypatch.setattr(distribution.random, "shuffle", lambda items: None)
    monkeypatch.setattr(distribution, "save_db", lambda: None)
    monkeypatch.setattr(distribution, "save_daily_db", lambda: None)
    monkeypatch.setattr(distribution, "_flush_audit_changes", lambda *args, **kwargs: None)

    yield

    distribution.teachers_db.clear()
    distribution.daily_db.clear()
    distribution.processed_absences.clear()
    distribution.last_assigned_teachers.clear()


def test_assign_logic_uses_admin_supervision_when_no_valid_substitute():
    absent = _absent_teacher()
    distribution.teachers_db["معلم قسم آخر"] = _teacher(dept="الرياضة")
    distribution.teachers_db["عضو إداري"] = _teacher(role="مدير المدرسة", is_admin_staff=True)

    distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)

    rows = _daily_rows_for(absent)
    assert len(rows) == 1
    assert rows[0]["المعلم البديل"] == "إشراف إداري"


def test_assign_logic_selects_single_valid_substitute_and_increments_cover_count():
    absent = _absent_teacher()
    sub = "المعلم البديل"
    distribution.teachers_db[sub] = _teacher(cover_count=0)

    result = distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)

    rows = _daily_rows_for(absent)
    assert len(rows) == 1
    assert rows[0]["المعلم البديل"] == sub
    assert distribution.teachers_db[sub]["cover_count"] == 1
    assert distribution.teachers_db[absent]["absent_count"] == 1
    assert result["refresh_current_abs"] == [absent]


def test_assign_logic_excludes_exempt_teacher(monkeypatch):
    absent = _absent_teacher()
    sub = "معلم معفى"
    distribution.teachers_db[sub] = _teacher(cover_count=0)
    monkeypatch.setattr(
        distribution,
        "is_teacher_exempt_for_slot",
        lambda teacher, day, period: teacher == sub,
    )

    distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)

    rows = _daily_rows_for(absent)
    assert len(rows) == 1
    assert rows[0]["المعلم البديل"] == "إشراف إداري"
    assert distribution.teachers_db[sub]["cover_count"] == 0


def test_assign_logic_processed_absences_prevents_duplicate_absent_count():
    absent = _absent_teacher()

    distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)
    distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)

    assert distribution.teachers_db[absent]["absent_count"] == 1
    assert (TARGET_DATE, absent) in distribution.processed_absences


def test_cancel_teacher_absence_core_reverses_assignment_effects():
    absent = _absent_teacher()
    sub = "المعلم البديل"
    distribution.teachers_db[sub] = _teacher(cover_count=0)

    distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, False, True)
    result = distribution.cancel_teacher_absence_core(absent, DAY_NAME, DEPT, True, [absent])

    assert distribution.teachers_db[absent]["absent_count"] == 0
    assert distribution.teachers_db[sub]["cover_count"] == 0
    assert _daily_rows_for(absent) == []
    assert (TARGET_DATE, absent) not in distribution.processed_absences
    assert result["refresh_current_abs"] == []


def test_assign_logic_alt_regeneration_removes_old_auto_assignment_and_reassigns():
    absent = _absent_teacher()
    old_sub = "بديل قديم"
    new_sub = "بديل جديد"
    distribution.teachers_db[old_sub] = _teacher(dept="الرياضة", cover_count=1)
    distribution.teachers_db[new_sub] = _teacher(cover_count=0)
    distribution.daily_db.append(
        {
            "date": TARGET_DATE,
            "dept": DEPT,
            "المعلم الغائب": absent,
            "الصف": "تاسع 1",
            "الحصة": "1",
            "المعلم البديل": old_sub,
            "حالة_التكليف": "",
        }
    )

    result = distribution.assign_logic_core([absent], DAY_NAME, DEPT, 3, True, True)

    rows = _daily_rows_for(absent)
    assert len(rows) == 1
    assert rows[0]["المعلم البديل"] == new_sub
    assert distribution.teachers_db[old_sub]["cover_count"] == 0
    assert distribution.teachers_db[new_sub]["cover_count"] == 1
    assert result["refresh_current_abs"] == [absent]


def test_cancel_teacher_absence_core_returns_unchanged_for_empty_absent_name():
    current_abs = ["المعلم الغائب"]

    result = distribution.cancel_teacher_absence_core("", DAY_NAME, DEPT, True, current_abs)

    assert result == {
        "refresh_dept": DEPT,
        "refresh_day": DAY_NAME,
        "refresh_is_admin": True,
        "refresh_current_abs": current_abs,
    }
    assert distribution.daily_db == []


def test_cancel_teacher_absence_core_returns_unchanged_for_whitespace_absent_name():
    current_abs = ["المعلم الغائب"]

    result = distribution.cancel_teacher_absence_core("   ", DAY_NAME, DEPT, True, current_abs)

    assert result == {
        "refresh_dept": DEPT,
        "refresh_day": DAY_NAME,
        "refresh_is_admin": True,
        "refresh_current_abs": current_abs,
    }
    assert distribution.daily_db == []


def test_rollback_auto_assignments_removes_auto_row_and_restores_cover_count():
    absent = _absent_teacher()
    old_sub = "بديل آلي"
    distribution.teachers_db[old_sub] = _teacher(cover_count=1)
    distribution.daily_db.append(
        {
            "date": TARGET_DATE,
            "dept": DEPT,
            "المعلم الغائب": absent,
            "الصف": "تاسع 1",
            "الحصة": "1",
            "المعلم البديل": old_sub,
            "حالة_التكليف": "",
        }
    )

    distribution.rollback_auto_assignments_for_absentees_core([absent], DAY_NAME)

    assert _daily_rows_for(absent) == []
    assert distribution.teachers_db[old_sub]["cover_count"] == 0


def test_rollback_auto_assignments_removes_modified_row_without_changing_cover_count():
    absent = _absent_teacher()
    old_sub = "بديل معدل"
    distribution.teachers_db[old_sub] = _teacher(cover_count=1)
    distribution.daily_db.append(
        {
            "date": TARGET_DATE,
            "dept": DEPT,
            "المعلم الغائب": absent,
            "الصف": "تاسع 1",
            "الحصة": "1",
            "المعلم البديل": old_sub,
            "حالة_التكليف": "تقصير",
        }
    )

    distribution.rollback_auto_assignments_for_absentees_core([absent], DAY_NAME)

    assert _daily_rows_for(absent) == []
    assert distribution.teachers_db[old_sub]["cover_count"] == 1


def test_process_admin_action_core_returns_unchanged_for_empty_dataframe():
    current_abs = ["المعلم الغائب"]

    result = distribution.process_admin_action_core(
        pd.DataFrame(),
        "المعلم الغائب",
        "1",
        "بديل جديد",
        DAY_NAME,
        DEPT,
        True,
        current_abs,
        "normal",
    )

    assert result == {
        "refresh_dept": DEPT,
        "refresh_day": DAY_NAME,
        "refresh_is_admin": True,
        "refresh_current_abs": current_abs,
    }


def test_process_admin_action_core_rejects_invalid_new_sub_for_normal_action():
    absent = _absent_teacher()
    old_sub = "بديل قديم"
    distribution.teachers_db[old_sub] = _teacher(cover_count=1)
    distribution.daily_db.append(
        {
            "date": TARGET_DATE,
            "dept": DEPT,
            "المعلم الغائب": absent,
            "الصف": "تاسع 1",
            "الحصة": "1",
            "المعلم البديل": old_sub,
            "حالة_التكليف": "",
        }
    )

    result = distribution.process_admin_action_core(
        pd.DataFrame([{"row": 1}]),
        absent,
        "1",
        "⚠️ لا يوجد بديل",
        DAY_NAME,
        DEPT,
        True,
        [absent],
        "normal",
    )

    rows = _daily_rows_for(absent)
    assert rows[0]["المعلم البديل"] == old_sub
    assert distribution.teachers_db[old_sub]["cover_count"] == 1
    assert result["refresh_current_abs"] == [absent]


def test_process_admin_action_core_normal_action_switches_substitute_and_counts():
    absent = _absent_teacher()
    old_sub = "بديل قديم"
    new_sub = "بديل جديد"
    distribution.teachers_db[old_sub] = _teacher(cover_count=1)
    distribution.teachers_db[new_sub] = _teacher(cover_count=0)
    distribution.daily_db.append(
        {
            "date": TARGET_DATE,
            "dept": DEPT,
            "المعلم الغائب": absent,
            "الصف": "تاسع 1",
            "الحصة": "1",
            "المعلم البديل": old_sub,
            "حالة_التكليف": "",
        }
    )

    result = distribution.process_admin_action_core(
        pd.DataFrame([{"row": 1}]),
        absent,
        "1",
        new_sub,
        DAY_NAME,
        DEPT,
        True,
        [absent],
        "normal",
    )

    rows = _daily_rows_for(absent)
    assert len(rows) == 1
    assert rows[0]["المعلم البديل"] == new_sub
    assert rows[0]["حالة_التكليف"] == ""
    assert distribution.teachers_db[old_sub]["cover_count"] == 0
    assert distribution.teachers_db[new_sub]["cover_count"] == 1
    assert result["refresh_current_abs"] == [absent]


def test_rollback_auto_assignments_returns_none_and_leaves_state_unchanged_when_no_absentees():
    untouched_row = {
        "date": TARGET_DATE,
        "dept": DEPT,
        "المعلم الغائب": "معلم غير مستهدف",
        "الصف": "تاسع 1",
        "الحصة": "1",
        "المعلم البديل": "بديل غير مستهدف",
        "حالة_التكليف": "",
    }
    distribution.daily_db.append(dict(untouched_row))
    distribution.teachers_db["بديل غير مستهدف"] = _teacher(cover_count=3)

    result = distribution.rollback_auto_assignments_for_absentees_core([], DAY_NAME)

    assert result is None
    assert distribution.daily_db == [untouched_row]
    assert distribution.teachers_db["بديل غير مستهدف"]["cover_count"] == 3


def test_rollback_auto_assignments_keeps_unrelated_rows_and_counts():
    target_absent = "المعلم الغائب"
    unrelated_absent = "معلم غير مستهدف"
    target_sub = "بديل مستهدف"
    unrelated_sub = "بديل غير مستهدف"

    distribution.teachers_db[target_sub] = _teacher(cover_count=1)
    distribution.teachers_db[unrelated_sub] = _teacher(cover_count=7)

    target_row = {
        "date": TARGET_DATE,
        "dept": DEPT,
        "المعلم الغائب": target_absent,
        "الصف": "تاسع 1",
        "الحصة": "1",
        "المعلم البديل": target_sub,
        "حالة_التكليف": "",
    }
    unrelated_row = {
        "date": TARGET_DATE,
        "dept": DEPT,
        "المعلم الغائب": unrelated_absent,
        "الصف": "تاسع 2",
        "الحصة": "2",
        "المعلم البديل": unrelated_sub,
        "حالة_التكليف": "",
    }
    distribution.daily_db.extend([dict(target_row), dict(unrelated_row)])

    result = distribution.rollback_auto_assignments_for_absentees_core([target_absent], DAY_NAME)

    assert result is None
    assert target_row not in distribution.daily_db
    assert distribution.daily_db == [unrelated_row]
    assert distribution.teachers_db[target_sub]["cover_count"] == 0
    assert distribution.teachers_db[unrelated_sub]["cover_count"] == 7

