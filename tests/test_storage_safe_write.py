# -*- coding: utf-8 -*-
"""اختبارات safe_write_json الأساسية."""

from __future__ import annotations

import json

import storage


def test_safe_write_json_writes_readable_dict(tmp_path):
    target = tmp_path / "sample.json"
    assert storage.safe_write_json(target, {"a": 1, "b": "نص"}, make_backup=False) is True
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "b": "نص"}


def test_safe_write_json_overwrites_with_valid_json(tmp_path):
    target = tmp_path / "sample.json"
    assert storage.safe_write_json(target, {"old": True}, make_backup=False) is True
    assert storage.safe_write_json(target, {"new": [1, 2, 3]}, make_backup=False) is True
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"new": [1, 2, 3]}


def test_safe_write_json_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "sample.json"
    assert storage.safe_write_json(target, {"ok": True}, make_backup=False) is True
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["ok"] is True
