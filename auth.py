# -*- coding: utf-8 -*-
"""
auth.py
طبقة الحسابات والصلاحيات الأساسية لمنظومة مسار.

Phase 3D:
- نقل دوال الدخول والحسابات المشفرة والصلاحيات المركزية خارج app.py.
- لا يحتوي هذا الملف أي ربط Gradio أو مكونات واجهة.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import os
import secrets

from storage import (
    AUTH_DB_FILE,
    AUTH_ACCOUNTS_FILE,
    safe_write_json,
)

# v1.3 — أدوار وصلاحيات مركزية
OWNER_ROLE = "صاحب النظام"
SHARED_TEACHER_ROLE = "مستخدم عام"
ADMIN_ACCESS_ROLES = ["مدير المدرسة", "المدير المساعد"]
DEPT_LEADER_ROLES = ["معلم أول", "منسق مادة"]

# v1.8.2 — حسابات دخول مشفرة قابلة للتغيير وإعادة التعيين
AUTH_ACCOUNTS_VERSION = 1
PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 210_000
OWNER_ACCOUNT_ID = "__owner_secret__"

_TZ_OMAN = datetime.timezone(datetime.timedelta(hours=4))


def _auth_now_text():
    return datetime.datetime.now(_TZ_OMAN).strftime("%Y-%m-%d %H:%M:%S")


def load_auth_db():
    auth_map = {}

    auth_json = os.getenv("AUTH_DB_JSON", "").strip()
    if auth_json:
        try:
            loaded = json.loads(auth_json)
            if isinstance(loaded, dict):
                auth_map.update(loaded)
        except Exception as e:
            print(f"AUTH_DB_JSON parse error: {e}")

    if not auth_map and os.path.exists(AUTH_DB_FILE):
        try:
            with open(AUTH_DB_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                auth_map.update(loaded)
        except Exception as e:
            print(f"AUTH_DB file load error: {e}")

    owner_pin = os.getenv("SYSTEM_OWNER_PIN", "").strip()
    owner_name = os.getenv("SYSTEM_OWNER_NAME", "صاحب النظام").strip() or "صاحب النظام"
    if owner_pin:
        auth_map[owner_pin] = {
            "role": OWNER_ROLE,
            "dept": "الكل",
            "name": owner_name,
            "is_owner": True,
        }

    return auth_map


AUTH_DB = load_auth_db()


def _pin_hash(pin_value, *, salt_hex=None, iterations=PIN_HASH_ITERATIONS):
    pin_text = str(pin_value or "")
    if salt_hex:
        salt = bytes.fromhex(str(salt_hex))
    else:
        salt = secrets.token_bytes(16)

    derived = hashlib.pbkdf2_hmac(
        "sha256",
        pin_text.encode("utf-8"),
        salt,
        int(iterations),
    )
    return (
        f"{PIN_HASH_ALGORITHM}${int(iterations)}$"
        f"{salt.hex()}${derived.hex()}"
    )


def _verify_pin_hash(pin_value, stored_hash):
    try:
        algorithm, iterations, salt_hex, expected_hex = str(stored_hash).split("$", 3)
        if algorithm != PIN_HASH_ALGORITHM:
            return False
        calculated = _pin_hash(
            pin_value,
            salt_hex=salt_hex,
            iterations=int(iterations),
        )
        calculated_hex = calculated.rsplit("$", 1)[-1]
        return hmac.compare_digest(calculated_hex, expected_hex)
    except Exception:
        return False


def _account_display_name(record):
    name = str(record.get("name", "")).strip()
    role = str(record.get("role", "")).strip()
    dept = str(record.get("dept", "")).strip()

    if role == SHARED_TEACHER_ROLE:
        return name or "الدخول العام"
    if name:
        return name
    if dept and dept not in {"الكل", "المعلمون"}:
        return f"{role} — {dept}"
    return role or "حساب غير مسمى"


def _make_legacy_account_id(record, index):
    raw = "|".join([
        str(record.get("role", "")),
        str(record.get("dept", "")),
        str(record.get("name", "")),
        str(index),
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"account_{digest}"


def _empty_auth_accounts_payload():
    return {
        "version": AUTH_ACCOUNTS_VERSION,
        "updated_at": _auth_now_text(),
        "accounts": {},
    }


def load_auth_accounts():
    if not os.path.exists(AUTH_ACCOUNTS_FILE):
        return _empty_auth_accounts_payload()

    try:
        with open(AUTH_ACCOUNTS_FILE, "r", encoding="utf-8") as auth_file:
            payload = json.load(auth_file)
        if not isinstance(payload, dict):
            return _empty_auth_accounts_payload()
        if not isinstance(payload.get("accounts"), dict):
            payload["accounts"] = {}
        payload.setdefault("version", AUTH_ACCOUNTS_VERSION)
        return payload
    except Exception as exc:
        print(f"load_auth_accounts error: {exc}")
        return _empty_auth_accounts_payload()


def save_auth_accounts(payload):
    clean_payload = dict(payload or {})
    clean_payload["version"] = AUTH_ACCOUNTS_VERSION
    clean_payload["updated_at"] = _auth_now_text()
    clean_payload.setdefault("accounts", {})
    return safe_write_json(AUTH_ACCOUNTS_FILE, clean_payload)


def initialize_auth_accounts():
    """
    ترحيل رموز الدخول القديمة مرة واحدة إلى Hash مشفر.
    رمز مالك النظام مستثنى ويبقى داخل Secret.
    """
    if os.path.exists(AUTH_ACCOUNTS_FILE):
        return load_auth_accounts()

    payload = _empty_auth_accounts_payload()
    accounts = payload["accounts"]

    migrated_index = 0
    for legacy_pin, legacy_info in AUTH_DB.items():
        if not isinstance(legacy_info, dict):
            continue

        role = str(legacy_info.get("role", "")).strip()
        is_owner = bool(
            legacy_info.get("is_owner", False)
            or role == OWNER_ROLE
        )
        if is_owner:
            continue

        pin_text = str(legacy_pin or "").strip()
        if not pin_text:
            continue

        account_record = {
            "role": role,
            "dept": str(legacy_info.get("dept", "الكل")).strip() or "الكل",
            "name": str(legacy_info.get("name", "")).strip(),
            "display_name": str(legacy_info.get("display_name", legacy_info.get("name", ""))).strip(),
            "official_title": str(legacy_info.get("official_title", role)).strip(),
            "welcome_title": str(legacy_info.get("welcome_title", "")).strip(),
            "department_label": str(legacy_info.get("department_label", "")).strip(),
            "welcome_phrase": str(legacy_info.get("welcome_phrase", "")).strip(),
            "welcome_template": str(legacy_info.get("welcome_template", "")).strip(),
            "whatsapp_title": str(legacy_info.get("whatsapp_title", role)).strip(),
            "is_owner": False,
            "enabled": True,
            "pin_hash": _pin_hash(pin_text),
            "must_change_pin": False,
            "created_at": _auth_now_text(),
            "updated_at": _auth_now_text(),
            "migration_source": "legacy_auth",
        }

        account_id = _make_legacy_account_id(
            account_record,
            migrated_index,
        )
        while account_id in accounts:
            migrated_index += 1
            account_id = _make_legacy_account_id(
                account_record,
                migrated_index,
            )

        account_record["account_id"] = account_id
        accounts[account_id] = account_record
        migrated_index += 1

    safe_write_json(
        AUTH_ACCOUNTS_FILE,
        payload,
        make_backup=False,
    )
    return payload


AUTH_ACCOUNTS = initialize_auth_accounts()


def _owner_login_record(pin_value):
    owner_pin = os.getenv("SYSTEM_OWNER_PIN", "").strip()
    if not owner_pin:
        return None

    if not hmac.compare_digest(
        str(pin_value or "").strip(),
        owner_pin,
    ):
        return None

    owner_name = (
        os.getenv("SYSTEM_OWNER_NAME", "صاحب النظام").strip()
        or "صاحب النظام"
    )
    return {
        "account_id": OWNER_ACCOUNT_ID,
        "role": OWNER_ROLE,
        "dept": "الكل",
        "name": owner_name,
        "is_owner": True,
        "enabled": True,
        "must_change_pin": False,
    }


def authenticate_login_pin(pin_value):
    """
    إرجاع: account_id, user_info, error_code
    لا يعتمد على رموز AUTH_DB القديمة بعد إنشاء الملف المشفر.
    """
    pin_text = str(pin_value or "").strip()
    if not pin_text:
        return "", None, "invalid"

    owner_record = _owner_login_record(pin_text)
    if owner_record:
        return OWNER_ACCOUNT_ID, owner_record, ""

    payload = load_auth_accounts()
    for account_id, record in payload.get("accounts", {}).items():
        if not isinstance(record, dict):
            continue
        if not _verify_pin_hash(pin_text, record.get("pin_hash", "")):
            continue

        if not bool(record.get("enabled", True)):
            return str(account_id), None, "disabled"

        user_info = dict(record)
        user_info["account_id"] = str(account_id)
        user_info["is_owner"] = False
        return str(account_id), user_info, ""

    return "", None, "invalid"


def _validate_new_pin(pin_value):
    pin_text = str(pin_value or "")
    if pin_text != pin_text.strip():
        return False, "لا تسمح رموز الدخول بمسافات في البداية أو النهاية."
    if len(pin_text) < 4:
        return False, "يجب ألا يقل رمز الدخول عن 4 خانات."
    if len(pin_text) > 20:
        return False, "يجب ألا يزيد رمز الدخول عن 20 خانة."
    if any(char.isspace() for char in pin_text):
        return False, "لا يسمح بوجود مسافات داخل رمز الدخول."
    return True, ""


def _pin_is_used_by_another_account(pin_value, exclude_account_id=""):
    pin_text = str(pin_value or "").strip()

    owner_pin = os.getenv("SYSTEM_OWNER_PIN", "").strip()
    if owner_pin and hmac.compare_digest(pin_text, owner_pin):
        return True

    payload = load_auth_accounts()
    for account_id, record in payload.get("accounts", {}).items():
        if str(account_id) == str(exclude_account_id):
            continue
        if _verify_pin_hash(pin_text, record.get("pin_hash", "")):
            return True
    return False


def get_auth_account_choices():
    payload = load_auth_accounts()
    choices = []
    for account_id, record in payload.get("accounts", {}).items():
        status = "مفعل" if bool(record.get("enabled", True)) else "معطل"
        label = (
            f"{_account_display_name(record)} | "
            f"{record.get('role', '—')} | {status}"
        )
        choices.append((label, str(account_id)))
    choices.sort(key=lambda item: item[0])
    return choices


def get_permissions(role="", is_owner=False, dept_value="", is_admin_flag=None):
    """
    v1.3 — مركز صلاحيات منظومة مسار.
    هذه الدالة هي المرجع الأساسي لظهور الأقسام وصلاحيات التعديل.
    """
    role_clean = str(role or "").strip()
    dept_clean = str(dept_value or "").strip()

    owner_mode = bool(is_owner) or role_clean == OWNER_ROLE
    shared_teacher_mode = bool(role_clean == SHARED_TEACHER_ROLE or dept_clean == "المعلمون")

    if is_admin_flag is None:
        admin_mode = bool(owner_mode or role_clean in ADMIN_ACCESS_ROLES)
    else:
        admin_mode = bool(is_admin_flag)

    # لا يُعامل الدخول العام كإدارة مهما كانت قيمة القسم بعد التحويل.
    if shared_teacher_mode:
        admin_mode = False

    dept_leader_mode = bool(
        not owner_mode
        and not admin_mode
        and not shared_teacher_mode
    )

    can_manage_exemptions = bool((owner_mode or admin_mode) and not shared_teacher_mode)
    can_view_distribution = bool(not shared_teacher_mode)
    can_view_balances = bool(not shared_teacher_mode)
    can_view_swap = True
    can_view_day_table = True
    can_view_teacher_table = True
    can_access_school_data = bool(owner_mode)
    can_use_swap_excel = bool(not shared_teacher_mode)
    can_edit_vault_basic = bool((owner_mode or admin_mode) and not shared_teacher_mode)
    can_edit_sensitive_teacher_data = bool(owner_mode)
    can_clear_system = bool(owner_mode)
    can_close_month = bool((owner_mode or admin_mode) and not shared_teacher_mode)
    can_manage_school_data = bool(owner_mode)
    can_add_manual_staff = bool(owner_mode)
    can_delete_teacher = bool(owner_mode)

    return {
        "is_owner": owner_mode,
        "is_admin": bool(owner_mode or admin_mode),
        "is_shared_teacher": shared_teacher_mode,
        "is_dept_leader": dept_leader_mode,

        "controls_row": not shared_teacher_mode,
        "distribution_tab": can_view_distribution,
        "balances_tab": can_view_balances,
        "exemptions_tab": can_manage_exemptions,
        "swap_tab": can_view_swap,
        "day_tab": can_view_day_table,
        "teacher_tab": can_view_teacher_table,
        "school_data_tab": can_access_school_data,
        "swap_excel_btn": can_use_swap_excel,

        "can_view_distribution": can_view_distribution,
        "can_view_balances": can_view_balances,
        "can_manage_exemptions": can_manage_exemptions,
        "can_view_swap": can_view_swap,
        "can_view_day_table": can_view_day_table,
        "can_view_teacher_table": can_view_teacher_table,
        "can_access_school_data": can_access_school_data,
        "can_use_swap_excel": can_use_swap_excel,
        "can_edit_vault_basic": can_edit_vault_basic,
        "can_edit_sensitive_teacher_data": can_edit_sensitive_teacher_data,
        "can_clear_system": can_clear_system,
        "can_close_month": can_close_month,
        "can_manage_school_data": can_manage_school_data,
        "can_add_manual_staff": can_add_manual_staff,
        "can_delete_teacher": can_delete_teacher,
    }


def get_permissions_from_flags(is_admin=False, is_owner=False):
    """تحويل الحالات القديمة is_admin/is_owner إلى صلاحيات مركزية دون تغيير ربط الأحداث."""
    if bool(is_owner):
        return get_permissions(OWNER_ROLE, True)
    if bool(is_admin):
        return get_permissions("مدير المدرسة", False)
    return get_permissions("معلم", False)


def get_ui_visibility_updates(pin, role, is_owner):
    # pin موجود للتوافق مع الربط القديم، والصلاحية تُبنى من الدور والمالك.
    return get_permissions(role=role, is_owner=is_owner)
