# -*- coding: utf-8 -*-
"""اختبارات أولية للـ auth cores بدون Gradio وبدون استيراد app.py."""

from __future__ import annotations

import auth


def test_change_own_account_pin_rejects_owner_secret_flow():
    result = auth.change_own_account_pin_core(
        "__owner_secret__",
        "old-pin",
        "new-pin",
        "new-pin",
        is_owner=True,
    )
    assert result["ok"] is False
    assert "مالك النظام" in result["status_html"]


def test_change_own_account_pin_rejects_missing_account(monkeypatch):
    monkeypatch.setattr(auth, "load_auth_accounts", lambda: {"accounts": {}})
    result = auth.change_own_account_pin_core(
        "missing-account",
        "1234",
        "5678",
        "5678",
        is_owner=False,
    )
    assert result["ok"] is False
    assert result["mode"] == "clear_inputs"


def test_owner_reset_account_pin_rejects_non_owner():
    result = auth.owner_reset_account_pin_core(
        "account-1",
        "123456",
        is_owner=False,
    )
    assert result["ok"] is False
    assert result["new_pin"] == ""


def test_owner_toggle_account_status_rejects_non_owner():
    result = auth.owner_toggle_account_status_core(
        "account-1",
        is_owner=False,
    )
    assert result["ok"] is False
    assert result["mode"] == "error"


def test_save_auth_account_profile_rejects_non_owner():
    result = auth.save_auth_account_profile_core(
        "account-1",
        "اسم",
        "منصب",
        "ترحيب",
        "قسم",
        "عبارة",
        "قالب",
        "واتساب",
        is_owner=False,
    )
    assert result["ok"] is False
    assert "مالك النظام" in result["status_html"]
