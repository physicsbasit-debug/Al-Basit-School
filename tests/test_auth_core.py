# -*- coding: utf-8 -*-
"""اختبارات أولية للـ auth cores بدون Gradio وبدون استيراد app.py."""

from __future__ import annotations

import json

import auth
import storage


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


def test_save_auth_account_profile_success_updates_account_and_writes_real_audit_log(tmp_path, monkeypatch):
    account_id = "account-profile-success"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. وليد",
                "display_name": "الاسم القديم",
                "official_title": "المسمى القديم",
                "welcome_title": "الترحيب القديم",
                "department_label": "القسم القديم",
                "welcome_phrase": "العبارة القديمة",
                "welcome_template": "القالب القديم",
                "whatsapp_title": "واتساب قديم",
                "enabled": True,
                "is_owner": False,
                "pin_hash": "hash-placeholder",
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    result = auth.save_auth_account_profile_core(
        account_id,
        "الاسم الجديد",
        "المسمى الجديد",
        "الترحيب الجديد",
        "القسم الجديد",
        "العبارة الجديدة",
        "القالب الجديد",
        "واتساب جديد",
        is_owner=True,
        actor_name="مالك الاختبار",
        actor_role="صاحب النظام",
    )

    assert result["ok"] is True
    assert result["mode"] == "success"
    assert result["account_id"] == account_id
    assert result["account"]["display_name"] == "الاسم الجديد"
    assert result["account"]["official_title"] == "المسمى الجديد"
    assert any(choice[1] == account_id for choice in result["choices"])

    with open(auth_accounts_file, "r", encoding="utf-8") as accounts_file:
        saved_payload = json.load(accounts_file)
    saved_account = saved_payload["accounts"][account_id]
    assert saved_account["display_name"] == "الاسم الجديد"
    assert saved_account["welcome_template"] == "القالب الجديد"
    assert saved_account["whatsapp_title"] == "واتساب جديد"
    assert saved_account.get("profile_updated_at")

    assert audit_log_file.exists()
    with open(audit_log_file, "r", encoding="utf-8") as audit_file:
        audit_records = json.load(audit_file)

    assert len(audit_records) == 1
    record = audit_records[0]
    assert record["action"] == "تعديل تخصيص حساب دخول"
    assert record["actor_name"] == "مالك الاختبار"
    assert record["actor_role"] == "صاحب النظام"
    assert record["target_teacher"] == ""
    assert "تعديل هيدر ومسمى حساب" in record["details"]
    assert record["old_value"]["display_name"] == "الاسم القديم"
    assert record["new_value"]["display_name"] == "الاسم الجديد"
    assert record["new_value"]["welcome_phrase"] == "العبارة الجديدة"
    assert record["source"]


def test_owner_toggle_account_status_success_disables_enabled_account_and_writes_real_audit_log(tmp_path, monkeypatch):
    account_id = "account-toggle-success"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. وليد",
                "display_name": "أ. وليد",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": "hash-placeholder",
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    result = auth.owner_toggle_account_status_core(
        account_id,
        is_owner=True,
        actor_name="مالك الاختبار",
        actor_role="صاحب النظام",
    )

    assert result["ok"] is True
    assert result["mode"] == "success"
    assert result["account_id"] == account_id
    assert result["enabled"] is False
    assert any(choice[1] == account_id and "معطل" in choice[0] for choice in result["choices"])
    assert "تم تعطيل الحساب بنجاح" in result["status_html"]

    with open(auth_accounts_file, "r", encoding="utf-8") as accounts_file:
        saved_payload = json.load(accounts_file)
    saved_account = saved_payload["accounts"][account_id]
    assert saved_account["enabled"] is False
    assert saved_account.get("updated_at")

    assert audit_log_file.exists()
    with open(audit_log_file, "r", encoding="utf-8") as audit_file:
        audit_records = json.load(audit_file)

    assert len(audit_records) == 1
    record = audit_records[0]
    assert record["action"] == "تعطيل حساب دخول"
    assert record["actor_name"] == "مالك الاختبار"
    assert record["actor_role"] == "صاحب النظام"
    assert record["target_teacher"] == ""
    assert record["old_value"] == "مفعل"
    assert record["new_value"] == "معطل"
    assert "تعطيل حساب دخول" in record["details"]
    assert "أ. وليد" in record["details"]
    assert record["source"]


def test_owner_reset_account_pin_success_sets_temporary_pin_and_writes_real_audit_log(tmp_path, monkeypatch):
    account_id = "account-reset-success"
    requested_pin = "654321"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. وليد",
                "display_name": "أ. وليد",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": auth._pin_hash("123456"),
                "must_change_pin": False,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    result = auth.owner_reset_account_pin_core(
        account_id,
        requested_pin,
        is_owner=True,
        actor_name="مالك الاختبار",
        actor_role="صاحب النظام",
    )

    assert result["ok"] is True
    assert result["mode"] == "success"
    assert result["account_id"] == account_id
    assert result["new_pin"] == requested_pin
    assert any(choice[1] == account_id for choice in result["choices"])
    assert "تمت إعادة التعيين" in result["status_html"]

    with open(auth_accounts_file, "r", encoding="utf-8") as accounts_file:
        saved_payload = json.load(accounts_file)
    saved_account = saved_payload["accounts"][account_id]

    assert saved_account["must_change_pin"] is True
    assert saved_account["pin_reset_at"]
    assert saved_account["pin_reset_by"] == "مالك الاختبار"
    assert saved_account["updated_at"] == saved_account["pin_reset_at"]
    assert auth._verify_pin_hash(requested_pin, saved_account["pin_hash"]) is True
    assert auth._verify_pin_hash("123456", saved_account["pin_hash"]) is False

    assert audit_log_file.exists()
    with open(audit_log_file, "r", encoding="utf-8") as audit_file:
        audit_records = json.load(audit_file)

    assert len(audit_records) == 1
    record = audit_records[0]
    assert record["action"] == "إعادة تعيين رمز دخول"
    assert record["actor_name"] == "مالك الاختبار"
    assert record["actor_role"] == "صاحب النظام"
    assert record["target_teacher"] == ""
    assert record["old_value"] == "رمز مشفر"
    assert record["new_value"] == "رمز مؤقت مشفر"
    assert "إعادة تعيين حساب" in record["details"]
    assert "أ. وليد" in record["details"]
    assert record["source"]


def test_change_own_account_pin_success_clears_must_change_and_writes_real_audit_log(tmp_path, monkeypatch):
    account_id = "account-change-pin-success"
    current_pin = "111111"
    new_pin = "222222"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    audit_log_file = tmp_path / "audit_log.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "AUDIT_LOG_FILE", str(audit_log_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. وليد",
                "display_name": "أ. وليد",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": auth._pin_hash(current_pin),
                "must_change_pin": True,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    result = auth.change_own_account_pin_core(
        account_id,
        current_pin,
        new_pin,
        new_pin,
        actor_name="أ. وليد",
        actor_role="مدير المدرسة",
        is_owner=False,
    )

    assert result["ok"] is True
    assert result["mode"] == "clear_inputs"
    assert "تم تغيير رمز الدخول بنجاح" in result["status_html"]
    assert set(result.keys()) == {"ok", "mode", "status_html"}

    with open(auth_accounts_file, "r", encoding="utf-8") as accounts_file:
        saved_payload = json.load(accounts_file)
    saved_account = saved_payload["accounts"][account_id]

    assert saved_account["must_change_pin"] is False
    assert saved_account["pin_changed_at"]
    assert saved_account["updated_at"] == saved_account["pin_changed_at"]
    assert auth._verify_pin_hash(new_pin, saved_account["pin_hash"]) is True
    assert auth._verify_pin_hash(current_pin, saved_account["pin_hash"]) is False

    assert audit_log_file.exists()
    with open(audit_log_file, "r", encoding="utf-8") as audit_file:
        audit_records = json.load(audit_file)

    assert len(audit_records) == 1
    record = audit_records[0]
    assert record["action"] == "تغيير رمز دخول"
    assert record["actor_name"] == "أ. وليد"
    assert record["actor_role"] == "مدير المدرسة"
    assert record["target_teacher"] == ""
    assert record["old_value"] == "رمز مشفر"
    assert record["new_value"] == "رمز مشفر"
    assert "غيّر المستخدم رمز حساب" in record["details"]
    assert "أ. وليد" in record["details"]
    assert record["source"]


def test_authenticate_login_pin_success_returns_regular_account_tuple(tmp_path, monkeypatch):
    account_id = "account-login-success"
    login_pin = "333333"
    auth_accounts_file = tmp_path / "auth_accounts.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)

    pin_hash = auth._pin_hash(login_pin)
    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. وليد",
                "display_name": "أ. وليد",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": pin_hash,
                "must_change_pin": False,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    returned_account_id, user_info, error_code = auth.authenticate_login_pin(login_pin)

    assert error_code == ""
    assert returned_account_id == account_id
    assert isinstance(user_info, dict)
    assert user_info["account_id"] == account_id
    assert user_info["is_owner"] is False
    assert user_info["enabled"] is True
    assert user_info["role"] == "مدير المدرسة"
    assert user_info["dept"] == "الكل"
    assert user_info["display_name"] == "أ. وليد"
    assert user_info["pin_hash"] == pin_hash
    assert "pin_hash" in user_info


def test_validate_new_pin_accepts_four_to_twelve_numeric_digits():
    assert auth._validate_new_pin("1234") == (True, "")
    assert auth._validate_new_pin("12345") == (True, "")
    assert auth._validate_new_pin("123456") == (True, "")
    assert auth._validate_new_pin("1234567") == (True, "")
    assert auth._validate_new_pin("123456789012") == (True, "")

    ok, message = auth._validate_new_pin("123")
    assert ok is False
    assert "4 أرقام" in message

    ok, message = auth._validate_new_pin("1234567890123")
    assert ok is False
    assert "12 رقم" in message

    ok, message = auth._validate_new_pin("12345a")
    assert ok is False
    assert "أرقام فقط" in message

    ok, message = auth._validate_new_pin("123 56")
    assert ok is False
    assert "أرقام فقط" in message

    ok, message = auth._validate_new_pin(" 123456")
    assert ok is False
    assert "مسافات" in message

    ok, message = auth._validate_new_pin("123456 ")
    assert ok is False
    assert "مسافات" in message



def test_authenticate_login_pin_keeps_existing_legacy_four_digit_account_valid(tmp_path, monkeypatch):
    account_id = "account-legacy-four-digit"
    legacy_pin = "1234"
    auth_accounts_file = tmp_path / "auth_accounts.json"

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)

    pin_hash = auth._pin_hash(legacy_pin)
    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. حساب قديم",
                "display_name": "أ. حساب قديم",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": pin_hash,
                "must_change_pin": False,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    returned_account_id, user_info, error_code = auth.authenticate_login_pin(legacy_pin)

    assert error_code == ""
    assert returned_account_id == account_id
    assert user_info["account_id"] == account_id
    assert user_info["pin_hash"] == pin_hash


def test_authenticate_login_pin_invalid_pin_delays_once(tmp_path, monkeypatch):
    account_id = "account-invalid-delay"
    valid_pin = "333333"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    sleep_calls = []

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. اختبار",
                "display_name": "أ. اختبار",
                "official_title": "مدير المدرسة",
                "enabled": True,
                "is_owner": False,
                "pin_hash": auth._pin_hash(valid_pin),
                "must_change_pin": False,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    returned_account_id, user_info, error_code = auth.authenticate_login_pin("999999")

    assert returned_account_id == ""
    assert user_info is None
    assert error_code == "invalid"
    assert sleep_calls == [0.5]


def test_authenticate_login_pin_disabled_account_does_not_delay(tmp_path, monkeypatch):
    account_id = "account-disabled-no-delay"
    disabled_pin = "444444"
    auth_accounts_file = tmp_path / "auth_accounts.json"
    sleep_calls = []

    monkeypatch.setattr(auth, "AUTH_ACCOUNTS_FILE", str(auth_accounts_file))
    monkeypatch.setattr(storage, "BACKUPS_DIR", str(tmp_path / "backups"))
    monkeypatch.delenv("SYSTEM_OWNER_PIN", raising=False)
    monkeypatch.setattr(auth.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    initial_payload = {
        "version": auth.AUTH_ACCOUNTS_VERSION,
        "accounts": {
            account_id: {
                "account_id": account_id,
                "role": "مدير المدرسة",
                "dept": "الكل",
                "name": "أ. حساب معطل",
                "display_name": "أ. حساب معطل",
                "official_title": "مدير المدرسة",
                "enabled": False,
                "is_owner": False,
                "pin_hash": auth._pin_hash(disabled_pin),
                "must_change_pin": False,
            }
        },
    }
    assert auth.save_auth_accounts(initial_payload) is True

    returned_account_id, user_info, error_code = auth.authenticate_login_pin(disabled_pin)

    assert returned_account_id == account_id
    assert user_info is None
    assert error_code == "disabled"
    assert sleep_calls == []

