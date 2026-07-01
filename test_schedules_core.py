# -*- coding: utf-8 -*-
"""اختبارات خفيفة لدوال الجداول النظيفة في schedules.py."""

from __future__ import annotations

from schedules import clean_teacher_name, get_name_fingerprint


def test_clean_teacher_name_strips_edges_and_collapses_spaces():
    assert clean_teacher_name("  أحمد   سالم  ") == "أحمد سالم"


def test_clean_teacher_name_handles_empty_string():
    assert clean_teacher_name("") == ""


def test_get_name_fingerprint_matches_names_with_extra_spaces_and_bin():
    assert get_name_fingerprint("  أحمد   بن   سعيد  ") == get_name_fingerprint("أحمد سعيد")


def test_get_name_fingerprint_not_empty_for_valid_name():
    first_word, words = get_name_fingerprint("وليد الهنائي")
    assert first_word
    assert words
