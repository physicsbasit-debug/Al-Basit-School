# -*- coding: utf-8 -*-
"""اختبارات دوال الإعفاءات الخالصة."""

from __future__ import annotations

import exemptions


def test_normalize_exempt_slots_filters_and_normalizes():
    slots = exemptions.normalize_exempt_slots([
        {"day": "الأحد", "period": "2"},
        "الإثنين ح3",
        {"day": "الجمعة", "period": 1},
        {"day": "الأحد", "period": "2"},
        {"day": "الثلاثاء", "period": "99"},
    ])
    assert slots == [
        {"day": "الأحد", "period": 2},
        {"day": "الإثنين", "period": 3},
    ]


def test_build_exempt_slots_from_days_periods_cross_product():
    slots = exemptions.build_exempt_slots_from_days_periods(
        ["الأحد", "الخميس", "الجمعة"],
        [1, "2", 99, "bad"],
    )
    assert slots == [
        {"day": "الأحد", "period": 1},
        {"day": "الأحد", "period": 2},
        {"day": "الخميس", "period": 1},
        {"day": "الخميس", "period": 2},
    ]


def test_is_teacher_exempt_for_slot_true_for_specific_slot():
    name = "معلم اختبار إعفاء"
    original = dict(exemptions.teachers_db)
    try:
        exemptions.teachers_db.clear()
        exemptions.teachers_db[name] = {
            "exempt_days": [],
            "exempt_periods": [],
            "exempt_slots": [{"day": "الأحد", "period": 2}],
        }
        assert exemptions.is_teacher_exempt_for_slot(name, "الأحد", 2) is True
    finally:
        exemptions.teachers_db.clear()
        exemptions.teachers_db.update(original)


def test_is_teacher_exempt_for_slot_false_when_no_match():
    name = "معلم اختبار غير معفى"
    original = dict(exemptions.teachers_db)
    try:
        exemptions.teachers_db.clear()
        exemptions.teachers_db[name] = {
            "exempt_days": [],
            "exempt_periods": [],
            "exempt_slots": [{"day": "الأحد", "period": 2}],
        }
        assert exemptions.is_teacher_exempt_for_slot(name, "الإثنين", 2) is False
    finally:
        exemptions.teachers_db.clear()
        exemptions.teachers_db.update(original)
