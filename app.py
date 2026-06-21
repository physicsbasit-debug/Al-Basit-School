import os
import gradio as gr
import shutil
import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import datetime
import matplotlib
import random
import json
import re
import urllib.parse
import tempfile
import threading
import functools
import zipfile
import html as html_lib
import base64
import mimetypes
import hashlib
import hmac
import secrets
matplotlib.use('Agg')  
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont


# --- 1. الإعدادات والوقت ---
tz_oman = datetime.timezone(datetime.timedelta(hours=4))
APP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 12

# v1.8.1 — الاستمرارية والتخزين الدائم
LOCAL_DATA_DIR = os.path.join(APP_DIR, "data")
REQUESTED_PERSISTENT_DATA_DIR = os.getenv("MASAR_DATA_DIR", "/data/masar").strip() or "/data/masar"

def _probe_writable_directory(path_value):
    """التحقق من أن مسار التخزين موجود وقابل للكتابة."""
    try:
        target = os.path.abspath(str(path_value))
        os.makedirs(target, exist_ok=True)
        probe_path = os.path.join(target, f".masar_write_probe_{os.getpid()}")
        with open(probe_path, "w", encoding="utf-8") as probe_file:
            probe_file.write("ok")
            probe_file.flush()
            os.fsync(probe_file.fileno())
        os.remove(probe_path)
        return True, target, ""
    except Exception as exc:
        return False, os.path.abspath(str(path_value)), str(exc)

_persistent_ok, _persistent_path, _persistent_error = _probe_writable_directory(
    REQUESTED_PERSISTENT_DATA_DIR
)

if _persistent_ok:
    DATA_DIR = _persistent_path
    PERSISTENT_STORAGE_ACTIVE = True
    STORAGE_MODE = "persistent"
    STORAGE_ERROR = ""
else:
    os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
    DATA_DIR = os.path.abspath(LOCAL_DATA_DIR)
    PERSISTENT_STORAGE_ACTIVE = False
    STORAGE_MODE = "local_fallback"
    STORAGE_ERROR = _persistent_error

DB_FILE = os.path.join(DATA_DIR, "school_balances.json")
DAILY_DB_FILE = os.path.join(DATA_DIR, "daily_assignments.json")
SWAP_DB_FILE = os.path.join(DATA_DIR, "friendly_swaps.json")
AUTH_DB_FILE = os.getenv("AUTH_DB_FILE", os.path.join(DATA_DIR, "auth_db.json"))

IMG_DIR = os.path.join(DATA_DIR, "generated_images")
SWAP_IMG_DIR = os.path.join(DATA_DIR, "generated_swap_tables")
SCHEDULES_DIR = os.path.join(DATA_DIR, "schedules")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BRANDING_DIR = os.path.join(DATA_DIR, "branding")
REFERENCE_STATUS_FILE = os.path.join(DATA_DIR, "reference_files_status.json")
AUTH_ACCOUNTS_FILE = os.path.join(DATA_DIR, "auth_accounts.json")
MIGRATION_STATUS_FILE = os.path.join(DATA_DIR, ".v1_8_1_migration.json")
MAX_BACKUPS_PER_FILE = 10

# v1.6 — أقفال داخلية لحماية الحالة وملفات JSON عند تعدد المستخدمين
STATE_LOCK = threading.RLock()
_JSON_LOCKS_GUARD = threading.RLock()
_JSON_FILE_LOCKS = {}

def _get_json_file_lock(file_path):
    key = os.path.abspath(str(file_path))
    with _JSON_LOCKS_GUARD:
        lock = _JSON_FILE_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _JSON_FILE_LOCKS[key] = lock
        return lock

def state_locked(func):
    """تنفيذ الدوال المعدلة للحالة العامة بصورة متسلسلة داخل العملية الحالية."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with STATE_LOCK:
            return func(*args, **kwargs)
    return wrapper

ADMIN_FILE = os.path.join(DATA_DIR, "admin_staff.xlsx")
PHONES_FILE = os.path.join(DATA_DIR, "teacher_phones.xlsx")
EXEMPTIONS_LOG_FILE = os.path.join(DATA_DIR, "exemptions_log.json")
AUDIT_LOG_FILE = os.path.join(DATA_DIR, "audit_log.json")
SCHOOL_CONFIG_FILE = os.path.join(DATA_DIR, "school_config.json")

SCHEDULE_FILES = {
    "التربية الإسلامية": os.path.join(SCHEDULES_DIR, "التربية_الإسلامية.xlsx"),
    "اللغة العربية": os.path.join(SCHEDULES_DIR, "اللغة_العربية.xlsx"),
    "الرياضيات": os.path.join(SCHEDULES_DIR, "الرياضيات.xlsx"),
    "العلوم": os.path.join(SCHEDULES_DIR, "العلوم.xlsx"),
    "اللغة الإنجليزية": os.path.join(SCHEDULES_DIR, "اللغة_الإنجليزية.xlsx"),
    "الدراسات الإجتماعية": os.path.join(SCHEDULES_DIR, "الدراسات_الاجتماعية.xlsx"),
    "المهارات الفردية": os.path.join(SCHEDULES_DIR, "المهارات_الفردية.xlsx"),
}
def ensure_data_directories():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(IMG_DIR, exist_ok=True)
    os.makedirs(SWAP_IMG_DIR, exist_ok=True)
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    os.makedirs(EXPORTS_DIR, exist_ok=True)
    os.makedirs(BRANDING_DIR, exist_ok=True)


def _safe_storage_backup_name(file_path):
    base = os.path.basename(str(file_path))
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".json"
    timestamp = get_now_oman().strftime("%Y%m%d_%H%M%S_%f")
    return os.path.join(BACKUPS_DIR, f"{stem}_{timestamp}{ext}")

def _prune_old_backups(file_path, max_keep=MAX_BACKUPS_PER_FILE):
    try:
        ensure_data_directories()
        base = os.path.basename(str(file_path))
        stem, ext = os.path.splitext(base)
        if not ext:
            ext = ".json"

        pattern_prefix = f"{stem}_"
        candidates = []
        for name in os.listdir(BACKUPS_DIR):
            if name.startswith(pattern_prefix) and name.endswith(ext):
                full_path = os.path.join(BACKUPS_DIR, name)
                if os.path.isfile(full_path):
                    candidates.append(full_path)

        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        for old_backup in candidates[int(max_keep):]:
            try:
                os.remove(old_backup)
            except Exception:
                pass
    except Exception as e:
        print(f"_prune_old_backups error for {file_path}: {e}")

def safe_write_json(file_path, data, *, make_backup=True):
    """
    v1.6 — حفظ JSON آمن ومتزامن:
    1) قفل خاص بكل ملف.
    2) نسخة احتياطية من الملف القديم.
    3) ملف مؤقت فريد داخل المجلد نفسه.
    4) التحقق من JSON.
    5) استبدال ذري باستخدام os.replace.
    """
    target_path = os.path.abspath(str(file_path))
    target_dir = os.path.dirname(target_path) or os.getcwd()
    temp_path = None
    lock = _get_json_file_lock(target_path)

    with lock:
        try:
            ensure_data_directories()
            os.makedirs(target_dir, exist_ok=True)

            if make_backup and os.path.exists(target_path):
                try:
                    backup_path = _safe_storage_backup_name(target_path)
                    shutil.copy2(target_path, backup_path)
                    _prune_old_backups(target_path)
                except Exception as backup_error:
                    print(f"safe_write_json backup warning for {target_path}: {backup_error}")

            base_name = os.path.basename(target_path)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target_dir,
                prefix=f".{base_name}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = temp_file.name
                json.dump(data, temp_file, ensure_ascii=False, indent=2)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            with open(temp_path, "r", encoding="utf-8") as check_file:
                json.load(check_file)

            os.replace(temp_path, target_path)
            temp_path = None

            try:
                dir_fd = os.open(target_dir, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                pass

            return True

        except Exception as e:
            print(f"safe_write_json error for {file_path}: {e}")
            if temp_path:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass
            return False



# ─────────────────────────────────────────────────────────────────────────────
# v1.8.1 — ترحيل البيانات القديمة وحالة التخزين الدائم
# ─────────────────────────────────────────────────────────────────────────────

LEGACY_ROOT_FILES = {
    os.path.join(APP_DIR, "school_balances.json"): DB_FILE,
    os.path.join(APP_DIR, "daily_assignments.json"): DAILY_DB_FILE,
    os.path.join(APP_DIR, "friendly_swaps.json"): SWAP_DB_FILE,
    os.path.join(APP_DIR, "auth_db.json"): AUTH_DB_FILE,
}

LEGACY_DATA_FILES = {
    os.path.join(LOCAL_DATA_DIR, "admin_staff.xlsx"): ADMIN_FILE,
    os.path.join(LOCAL_DATA_DIR, "teacher_phones.xlsx"): PHONES_FILE,
    os.path.join(LOCAL_DATA_DIR, "exemptions_log.json"): EXEMPTIONS_LOG_FILE,
    os.path.join(LOCAL_DATA_DIR, "audit_log.json"): AUDIT_LOG_FILE,
    os.path.join(LOCAL_DATA_DIR, "school_config.json"): SCHOOL_CONFIG_FILE,
    os.path.join(LOCAL_DATA_DIR, "reference_files_status.json"): REFERENCE_STATUS_FILE,
    os.path.join(LOCAL_DATA_DIR, "auth_accounts.json"): AUTH_ACCOUNTS_FILE,
}

LEGACY_DATA_DIRECTORIES = {
    os.path.join(LOCAL_DATA_DIR, "schedules"): SCHEDULES_DIR,
    os.path.join(LOCAL_DATA_DIR, "backups"): BACKUPS_DIR,
    os.path.join(LOCAL_DATA_DIR, "branding"): BRANDING_DIR,
    os.path.join(LOCAL_DATA_DIR, "exports"): EXPORTS_DIR,
    os.path.join(LOCAL_DATA_DIR, "generated_images"): IMG_DIR,
    os.path.join(APP_DIR, "generated_swap_tables"): SWAP_IMG_DIR,
}

def _atomic_copy_file_if_missing(source_path, destination_path):
    """نسخ الملف القديم فقط إذا لم يوجد في التخزين الدائم."""
    source = os.path.abspath(str(source_path))
    destination = os.path.abspath(str(destination_path))

    if source == destination:
        return "same"
    if not os.path.isfile(source):
        return "missing"
    if os.path.exists(destination):
        return "skipped_existing"

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    temp_destination = f"{destination}.migration_{os.getpid()}.tmp"
    try:
        shutil.copy2(source, temp_destination)
        os.replace(temp_destination, destination)
        return "copied"
    finally:
        try:
            if os.path.exists(temp_destination):
                os.remove(temp_destination)
        except Exception:
            pass

def _copy_directory_missing_only(source_dir, destination_dir):
    copied = 0
    skipped = 0
    errors = []

    source = os.path.abspath(str(source_dir))
    destination = os.path.abspath(str(destination_dir))

    if source == destination or not os.path.isdir(source):
        return copied, skipped, errors

    for root, _dirs, files in os.walk(source):
        rel = os.path.relpath(root, source)
        target_root = destination if rel == "." else os.path.join(destination, rel)
        os.makedirs(target_root, exist_ok=True)

        for filename in files:
            source_file = os.path.join(root, filename)
            target_file = os.path.join(target_root, filename)
            try:
                result = _atomic_copy_file_if_missing(source_file, target_file)
                if result == "copied":
                    copied += 1
                elif result == "skipped_existing":
                    skipped += 1
            except Exception as exc:
                errors.append(f"{source_file}: {exc}")

    return copied, skipped, errors

def migrate_legacy_data_once():
    """ترحيل البيانات السابقة مرة واحدة دون استبدال بيانات الـBucket."""
    ensure_data_directories()
    report = {
        "version": "1.8.1",
        "created_at": datetime.datetime.now(tz_oman).strftime("%Y-%m-%d %H:%M:%S"),
        "persistent_storage_active": bool(PERSISTENT_STORAGE_ACTIVE),
        "data_dir": DATA_DIR,
        "copied_files": [],
        "skipped_existing": [],
        "missing_sources": [],
        "errors": [],
    }

    if not PERSISTENT_STORAGE_ACTIVE:
        report["note"] = "يعمل التطبيق على التخزين المحلي الاحتياطي."
        return report

    if os.path.exists(MIGRATION_STATUS_FILE):
        try:
            with open(MIGRATION_STATUS_FILE, "r", encoding="utf-8") as status_file:
                previous = json.load(status_file)
            if isinstance(previous, dict):
                previous["already_completed"] = True
                return previous
        except Exception:
            pass

    mappings = {}
    mappings.update(LEGACY_ROOT_FILES)
    mappings.update(LEGACY_DATA_FILES)

    for source_path, destination_path in mappings.items():
        try:
            result = _atomic_copy_file_if_missing(source_path, destination_path)
            if result == "copied":
                report["copied_files"].append(destination_path)
            elif result == "skipped_existing":
                report["skipped_existing"].append(destination_path)
            elif result == "missing":
                report["missing_sources"].append(source_path)
        except Exception as exc:
            report["errors"].append(f"{source_path}: {exc}")

    for source_dir, destination_dir in LEGACY_DATA_DIRECTORIES.items():
        try:
            copied, skipped, errors = _copy_directory_missing_only(
                source_dir, destination_dir
            )
            if copied:
                report["copied_files"].append(f"{destination_dir} ({copied} ملف)")
            if skipped:
                report["skipped_existing"].append(
                    f"{destination_dir} ({skipped} ملف موجود)"
                )
            report["errors"].extend(errors)
        except Exception as exc:
            report["errors"].append(f"{source_dir}: {exc}")

    safe_write_json(MIGRATION_STATUS_FILE, report, make_backup=False)
    return report

MIGRATION_REPORT = migrate_legacy_data_once()

def get_persistent_storage_health():
    ensure_data_directories()
    writable = False
    error = ""
    probe_path = os.path.join(DATA_DIR, f".health_probe_{os.getpid()}")

    try:
        with open(probe_path, "w", encoding="utf-8") as probe:
            probe.write("ok")
            probe.flush()
            os.fsync(probe.fileno())
        writable = True
    except Exception as exc:
        error = str(exc)
    finally:
        try:
            if os.path.exists(probe_path):
                os.remove(probe_path)
        except Exception:
            pass

    return {
        "persistent": bool(PERSISTENT_STORAGE_ACTIVE),
        "writable": bool(writable),
        "path": DATA_DIR,
        "mode": STORAGE_MODE,
        "error": error or STORAGE_ERROR,
        "migration": MIGRATION_REPORT,
    }

def render_persistent_storage_status_html():
    health = get_persistent_storage_health()

    if health["persistent"] and health["writable"]:
        title = "التخزين الدائم متصل ويعمل"
        bg, fg, border, icon = "#dcfce7", "#166534", "#16a34a", "✅"
    elif health["writable"]:
        title = "التخزين المحلي يعمل، لكن الـBucket غير متصل"
        bg, fg, border, icon = "#fff7ed", "#9a3412", "#ea580c", "⚠️"
    else:
        title = "مسار التخزين غير قابل للكتابة"
        bg, fg, border, icon = "#fee2e2", "#991b1b", "#dc2626", "❌"

    migration = health.get("migration") or {}
    copied_count = len(migration.get("copied_files", []) or [])
    skipped_count = len(migration.get("skipped_existing", []) or [])
    error_text = html_lib.escape(str(health.get("error", "")))
    error_html = (
        f"<div style='margin-top:6px;'>التفاصيل: {error_text}</div>"
        if error_text else ""
    )

    return f"""
    <div style='background:{bg};color:{fg};padding:13px;border-radius:10px;
                border-right:5px solid {border};margin-bottom:12px;
                font-weight:800;line-height:1.8;direction:rtl;'>
        <div style='font-size:17px;'>{icon} {title}</div>
        <div>المسار الفعلي: <code>{html_lib.escape(DATA_DIR)}</code></div>
        <div>وضع التشغيل: <b>{html_lib.escape(STORAGE_MODE)}</b></div>
        <div>الترحيل: نُسخ {copied_count} بند، وتُرك {skipped_count} بند موجود دون استبدال.</div>
        {error_html}
    </div>
    """

def load_reference_status_registry():
    if not os.path.exists(REFERENCE_STATUS_FILE):
        return {}
    try:
        with open(REFERENCE_STATUS_FILE, "r", encoding="utf-8") as registry_file:
            loaded = json.load(registry_file)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(f"load_reference_status_registry error: {exc}")
        return {}

def save_reference_status_registry(registry):
    return safe_write_json(REFERENCE_STATUS_FILE, registry)

def update_reference_file_status(
    status_key,
    stored_path,
    *,
    original_name="",
    applied=None,
    extracted_count=None,
    department="",
):
    registry = load_reference_status_registry()
    record = registry.get(str(status_key), {})
    now_text = datetime.datetime.now(tz_oman).strftime("%Y-%m-%d %H:%M:%S")

    record.update({
        "status_key": str(status_key),
        "stored_path": str(stored_path or ""),
        "stored_name": os.path.basename(str(stored_path or "")) if stored_path else "",
        "department": str(department or record.get("department", "")),
        "updated_at": now_text,
    })

    if original_name:
        record["original_name"] = os.path.basename(str(original_name))
    if applied is not None:
        record["applied"] = bool(applied)
        if bool(applied):
            record["applied_at"] = now_text
    if extracted_count is not None:
        try:
            record["extracted_count"] = int(extracted_count)
        except Exception:
            record["extracted_count"] = extracted_count

    registry[str(status_key)] = record
    save_reference_status_registry(registry)
    return record

def _reference_status_key(kind, department=""):
    if kind == "schedule":
        return f"schedule::{str(department).strip()}"
    return str(kind).strip()



def get_reference_file_status(file_path, status_key="", data_loaded=False):
    registry = load_reference_status_registry()
    record = registry.get(str(status_key), {}) if status_key else {}
    file_exists = os.path.isfile(file_path)
    data_active = bool(data_loaded or record.get("applied", False))

    if file_exists:
        modified_time = datetime.datetime.fromtimestamp(
            os.path.getmtime(file_path),
            tz=tz_oman
        ).strftime("%Y-%m-%d %H:%M")
        file_name = (
            record.get("original_name")
            or record.get("stored_name")
            or os.path.basename(file_path)
        )

        if data_active:
            return {
                "exists": True,
                "data_loaded": True,
                "status_kind": "active",
                "status_text": "✅ الملف محفوظ والبيانات مفعّلة",
                "file_name": file_name,
                "modified_at": record.get("applied_at") or modified_time,
            }

        return {
            "exists": True,
            "data_loaded": False,
            "status_kind": "stored",
            "status_text": "🟠 الملف محفوظ وينتظر تحديث البيانات",
            "file_name": file_name,
            "modified_at": modified_time,
        }

    if data_active:
        return {
            "exists": False,
            "data_loaded": True,
            "status_kind": "loaded_only",
            "status_text": "🔵 البيانات محمّلة والملف المرجعي غير موجود",
            "file_name": record.get("original_name") or "—",
            "modified_at": record.get("applied_at") or record.get("updated_at") or "—",
        }

    return {
        "exists": False,
        "data_loaded": False,
        "status_kind": "missing",
        "status_text": "❌ لا يوجد ملف ولا بيانات مفعّلة",
        "file_name": record.get("original_name") or "—",
        "modified_at": record.get("updated_at") or "—",
    }

DEFAULT_SCHOOL_CONFIG = {
    "ministry_name": "وزارة التعليم",
    "directorate_region": "جنوب الباطنة",
    "directorate_prefix": "المديرية العامة للتعليم بمحافظة",
    "system_name": "منظومة مسار",
    "system_subtitle": "للاحتياط والتبادل الودي",
    "school_name": "مدرسة الباسط للتعليم الأساسي (8-10)",
    "developer_credit": "فكرة وتطوير: أ. محمود اليحيائي - أ. وليد الهنائي © 2026",
    "logo_url": "https://i.imgur.com/1cxFlX7.png",
    "theme_color": "#004d40",
    "theme_color_2": "#00695c",
    "accent_color": "#ffca28",
    "periods_per_day": 7,
    "week_days": ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"],
    "weekend_days": ["الجمعة", "السبت"],
    "official_departments": ["الهيئة الإدارية", "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية"]
}

def load_school_config():
    """
    v1.5 — تحميل إعدادات المدرسة.
    إذا لم يوجد الملف يتم إنشاؤه بالقيم الافتراضية.
    """
    ensure_data_directories()
    config = dict(DEFAULT_SCHOOL_CONFIG)

    if os.path.exists(SCHOOL_CONFIG_FILE):
        try:
            with open(SCHOOL_CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                config.update({k: v for k, v in loaded.items() if v is not None})
        except Exception as e:
            print(f"load_school_config warning: {e}")
    else:
        try:
            safe_write_json(SCHOOL_CONFIG_FILE, config, make_backup=False)
        except Exception as e:
            print(f"create school_config warning: {e}")

    return config

SCHOOL_CONFIG = load_school_config()

MINISTRY_NAME = str(DEFAULT_SCHOOL_CONFIG["ministry_name"])
DIRECTORATE_PREFIX = str(DEFAULT_SCHOOL_CONFIG["directorate_prefix"])
DIRECTORATE_REGION = str(SCHOOL_CONFIG.get("directorate_region", DEFAULT_SCHOOL_CONFIG["directorate_region"]))
DIRECTORATE_FULL_NAME = f"{DIRECTORATE_PREFIX} {DIRECTORATE_REGION}".strip()
SYSTEM_NAME = str(DEFAULT_SCHOOL_CONFIG["system_name"])
SYSTEM_SUBTITLE = str(DEFAULT_SCHOOL_CONFIG["system_subtitle"])
SCHOOL_NAME = str(SCHOOL_CONFIG.get("school_name", DEFAULT_SCHOOL_CONFIG["school_name"]))
DEVELOPER_CREDIT = str(DEFAULT_SCHOOL_CONFIG["developer_credit"])
SCHOOL_LOGO_URL = str(SCHOOL_CONFIG.get("logo_url", DEFAULT_SCHOOL_CONFIG["logo_url"]))
THEME_COLOR = str(SCHOOL_CONFIG.get("theme_color", DEFAULT_SCHOOL_CONFIG["theme_color"]))
THEME_COLOR_2 = str(SCHOOL_CONFIG.get("theme_color_2", DEFAULT_SCHOOL_CONFIG["theme_color_2"]))
ACCENT_COLOR = str(SCHOOL_CONFIG.get("accent_color", DEFAULT_SCHOOL_CONFIG["accent_color"]))

try:
    MAX_PERIODS = int(SCHOOL_CONFIG.get("periods_per_day", DEFAULT_SCHOOL_CONFIG["periods_per_day"]))
except Exception:
    MAX_PERIODS = int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])

SCHOOL_WEEK_DAYS = list(SCHOOL_CONFIG.get("week_days", DEFAULT_SCHOOL_CONFIG["week_days"]) or DEFAULT_SCHOOL_CONFIG["week_days"])
SCHOOL_WEEKEND_DAYS = list(SCHOOL_CONFIG.get("weekend_days", DEFAULT_SCHOOL_CONFIG["weekend_days"]) or DEFAULT_SCHOOL_CONFIG["weekend_days"])

OFFICIAL_DEPTS = list(SCHOOL_CONFIG.get("official_departments", DEFAULT_SCHOOL_CONFIG["official_departments"]) or DEFAULT_SCHOOL_CONFIG["official_departments"])

ADMIN_ROLES = ["مدير المدرسة", "المدير المساعد", "منسق شؤون مدرسية", "أخصائي توجيه مهني", "أخصائي اجتماعي", "أخصائي شؤون ادارية ومالية", "أخصائي مصادر التعلم", "أخصائي أنظمة مدرسية", "فني مختبر علوم", "فني دعم أجهزة مدرسية ثالث"]
ALL_ROLES = ["معلم", "معلم أول", "منسق مادة"] + ADMIN_ROLES

# v1.3 — أدوار وصلاحيات مركزية
OWNER_ROLE = "صاحب النظام"
SHARED_TEACHER_ROLE = "مستخدم عام"
ADMIN_ACCESS_ROLES = ["مدير المدرسة", "المدير المساعد"]
DEPT_LEADER_ROLES = ["معلم أول", "منسق مادة"]



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
            "role": "صاحب النظام",
            "dept": "الكل",
            "name": owner_name,
            "is_owner": True,
        }

    return auth_map


AUTH_DB = load_auth_db()


# ─────────────────────────────────────────────────────────────────────────────
# v1.8.2 — حسابات دخول مشفرة قابلة للتغيير وإعادة التعيين
# ─────────────────────────────────────────────────────────────────────────────

AUTH_ACCOUNTS_VERSION = 1
PIN_HASH_ALGORITHM = "pbkdf2_sha256"
PIN_HASH_ITERATIONS = 210_000
OWNER_ACCOUNT_ID = "__owner_secret__"

def _auth_now_text():
    return datetime.datetime.now(tz_oman).strftime("%Y-%m-%d %H:%M:%S")

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


ACCOUNT_WELCOME_DEFAULT_TEMPLATE = "{welcome_title} ({display_name}) {welcome_phrase}"
ACCOUNT_WELCOME_PLACEHOLDERS = [
    "{name}",
    "{display_name}",
    "{welcome_title}",
    "{official_title}",
    "{whatsapp_title}",
    "{department}",
    "{department_label}",
    "{school_name}",
    "{role}",
]


def _clean_account_profile_value(value):
    return str(value or "").strip()


def _default_department_label(record):
    dept = _clean_account_profile_value(record.get("dept", ""))
    if dept and dept not in {"الكل", "المعلمون", "الهيئة الإدارية"}:
        return f"قسم {dept}"
    return dept


def _account_profile_context(record):
    safe_record = record if isinstance(record, dict) else {}
    name = _clean_account_profile_value(safe_record.get("name", ""))
    role = _clean_account_profile_value(safe_record.get("role", ""))
    dept = _clean_account_profile_value(safe_record.get("dept", ""))
    display_name = _clean_account_profile_value(
        safe_record.get("display_name") or name
    )
    official_title = _clean_account_profile_value(
        safe_record.get("official_title") or role
    )
    whatsapp_title = _clean_account_profile_value(
        safe_record.get("whatsapp_title") or official_title or role
    )
    welcome_title = _clean_account_profile_value(
        safe_record.get("welcome_title", "")
    )
    department_label = _clean_account_profile_value(
        safe_record.get("department_label") or _default_department_label(safe_record)
    )
    welcome_phrase = _clean_account_profile_value(
        safe_record.get("welcome_phrase", "")
    )
    return {
        "name": name,
        "display_name": display_name,
        "official_title": official_title,
        "whatsapp_title": whatsapp_title,
        "welcome_title": welcome_title,
        "department": dept,
        "department_label": department_label,
        "school_name": SCHOOL_NAME,
        "role": role,
        "welcome_phrase": welcome_phrase,
    }


def _safe_format_account_template(template, context):
    template_text = _clean_account_profile_value(template)
    if not template_text:
        return ""
    try:
        return template_text.format(**context)
    except Exception:
        # لا نكسر الدخول بسبب قوس ناقص في عبارة ترحيب. الواجهة ليست مكانًا لتأملات الترايسباك.
        return template_text


def build_account_welcome_text(record):
    context = _account_profile_context(record)
    template = _clean_account_profile_value(record.get("welcome_template", "")) if isinstance(record, dict) else ""

    has_custom_profile = any([
        context.get("welcome_title"),
        context.get("welcome_phrase"),
        template,
        _clean_account_profile_value(record.get("display_name", "")) if isinstance(record, dict) else "",
    ])

    if has_custom_profile:
        if not template:
            template = ACCOUNT_WELCOME_DEFAULT_TEMPLATE
        text_value = _safe_format_account_template(template, context)
        text_value = re.sub(r"\s+", " ", str(text_value or "")).strip()
        # تنظيف الأقواس الفارغة إذا لم يضبط المستخدم بعض الحقول.
        text_value = text_value.replace("()", "").replace("( )", "").strip()
        if text_value:
            return text_value

    role = context.get("role", "")
    dept = context.get("department", "")
    raw_msg = WELCOME_MESSAGES.get(
        role,
        WELCOME_MESSAGES.get(dept, "مرحباً بك ({name}) في النظام."),
    )
    try:
        return raw_msg.format(name=context.get("display_name") or context.get("name"))
    except Exception:
        return raw_msg


def render_account_welcome_html(record, temporary_note=""):
    welcome_text = html_lib.escape(build_account_welcome_text(record))
    return (
        "<div style='background:#004d40;color:#ffca28;padding:15px;"
        "border-radius:10px;text-align:center;font-size:18px;"
        "font-weight:bold;margin-bottom:15px;line-height:1.8;'>"
        f"{welcome_text}{temporary_note}</div>"
    )


def get_account_session_display_name(record):
    context = _account_profile_context(record)
    return context.get("display_name") or context.get("name") or context.get("official_title") or ""


def render_account_profile_preview_html(
    display_name,
    official_title,
    welcome_title,
    department_label,
    welcome_phrase,
    welcome_template,
    whatsapp_title,
):
    fake_record = {
        "name": _clean_account_profile_value(display_name).replace("أ. ", ""),
        "display_name": display_name,
        "official_title": official_title,
        "welcome_title": welcome_title,
        "department_label": department_label,
        "welcome_phrase": welcome_phrase,
        "welcome_template": welcome_template,
        "whatsapp_title": whatsapp_title,
        "dept": department_label,
        "role": official_title,
    }
    welcome_text = html_lib.escape(build_account_welcome_text(fake_record))
    whatsapp = html_lib.escape(_clean_account_profile_value(whatsapp_title) or "غير محدد")
    official = html_lib.escape(_clean_account_profile_value(official_title) or "غير محدد")
    return f"""
    <div style='background:linear-gradient(135deg,#004d40,#0f766e);color:#ffca28;
                padding:16px;border-radius:14px;text-align:center;font-size:18px;
                font-weight:900;line-height:1.9;margin-top:8px;'>
        {welcome_text}
    </div>
    <div style='background:#f8fafc;border:1px solid #dbe3e8;border-radius:12px;
                padding:12px;margin-top:10px;line-height:1.8;color:#334155;'>
        <b>المسمى الرسمي:</b> {official}<br>
        <b>مسمى واتساب:</b> {whatsapp}
    </div>
    """


def preview_account_profile_settings(
    display_name,
    official_title,
    welcome_title,
    department_label,
    welcome_phrase,
    welcome_template,
    whatsapp_title,
    is_owner=False,
):
    if not bool(is_owner):
        return (
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>هذه المعاينة مخصصة لمالك النظام فقط.</div>",
        )
    return (
        gr.update(
            value=render_account_profile_preview_html(
                display_name,
                official_title,
                welcome_title,
                department_label,
                welcome_phrase,
                welcome_template,
                whatsapp_title,
            )
        ),
        gr.update(value=""),
    )


def load_auth_account_profile_for_editor(account_id, is_owner=False):
    if not bool(is_owner):
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>هذه اللوحة مخصصة لمالك النظام فقط.</div>",
        )

    payload = load_auth_accounts()
    record = payload.get("accounts", {}).get(str(account_id or "").strip())
    if not isinstance(record, dict):
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#a16207;font-weight:800;'>اختر حسابًا لعرض تخصيص الترحيب.</div>",
        )

    context = _account_profile_context(record)
    template = _clean_account_profile_value(record.get("welcome_template", "")) or ACCOUNT_WELCOME_DEFAULT_TEMPLATE
    preview = render_account_profile_preview_html(
        context["display_name"],
        context["official_title"],
        context["welcome_title"],
        context["department_label"],
        context["welcome_phrase"],
        template,
        context["whatsapp_title"],
    )
    return (
        gr.update(value=context["display_name"]),
        gr.update(value=context["official_title"]),
        gr.update(value=context["welcome_title"]),
        gr.update(value=context["department_label"]),
        gr.update(value=context["welcome_phrase"]),
        gr.update(value=template),
        gr.update(value=context["whatsapp_title"]),
        gr.update(value=preview),
        gr.update(value=""),
    )


@state_locked
def save_auth_account_profile(
    account_id,
    display_name,
    official_title,
    welcome_title,
    department_label,
    welcome_phrase,
    welcome_template,
    whatsapp_title,
    is_owner=False,
    actor_name="",
    actor_role="",
):
    if not bool(is_owner):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>هذه العملية مخصصة لمالك النظام فقط.</div>",
        )

    account_id = str(account_id or "").strip()
    payload = load_auth_accounts()
    account = payload.get("accounts", {}).get(account_id)
    if not isinstance(account, dict):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>اختر حسابًا صالحًا.</div>",
        )

    old_profile = {
        "display_name": account.get("display_name", ""),
        "official_title": account.get("official_title", ""),
        "welcome_title": account.get("welcome_title", ""),
        "department_label": account.get("department_label", ""),
        "welcome_phrase": account.get("welcome_phrase", ""),
        "welcome_template": account.get("welcome_template", ""),
        "whatsapp_title": account.get("whatsapp_title", ""),
    }

    account["display_name"] = _clean_account_profile_value(display_name)
    account["official_title"] = _clean_account_profile_value(official_title)
    account["welcome_title"] = _clean_account_profile_value(welcome_title)
    account["department_label"] = _clean_account_profile_value(department_label)
    account["welcome_phrase"] = _clean_account_profile_value(welcome_phrase)
    account["welcome_template"] = _clean_account_profile_value(welcome_template)
    account["whatsapp_title"] = _clean_account_profile_value(whatsapp_title)
    account["updated_at"] = _auth_now_text()
    account["profile_updated_at"] = account["updated_at"]

    if not save_auth_accounts(payload):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ تخصيص الحساب.</div>",
        )

    new_profile = {
        "display_name": account.get("display_name", ""),
        "official_title": account.get("official_title", ""),
        "welcome_title": account.get("welcome_title", ""),
        "department_label": account.get("department_label", ""),
        "welcome_phrase": account.get("welcome_phrase", ""),
        "welcome_template": account.get("welcome_template", ""),
        "whatsapp_title": account.get("whatsapp_title", ""),
    }

    write_audit_log(
        "تعديل تخصيص حساب دخول",
        target_teacher="",
        old_value=old_profile,
        new_value=new_profile,
        details=f"تعديل هيدر ومسمى حساب: {_account_display_name(account)}",
        actor_name=actor_name,
        actor_role=actor_role,
    )

    choices = get_auth_account_choices()
    preview = render_account_profile_preview_html(
        account.get("display_name", ""),
        account.get("official_title", ""),
        account.get("welcome_title", ""),
        account.get("department_label", ""),
        account.get("welcome_phrase", ""),
        account.get("welcome_template", "") or ACCOUNT_WELCOME_DEFAULT_TEMPLATE,
        account.get("whatsapp_title", ""),
    )
    return (
        gr.update(value=render_auth_accounts_html(True)),
        gr.update(choices=choices, value=account_id),
        gr.update(value=preview),
        (
            "<div style='color:#166534;background:#dcfce7;padding:10px;"
            "border-radius:8px;font-weight:800;'>"
            "تم حفظ تخصيص الترحيب والمسميات بنجاح. سيظهر الهيدر الجديد في تسجيل الدخول القادم."
            "</div>"
        ),
    )

def render_auth_accounts_html(is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:#64748b;text-align:center;padding:12px;'>"
            "تظهر الحسابات لمالك النظام بعد الدخول."
            "</div>"
        )

    payload = load_auth_accounts()
    rows = []

    for account_id, record in sorted(
        payload.get("accounts", {}).items(),
        key=lambda item: _account_display_name(item[1]),
    ):
        enabled = bool(record.get("enabled", True))
        status_text = "مفعل" if enabled else "معطل"
        status_color = "#166534" if enabled else "#b91c1c"
        temporary_text = (
            "نعم"
            if bool(record.get("must_change_pin", False))
            else "لا"
        )
        context = _account_profile_context(record)

        rows.append(f"""
        <tr>
            <td>{html_lib.escape(_account_display_name(record))}</td>
            <td>{html_lib.escape(context.get("display_name", "—"))}</td>
            <td>{html_lib.escape(context.get("official_title", "—"))}</td>
            <td>{html_lib.escape(context.get("whatsapp_title", "—"))}</td>
            <td>{html_lib.escape(str(record.get("role", "—")))}</td>
            <td>{html_lib.escape(str(record.get("dept", "—")))}</td>
            <td style='color:{status_color};font-weight:900;'>{status_text}</td>
            <td>{temporary_text}</td>
            <td>{html_lib.escape(str(record.get("updated_at", "—")))}</td>
        </tr>
        """)

    if not rows:
        return (
            "<div style='background:#fff7ed;color:#9a3412;padding:12px;"
            "border-radius:10px;font-weight:800;'>"
            "لا توجد حسابات غير حساب المالك. تحقق من AUTH_DB_JSON ثم أعد التشغيل."
            "</div>"
        )

    return f"""
    <div style='overflow-x:auto;direction:rtl;border:1px solid #dbe3e8;
                border-radius:12px;'>
        <table style='width:100%;min-width:1150px;border-collapse:collapse;
                      text-align:center;font-size:13px;'>
            <thead>
                <tr style='background:#0f766e;color:#fff;'>
                    <th>اسم الحساب</th>
                    <th>اسم العرض</th>
                    <th>المسمى الرسمي</th>
                    <th>مسمى واتساب</th>
                    <th>الدور الداخلي</th>
                    <th>القسم</th>
                    <th>الحالة</th>
                    <th>رمز مؤقت</th>
                    <th>آخر تحديث</th>
                </tr>
            </thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
    </div>
    """


def refresh_owner_accounts_panel(is_owner=False):
    if not bool(is_owner):
        return (
            gr.update(),
            gr.update(choices=[], value=None),
            gr.update(value=""),
            gr.update(
                value=(
                    "<div style='color:#b91c1c;font-weight:800;'>"
                    "هذه اللوحة مخصصة لمالك النظام فقط."
                    "</div>"
                )
            ),
        )

    choices = get_auth_account_choices()
    return (
        gr.update(value=render_auth_accounts_html(True)),
        gr.update(choices=choices, value=None),
        gr.update(value=""),
        gr.update(value=""),
    )

@state_locked
def change_own_account_pin(
    account_id,
    current_pin,
    new_pin,
    confirm_pin,
    actor_name="",
    actor_role="",
    is_owner=False,
):
    if bool(is_owner) or str(account_id) == OWNER_ACCOUNT_ID:
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            (
                "<div style='color:#9a3412;background:#fff7ed;padding:10px;"
                "border-radius:8px;font-weight:800;'>"
                "رمز مالك النظام يُغيّر من Secret الاستضافة، وليس من داخل المنظومة."
                "</div>"
            ),
        )

    account_id = str(account_id or "").strip()
    payload = load_auth_accounts()
    account = payload.get("accounts", {}).get(account_id)

    if not isinstance(account, dict):
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر تحديد حساب الجلسة الحالية.</div>",
        )

    if not bool(account.get("enabled", True)):
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>الحساب معطل.</div>",
        )

    if not _verify_pin_hash(current_pin, account.get("pin_hash", "")):
        return (
            gr.update(value=""),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>الرمز الحالي غير صحيح.</div>",
        )

    valid, validation_message = _validate_new_pin(new_pin)
    if not valid:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            f"<div style='color:#b91c1c;font-weight:800;'>{html_lib.escape(validation_message)}</div>",
        )

    if str(new_pin) != str(confirm_pin):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>تأكيد الرمز الجديد غير مطابق.</div>",
        )

    if _verify_pin_hash(new_pin, account.get("pin_hash", "")):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#a16207;font-weight:800;'>الرمز الجديد مطابق للرمز الحالي.</div>",
        )

    if _pin_is_used_by_another_account(
        new_pin,
        exclude_account_id=account_id,
    ):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>هذا الرمز مستخدم لحساب آخر.</div>",
        )

    account["pin_hash"] = _pin_hash(new_pin)
    account["must_change_pin"] = False
    account["updated_at"] = _auth_now_text()
    account["pin_changed_at"] = account["updated_at"]

    if not save_auth_accounts(payload):
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ الرمز الجديد.</div>",
        )

    write_audit_log(
        "تغيير رمز دخول",
        target_teacher="",
        old_value="رمز مشفر",
        new_value="رمز مشفر",
        details=f"غيّر المستخدم رمز حساب: {_account_display_name(account)}",
        actor_name=actor_name,
        actor_role=actor_role,
    )

    return (
        gr.update(value=""),
        gr.update(value=""),
        gr.update(value=""),
        (
            "<div style='color:#166534;background:#dcfce7;padding:10px;"
            "border-radius:8px;font-weight:800;'>"
            "تم تغيير رمز الدخول بنجاح. استخدم الرمز الجديد في الدخول القادم."
            "</div>"
        ),
    )

@state_locked
def owner_reset_account_pin(
    account_id,
    requested_pin,
    is_owner=False,
    actor_name="",
    actor_role="",
):
    if not bool(is_owner):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>هذه العملية للمالك فقط.</div>",
        )

    account_id = str(account_id or "").strip()
    payload = load_auth_accounts()
    account = payload.get("accounts", {}).get(account_id)

    if not isinstance(account, dict):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>اختر حسابًا صالحًا.</div>",
        )

    new_pin = str(requested_pin or "").strip()
    if not new_pin:
        new_pin = "".join(secrets.choice("0123456789") for _ in range(6))

    valid, validation_message = _validate_new_pin(new_pin)
    if not valid:
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update(),
            f"<div style='color:#b91c1c;font-weight:800;'>{html_lib.escape(validation_message)}</div>",
        )

    if _pin_is_used_by_another_account(
        new_pin,
        exclude_account_id=account_id,
    ):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>هذا الرمز مستخدم لحساب آخر.</div>",
        )

    account["pin_hash"] = _pin_hash(new_pin)
    account["must_change_pin"] = True
    account["updated_at"] = _auth_now_text()
    account["pin_reset_at"] = account["updated_at"]
    account["pin_reset_by"] = str(actor_name or "مالك النظام")

    if not save_auth_accounts(payload):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ إعادة التعيين.</div>",
        )

    write_audit_log(
        "إعادة تعيين رمز دخول",
        target_teacher="",
        old_value="رمز مشفر",
        new_value="رمز مؤقت مشفر",
        details=f"إعادة تعيين حساب: {_account_display_name(account)}",
        actor_name=actor_name,
        actor_role=actor_role,
    )

    choices = get_auth_account_choices()
    return (
        gr.update(value=render_auth_accounts_html(True)),
        gr.update(choices=choices, value=account_id),
        gr.update(value=""),
        gr.update(value=new_pin),
        (
            "<div style='color:#166534;background:#dcfce7;padding:10px;"
            "border-radius:8px;font-weight:800;'>"
            "تمت إعادة التعيين. يظهر الرمز الجديد في خانة «الرمز الجديد لمرة واحدة»."
            "</div>"
        ),
    )

@state_locked
def owner_toggle_account_status(
    account_id,
    is_owner=False,
    actor_name="",
    actor_role="",
):
    if not bool(is_owner):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>هذه العملية للمالك فقط.</div>",
        )

    account_id = str(account_id or "").strip()
    payload = load_auth_accounts()
    account = payload.get("accounts", {}).get(account_id)

    if not isinstance(account, dict):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>اختر حسابًا صالحًا.</div>",
        )

    new_enabled = not bool(account.get("enabled", True))
    account["enabled"] = new_enabled
    account["updated_at"] = _auth_now_text()

    if not save_auth_accounts(payload):
        return (
            gr.update(),
            gr.update(),
            gr.update(value=""),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر تحديث حالة الحساب.</div>",
        )

    action_name = "تفعيل حساب دخول" if new_enabled else "تعطيل حساب دخول"
    write_audit_log(
        action_name,
        target_teacher="",
        old_value="معطل" if new_enabled else "مفعل",
        new_value="مفعل" if new_enabled else "معطل",
        details=f"{action_name}: {_account_display_name(account)}",
        actor_name=actor_name,
        actor_role=actor_role,
    )

    choices = get_auth_account_choices()
    status_word = "تفعيل" if new_enabled else "تعطيل"
    return (
        gr.update(value=render_auth_accounts_html(True)),
        gr.update(choices=choices, value=account_id),
        gr.update(value=""),
        (
            "<div style='color:#166534;background:#dcfce7;padding:10px;"
            "border-radius:8px;font-weight:800;'>"
            f"تم {status_word} الحساب بنجاح."
            "</div>"
        ),
    )


WELCOME_MESSAGES = {
    "صاحب النظام": " أهلاً بك يا صاحب النظام ({name}).. جميع الصلاحيات العليا أصبحت بين يديك.",
    "مستخدم عام": " أهلاً بكم في منظومة الباسط ونورتونا.. تم تجهيز الوصول إلى التبادل الودي الأسبوعي، جدول اليوم، وجدول المعلم الأسبوعي.",
    "مدير المدرسة": " أهلاً بك يا قائد المدرسة وربان سفينتها ({name}).. الرادار الإداري وغرفة العمليات رهن إشارتك.",
    "المدير المساعد": " أهلاً بالذراع الأيمن للقيادة والسند الإداري ({name}).. صلاحيات التدخل المفتوحة مفعلة.",
    "العلوم": " مرحباً بقائد الملحمة والمعلم الأول ({name}).. تم تجهيز شاشة قسم العلوم بدقة.",
    "الرياضيات": " نورتنا مهندس الأرقام ({name}) شاشة قسم الرياضيات جاهزة لك.",
    "التربية الإسلامية": " سُعدنا بانضمامك ({name}) شاشة قسم التربية الإسلامية جاهزة لك.",
    "اللغة العربية": " مايسترو البيان ({name}) نورتنا وقسم اللغة العربية جاهز لك.",
    "اللغة الإنجليزية": " أهلا بك سفير اللغة ({name}) شاشة قسم اللغة الإنجليزية جاهزة لك.",
    "الدراسات الإجتماعية": " مرحبا بك ({name}) قسم الدراسات الإجتماعية جاهز.",
    "المهارات الفردية": " سُعدنا بانضمامك ({name}) هذه مساحة للتنسيق وتنظيم العمل."
}

last_assigned_teachers = []
processed_absences = set()

def dept_has_loaded_schedule_data(dept_name):
    weekdays = SCHOOL_WEEK_DAYS
    for _teacher_name, info in teachers_db.items():
        if str(info.get("dept", "")).strip() != str(dept_name).strip():
            continue
        for day_name in weekdays:
            day_schedule = info.get(day_name, {})
            if isinstance(day_schedule, dict) and any(str(v).strip() for v in day_schedule.values()):
                return True
    return False

def get_school_data_center_status():
    schedules_status = {}
    for dept_name, file_path in SCHEDULE_FILES.items():
        schedules_status[dept_name] = get_reference_file_status(
            file_path,
            _reference_status_key("schedule", dept_name),
            dept_has_loaded_schedule_data(dept_name),
        )

    admin_loaded = any(
        str(info.get("dept", "")).strip() == "الهيئة الإدارية"
        or str(info.get("role", "")).strip() in ADMIN_ROLES
        for info in teachers_db.values()
    )
    phones_loaded = any(
        str(info.get("phone", "")).strip()
        for info in teachers_db.values()
    )

    return {
        "admin_file": get_reference_file_status(
            ADMIN_FILE, _reference_status_key("admin"), admin_loaded
        ),
        "phones_file": get_reference_file_status(
            PHONES_FILE, _reference_status_key("phones"), phones_loaded
        ),
        "schedules": schedules_status,
    }
def render_reference_file_card(title, file_info):
    status_kind = file_info.get("status_kind", "missing")
    palettes = {
        "active": ("#2e7d32", "#e8f5e9"),
        "stored": ("#d97706", "#fff7ed"),
        "loaded_only": ("#0284c7", "#e0f2fe"),
        "missing": ("#c62828", "#ffebee"),
    }
    status_color, bg_color = palettes.get(status_kind, palettes["missing"])

    return f"""
    <div style="
        background:{bg_color};
        border:1px solid #d0d7de;
        border-right:5px solid {status_color};
        border-radius:12px;
        padding:16px;
        margin-bottom:12px;
        box-shadow:0 2px 6px rgba(0,0,0,0.05);
    ">
        <div style="font-size:18px; font-weight:bold; color:#004d40; margin-bottom:10px;">
            {title}
        </div>
        <div style="font-size:15px; margin-bottom:6px;">
            <b>الحالة:</b> {file_info["status_text"]}
        </div>
        <div style="font-size:15px; margin-bottom:6px;">
            <b>اسم الملف:</b> {file_info["file_name"]}
        </div>
        <div style="font-size:15px;">
            <b>آخر تحديث:</b> {file_info["modified_at"]}
        </div>
    </div>
    """
def render_admin_reference_card():
    status = get_school_data_center_status()
    return render_reference_file_card("🏢 ملف الإداريين", status["admin_file"])
def render_phones_reference_card():
    status = get_school_data_center_status()
    return render_reference_file_card("📱 ملف أرقام المعلمين", status["phones_file"])
def render_schedule_reference_cards():
    status = get_school_data_center_status()
    html_parts = ['<div style="margin-top:14px;"><h3 style="color:#004d40; margin-bottom:12px;">📚 ملفات جداول الأقسام</h3></div>']

    for dept_name, _file_path in SCHEDULE_FILES.items():
        file_info = status["schedules"][dept_name]
        html_parts.append(render_reference_file_card(f"📘 {dept_name}", file_info))

    return "".join(html_parts)
@state_locked
def save_admin_reference_file(file, is_owner=False):
    if not bool(is_owner):
        return "<div style='color:red; font-weight:bold;'>❌ اعتماد الملفات المرجعية متاح لمالك النظام فقط.</div>", gr.update(value=render_admin_reference_card())
    if file is None:
        return "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف الإداريين أولاً.</div>", gr.update(value=render_admin_reference_card())

    is_valid, msg = validate_reference_filename(file.name, ["الإدارة", "الادارة", "الإداريين", "الاداريين", "admin", "staff"])
    if not is_valid:
        return f"<div style='color:red; font-weight:bold;'>{msg}</div>", gr.update(value=render_admin_reference_card())

    try:
        ensure_data_directories()
        shutil.copy2(file.name, ADMIN_FILE)
        update_reference_file_status(
            _reference_status_key("admin"),
            ADMIN_FILE,
            original_name=file.name,
            applied=False,
        )
        return "<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد ملف الإداريين المرجعي بنجاح.</div>", gr.update(value=render_admin_reference_card())
    except Exception as e:
        return f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ الملف المرجعي: {str(e)}</div>", gr.update(value=render_admin_reference_card())
@state_locked
def refresh_admins_from_reference(dept_filter, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث بيانات الإداريين متاح لمالك النظام فقط.</div>",
            gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(value=render_admin_reference_card()), gr.update(value=None)
        )
    if not os.path.exists(ADMIN_FILE):
        return (
        "<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف إداريين مرجعي حتى الآن.</div>",
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(value=render_admin_reference_card()),
        gr.update(value=None)
    )

    try:
        df = pd.read_excel(ADMIN_FILE, header=None) if not ADMIN_FILE.endswith(".csv") else pd.read_csv(ADMIN_FILE, header=None)
        df = df.fillna("")

        added_or_updated = 0
        found_names = []

        for r in range(len(df)):
            raw_phone = str(df.iloc[r, 0]).strip() if df.shape[1] > 0 else ""
            raw_role = str(df.iloc[r, 1]).strip() if df.shape[1] > 1 else ""
            raw_name = str(df.iloc[r, 2]).strip() if df.shape[1] > 2 else ""

            if not raw_name or raw_name == "nan":
                continue

            t_name = clean_teacher_name(raw_name)
            if not t_name or len(t_name) < 3:
                continue

            if raw_phone.endswith(".0"):
                raw_phone = raw_phone[:-2]

            phone_digits = re.sub(r"\D", "", raw_phone)
            if len(phone_digits) == 8:
                phone_digits = "968" + phone_digits

            role_val = raw_role if raw_role else "أخصائي اجتماعي"

            if t_name not in teachers_db:
                teachers_db[t_name] = {
                    "dept": "الهيئة الإدارية",
                    "cover_count": 0,
                    "absent_count": 0,
                    "shortcoming_count": 0,
                    "phone": "",
                    "specialty": "",
                    "role": role_val,
                    "exempt_days": [],
                    "exempt_periods": [],
                    "absence_dates": [],
                    "الأحد": {},
                    "الإثنين": {},
                    "الثلاثاء": {},
                    "الأربعاء": {},
                    "الخميس": {}
                }
            else:
                teachers_db[t_name]["dept"] = "الهيئة الإدارية"
                teachers_db[t_name]["role"] = role_val

            teachers_db[t_name]["phone"] = phone_digits if phone_digits else teachers_db[t_name].get("phone", "")
            found_names.append(t_name)
            added_or_updated += 1

        save_db()
        update_reference_file_status(
            _reference_status_key("admin"),
            ADMIN_FILE,
            applied=True,
            extracted_count=added_or_updated,
        )

        dept_filter = resolve_effective_dept(dept_filter)
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        t_names_filtered = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])

        names_list_str = "، ".join(found_names) if found_names else "لا توجد أسماء صالحة"
        msg = (
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>"
            f"✅ تم تحديث بيانات الإداريين من الملف المرجعي بنجاح."
            f"<br>👥 الأسماء: {names_list_str}"
            f"</div>"
        )

        return (
            msg,
            gr.update(choices=abs_choices),
            gr.update(choices=choices_all, value=None),
            gr.update(choices=choices_all, value=None),
            gr.update(choices=t_names_filtered, value=None),
            gr.update(value=get_updated_balance(dept_filter)),
            gr.update(value=render_admin_reference_card()),
            gr.update(value=None)
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث الإداريين من المرجع: {str(e)}</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(value=render_admin_reference_card()),
            gr.update(value=None)
        )
@state_locked
def save_phones_reference_file(file, is_owner=False):
    if not bool(is_owner):
        return "<div style='color:red; font-weight:bold;'>❌ اعتماد الملفات المرجعية متاح لمالك النظام فقط.</div>", gr.update(value=render_phones_reference_card())
    if file is None:
        return "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف أرقام المعلمين أولاً.</div>", gr.update(value=render_phones_reference_card())

    try:
        file_path = file.name if hasattr(file, "name") else str(file)

        is_valid, msg = validate_reference_filename(file_path, ["أرقام", "ارقام", "هواتف", "واتساب", "phones", "teacher_phones"])
        if not is_valid:
            return f"<div style='color:red; font-weight:bold;'>{msg}</div>", gr.update(value=render_phones_reference_card())

        ensure_data_directories()
        shutil.copy2(file_path, PHONES_FILE)
        update_reference_file_status(
            _reference_status_key("phones"),
            PHONES_FILE,
            original_name=file_path,
            applied=False,
        )

        return "<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد ملف أرقام المعلمين المرجعي بنجاح.</div>", gr.update(value=render_phones_reference_card())

    except Exception as e:
        return f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ ملف الأرقام المرجعي: {str(e)}</div>", gr.update(value=render_phones_reference_card())
@state_locked
def refresh_phones_from_reference(dept_filter, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث أرقام المعلمين متاح لمالك النظام فقط.</div>",
            gr.update(), gr.update(value=render_phones_reference_card()), gr.update(value=None)
        )
    if not os.path.exists(PHONES_FILE):
        return (
            "<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف أرقام معلمين مرجعي حتى الآن.</div>",
            gr.update(),
            gr.update(value=render_phones_reference_card()),
            gr.update(value=None)
        )

    try:
        df = pd.read_excel(PHONES_FILE, header=None) if not PHONES_FILE.endswith(".csv") else pd.read_csv(PHONES_FILE, header=None)
        updated = 0
        db_fingerprints = {k: get_name_fingerprint(k) for k in teachers_db.keys()}

        for r in range(len(df)):
            raw_name = str(df.iloc[r, 0]).strip() if df.shape[1] > 0 else ""
            raw_phone = str(df.iloc[r, 1]).strip() if df.shape[1] > 1 else ""

            if not raw_name or raw_name == "nan":
                continue

            if raw_phone.endswith(".0"):
                raw_phone = raw_phone[:-2]

            phone_digits = re.sub(r"\D", "", raw_phone)
            if len(phone_digits) == 8:
                phone_digits = "968" + phone_digits
            if not phone_digits:
                continue

            phone_first_name, phone_name_fingerprint = get_name_fingerprint(raw_name)
            if not phone_first_name:
                continue

            for db_key, (db_first_name, db_words) in db_fingerprints.items():
                if db_first_name == phone_first_name and len(db_words) > 0 and db_words.issubset(phone_name_fingerprint):
                    teachers_db[db_key]["phone"] = phone_digits
                    updated += 1
                    break

        save_db()
        update_reference_file_status(
            _reference_status_key("phones"),
            PHONES_FILE,
            applied=True,
            extracted_count=updated,
        )

        msg = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>✅ تم تحديث أرقام ({updated}) من المعلمين من الملف المرجعي بنجاح.</div>"

        return (
            msg,
            gr.update(value=get_updated_balance(dept_filter)),
            gr.update(value=render_phones_reference_card()),
            gr.update(value=None)
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث أرقام المعلمين من المرجع: {str(e)}</div>",
            gr.update(),
            gr.update(value=render_phones_reference_card()),
            gr.update(value=None)
        )
@state_locked
def save_schedule_reference_file(file, dept_name, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ اعتماد الجداول المرجعية متاح لمالك النظام فقط.</div>",
            gr.update(value=render_schedule_reference_cards())
        )
    if file is None:
        return (
            "<div style='color:red; font-weight:bold;'>❌ الرجاء اختيار ملف الجدول أولاً.</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    if dept_name not in SCHEDULE_FILES:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ القسم غير معتمد: {dept_name}</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    try:
        file_path = file.name if hasattr(file, "name") else str(file)

        is_valid, msg = validate_reference_filename(file_path, [dept_name])
        if not is_valid:
            return (
                f"<div style='color:red; font-weight:bold;'>{msg}</div>",
                gr.update(value=render_schedule_reference_cards())
            )

        ensure_data_directories()
        shutil.copy2(file_path, SCHEDULE_FILES[dept_name])
        update_reference_file_status(
            _reference_status_key("schedule", dept_name),
            SCHEDULE_FILES[dept_name],
            original_name=file_path,
            applied=False,
            department=dept_name,
        )

        return (
            f"<div style='color:#2e7d32; font-weight:bold;'>✅ تم اعتماد الملف المرجعي لقسم ({dept_name}) بنجاح.</div>",
            gr.update(value=render_schedule_reference_cards())
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء حفظ الملف المرجعي للقسم ({dept_name}): {str(e)}</div>",
            gr.update(value=render_schedule_reference_cards())
        )
@state_locked
def refresh_schedule_from_reference(dept_name, current_day, is_owner=False):
    if not bool(is_owner):
        return (
            "<div style='color:red; font-weight:bold;'>❌ تحديث الجداول المرجعية متاح لمالك النظام فقط.</div>",
            gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(value=render_schedule_reference_cards()), gr.update(value=None)
        )
    if dept_name not in SCHEDULE_FILES:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ القسم غير معتمد: {dept_name}</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(value=render_schedule_reference_cards()),
            gr.update(value=None)
        )

    schedule_file = SCHEDULE_FILES[dept_name]

    if not os.path.exists(schedule_file):
        return (
            f"<div style='color:red; font-weight:bold;'>❌ لا يوجد ملف مرجعي محفوظ لقسم ({dept_name}) حتى الآن.</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(value=render_schedule_reference_cards()),
            gr.update(value=None)
        )

    try:
        df = pd.read_excel(schedule_file, header=None) if not schedule_file.endswith(".csv") else pd.read_csv(schedule_file, header=None)
        df = df.fillna('')
        found_in_file = []
        start_row = 0

        for i in range(min(15, len(df))):
            row_str = " ".join([str(x) for x in df.iloc[i].values])
            if "اليوم" in row_str and ("الأولى" in row_str or "الاولى" in row_str):
                start_row = i - 2
                break

        if start_row < 0:
            start_row = 0

        for r in range(start_row, len(df), 10):
            if r + 2 >= len(df):
                break

            for base_col in [0, 9]:
                if base_col + 7 >= len(df.columns):
                    continue

                t_name_raw = str(df.iloc[r, base_col]).strip()
                if not t_name_raw or "ALBATINAH" in t_name_raw.upper() or "اليوم" in t_name_raw:
                    continue

                t_name = clean_teacher_name(t_name_raw)
                if not t_name or len(t_name) < 3:
                    continue

                if t_name not in found_in_file:
                    found_in_file.append(t_name)

                if t_name not in teachers_db:
                    teachers_db[t_name] = {
                        "dept": dept_name,
                        "cover_count": 0,
                        "absent_count": 0,
                        "shortcoming_count": 0,
                        "phone": "",
                        "specialty": "",
                        "role": "معلم",
                        "exempt_days": [],
                        "exempt_periods": [],
                        "absence_dates": [],
                        "الأحد": {},
                        "الإثنين": {},
                        "الثلاثاء": {},
                        "الأربعاء": {},
                        "الخميس": {}
                    }
                else:
                    teachers_db[t_name]["dept"] = dept_name
                    teachers_db[t_name]["الأحد"] = {}
                    teachers_db[t_name]["الإثنين"] = {}
                    teachers_db[t_name]["الثلاثاء"] = {}
                    teachers_db[t_name]["الأربعاء"] = {}
                    teachers_db[t_name]["الخميس"] = {}

                col_to_p = {}
                day_col = -1
                for c in range(base_col, min(base_col + 8, len(df.columns))):
                    val = str(df.iloc[r+2, c]).strip().replace("أ", "ا").replace("إ", "ا")
                    if "اليوم" in val:
                        day_col = c
                    elif "الاولى" in val:
                        col_to_p[c] = 1
                    elif "الثانية" in val:
                        col_to_p[c] = 2
                    elif "الثالثة" in val:
                        col_to_p[c] = 3
                    elif "الرابعة" in val:
                        col_to_p[c] = 4
                    elif "الخامسة" in val:
                        col_to_p[c] = 5
                    elif "السادسة" in val:
                        col_to_p[c] = 6
                    elif "السابعة" in val:
                        col_to_p[c] = 7

                if day_col == -1:
                    day_col = base_col + 7
                if day_col >= len(df.columns):
                    continue

                for dr in range(r+3, min(r+8, len(df))):
                    day_cell = str(df.iloc[dr, day_col]).replace("أ", "ا").replace("إ", "ا")
                    current_day_val = next((d for d in ["الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس"] if d in day_cell), None)
                    if not current_day_val:
                        continue

                    current_day_val = current_day_val.replace("الاحد", "الأحد").replace("الاثنين", "الإثنين").replace("الاربعاء", "الأربعاء")

                    for c, pnum in col_to_p.items():
                        if c < len(df.columns):
                            val = str(df.iloc[dr, c]).strip()
                            cls = extract_class_info(val, dept_name)
                            if cls:
                                teachers_db[t_name][current_day_val][pnum] = cls

        save_db()
        update_reference_file_status(
            _reference_status_key("schedule", dept_name),
            schedule_file,
            applied=True,
            extracted_count=len(found_in_file),
            department=dept_name,
        )

        t_names_all = sorted(list(teachers_db.keys()))
        choices_all = get_teacher_choices("الكل")
        abs_choices = get_absentee_choices("الكل")
        names_list_str = "، ".join(found_in_file) if found_in_file else "لا توجد أسماء صالحة"

        msg = (
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>"
            f"✅ تم تحديث قسم ({dept_name}) من الملف المرجعي بنجاح."
            f"<br>👥 الأسماء: {names_list_str}"
            f"</div>"
        )

        return (
            msg,
            gr.update(choices=abs_choices),
            gr.update(choices=choices_all, value=None),
            gr.update(choices=choices_all, value=None),
            gr.update(value=get_updated_balance("الكل")),
            gr.update(value=get_updated_absences("الكل")),
            gr.update(value=get_day_overview(current_day, "الكل")),
            gr.update(value=render_schedule_reference_cards()),
            gr.update(value=None)
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء تحديث قسم ({dept_name}) من المرجع: {str(e)}</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(value=render_schedule_reference_cards()),
            gr.update(value=None)
        )

def get_now_oman():
    return datetime.datetime.now(tz_oman)

def get_current_day_oman():
    weekday = get_now_oman().weekday()
    days_map = {6: "الأحد", 0: "الإثنين", 1: "الثلاثاء", 2: "الأربعاء", 3: "الخميس", 4: "الأحد", 5: "الأحد"}
    return days_map.get(weekday, "الأحد")
    




    
def get_date_of_weekday(target_day_name):
    days_map = {"الأحد": 6, "الإثنين": 0, "الثلاثاء": 1, "الأربعاء": 2, "الخميس": 3}
    target_weekday = days_map.get(target_day_name, 6)
    now = get_now_oman()
    diff = (target_weekday - now.weekday()) % 7
    target_date = now + datetime.timedelta(days=diff)
    return target_date.strftime("%Y-%m-%d")

candidate_font_paths = [
    os.path.join(APP_DIR, "Cairo-Regular.ttf"),
    "/app/Cairo-Regular.ttf",
    "./Cairo-Regular.ttf",
]
font_path = next((p for p in candidate_font_paths if os.path.exists(p)), None)
if font_path:
    print(f"font ok: {font_path}")
    arabic_font = fm.FontProperties(fname=font_path)
else:
    print("font warning: Cairo-Regular.ttf not found")
    arabic_font = fm.FontProperties()

image_font_candidate_paths = [
    os.path.join(APP_DIR, "Amiri-Regular.ttf"),
    "/app/Amiri-Regular.ttf",
    "./Amiri-Regular.ttf",
]
image_font_path = next((p for p in image_font_candidate_paths if os.path.exists(p)), None)
if image_font_path:
    print(f"image font ok: {image_font_path}")
    image_font = fm.FontProperties(fname=image_font_path)
else:
    print("image font warning: Amiri-Regular.ttf not found; falling back to Cairo/default")
    image_font = arabic_font
reshaper_config = {'support_ligatures': False}
reshaper = arabic_reshaper.ArabicReshaper(configuration=reshaper_config)

def fix_arabic(text):
    reshaped = reshaper.reshape(str(text))
    bidi = get_display(reshaped)
    for c in ['\u202a', '\u202b', '\u202c', '\u200e', '\u200f']: bidi = bidi.replace(c, '')
    return bidi

teachers_db = {}
daily_db = []
exemptions_log = []

def save_db():
    if not safe_write_json(DB_FILE, teachers_db):
        print("save_db error: safe_write_json failed")

def load_db():
    global teachers_db
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                teachers_db = json.load(f)
        
                for t in teachers_db:
                    teachers_db[t]["phone"] = teachers_db[t].get("phone", "") 
                    teachers_db[t]["specialty"] = teachers_db[t].get("specialty", "") 
                    teachers_db[t]["role"] = teachers_db[t].get("role", "معلم") 
                 
                    teachers_db[t]["exempt_days"] = teachers_db[t].get("exempt_days", [])
                    teachers_db[t]["exempt_periods"] = [int(p) for p in teachers_db[t].get("exempt_periods", [])]
                    teachers_db[t]["absence_dates"] = teachers_db[t].get("absence_dates", [])
                    teachers_db[t]["shortcoming_count"] = teachers_db[t].get("shortcoming_count", 0) 
                    teachers_db[t]["exemption_updated_at"] = teachers_db[t].get("exemption_updated_at", "")
                    
                    for day in SCHOOL_WEEK_DAYS:
                        if day in teachers_db[t]:
                            teachers_db[t][day] = {int(k): str(v) for k, v in teachers_db[t][day].items()}
        except Exception as e: print("Error loading DB:", e)
ensure_data_directories()
load_db()

def save_exemptions_log():
    if not safe_write_json(EXEMPTIONS_LOG_FILE, exemptions_log):
        print("save_exemptions_log error: safe_write_json failed")

def _audit_json_safe(value):
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except Exception:
        return str(value)

def write_audit_log(action, target_teacher="", old_value=None, new_value=None, details="", actor_name="", actor_role=""):
    """تسجيل العمليات الحساسة فقط في ملف data/audit_log.json بصورة متزامنة."""
    lock = _get_json_file_lock(AUDIT_LOG_FILE)
    with lock:
        try:
            ensure_data_directories()
            actor_name = str(actor_name or "").strip() or "غير محدد"
            actor_role = str(actor_role or "").strip() or "غير محدد"

            record = {
                "timestamp": get_now_oman().strftime("%Y-%m-%d %H:%M:%S"),
                "actor_name": actor_name,
                "actor_role": actor_role,
                "action": str(action or "").strip(),
                "target_teacher": str(target_teacher or "").strip(),
                "old_value": _audit_json_safe(old_value),
                "new_value": _audit_json_safe(new_value),
                "details": str(details or "").strip(),
                "source": SYSTEM_NAME
            }

            existing = []
            if os.path.exists(AUDIT_LOG_FILE):
                try:
                    with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if isinstance(loaded, list):
                        existing = loaded
                except Exception:
                    existing = []

            existing.append(record)

            if not safe_write_json(AUDIT_LOG_FILE, existing):
                print("write_audit_log error: safe_write_json failed")

        except Exception as e:
            print(f"write_audit_log error: {e}")

def _queue_audit_change(entries, action, target_teacher, old_value, new_value, details):
    if old_value == new_value:
        return
    entries.append({
        "action": action,
        "target_teacher": target_teacher,
        "old_value": old_value,
        "new_value": new_value,
        "details": details,
    })

def _flush_audit_changes(entries, actor_name="", actor_role=""):
    for entry in entries:
        write_audit_log(
            entry.get("action", ""),
            target_teacher=entry.get("target_teacher", ""),
            old_value=entry.get("old_value"),
            new_value=entry.get("new_value"),
            details=entry.get("details", ""),
            actor_name=actor_name,
            actor_role=actor_role,
        )


# ─────────────────────────────────────────────────────────────────────────────
# v1.7 — لوحة سجل العمليات والنسخ الاحتياطية (مالك النظام فقط)
# ─────────────────────────────────────────────────────────────────────────────

def load_audit_log_records():
    """قراءة سجل العمليات الحساسة بصورة آمنة وإرجاع قائمة مرتبة من الأحدث."""
    lock = _get_json_file_lock(AUDIT_LOG_FILE)
    with lock:
        if not os.path.exists(AUDIT_LOG_FILE):
            return []
        try:
            with open(AUDIT_LOG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if not isinstance(loaded, list):
                return []
            records = [item for item in loaded if isinstance(item, dict)]
            records.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
            return records
        except Exception as e:
            print(f"load_audit_log_records error: {e}")
            return []


def _audit_value_to_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(value)
    return str(value)


def _parse_audit_date(value, label):
    if value is None or value == "":
        return None, ""

    if isinstance(value, datetime.datetime):
        return value.date(), ""

    if isinstance(value, datetime.date):
        return value, ""

    if isinstance(value, (int, float)):
        try:
            return datetime.datetime.fromtimestamp(
                float(value),
                tz=tz_oman,
            ).date(), ""
        except Exception:
            return None, f"قيمة {label} غير صالحة."

    raw = str(value or "").strip()
    if not raw:
        return None, ""

    # يدعم YYYY-MM-DD وكذلك قيم ISO التي قد يعيدها مكوّن Gradio DateTime.
    date_part = raw[:10]
    try:
        return datetime.datetime.strptime(date_part, "%Y-%m-%d").date(), ""
    except Exception:
        return None, f"صيغة {label} غير صحيحة. اختر التاريخ من التقويم."



def get_audit_date_range(preset):
    """إرجاع نطاق تاريخ جاهز وفق توقيت سلطنة عمان."""
    today = datetime.datetime.now(tz_oman).date()
    preset_value = str(preset or "").strip()

    if preset_value == "today":
        start_date = today
        end_date = today
    elif preset_value == "last_7_days":
        start_date = today - datetime.timedelta(days=6)
        end_date = today
    elif preset_value == "this_month":
        start_date = today.replace(day=1)
        end_date = today
    elif preset_value == "clear":
        return None, None
    else:
        return None, None

    return start_date.isoformat(), end_date.isoformat()



def filter_audit_records(records, action_filter="الكل", actor_filter="الكل", teacher_filter="الكل", date_from="", date_to=""):
    start_date, start_error = _parse_audit_date(date_from, "تاريخ البداية")
    end_date, end_error = _parse_audit_date(date_to, "تاريخ النهاية")
    error = start_error or end_error
    if error:
        return [], error
    if start_date and end_date and start_date > end_date:
        return [], "تاريخ البداية يجب ألا يكون بعد تاريخ النهاية."

    action_filter = str(action_filter or "الكل").strip()
    actor_filter = str(actor_filter or "الكل").strip()
    teacher_filter = str(teacher_filter or "الكل").strip()

    filtered = []
    for record in records:
        if action_filter != "الكل" and str(record.get("action", "")).strip() != action_filter:
            continue
        if actor_filter != "الكل" and str(record.get("actor_name", "")).strip() != actor_filter:
            continue
        if teacher_filter != "الكل" and str(record.get("target_teacher", "")).strip() != teacher_filter:
            continue

        timestamp = str(record.get("timestamp", "")).strip()
        record_date = None
        try:
            record_date = datetime.datetime.strptime(timestamp[:10], "%Y-%m-%d").date()
        except Exception:
            pass

        if start_date and (record_date is None or record_date < start_date):
            continue
        if end_date and (record_date is None or record_date > end_date):
            continue
        filtered.append(record)

    return filtered, ""


def _audit_filter_choices(records):
    actions = sorted({str(r.get("action", "")).strip() for r in records if str(r.get("action", "")).strip()})
    actors = sorted({str(r.get("actor_name", "")).strip() for r in records if str(r.get("actor_name", "")).strip()})
    teachers = sorted({str(r.get("target_teacher", "")).strip() for r in records if str(r.get("target_teacher", "")).strip()})
    return ["الكل"] + actions, ["الكل"] + actors, ["الكل"] + teachers


def render_audit_log_html(records, total_records=0, error_message=""):
    if error_message:
        return f"<div style='background:#ffebee;color:#b91c1c;border-right:5px solid #c62828;padding:12px;border-radius:10px;font-weight:800;'>{html_lib.escape(error_message)}</div>"

    if not records:
        return "<div style='background:#f8fafc;border:1px dashed #cbd5e1;border-radius:10px;padding:18px;text-align:center;color:#64748b;'>لا توجد عمليات مطابقة للعرض.</div>"

    display_records = records[:200]
    rows = []
    for record in display_records:
        values = {
            "timestamp": str(record.get("timestamp", "")),
            "actor_name": str(record.get("actor_name", "")),
            "actor_role": str(record.get("actor_role", "")),
            "action": str(record.get("action", "")),
            "target_teacher": str(record.get("target_teacher", "")),
            "old_value": _audit_value_to_text(record.get("old_value")),
            "new_value": _audit_value_to_text(record.get("new_value")),
            "details": str(record.get("details", "")),
        }
        safe = {key: html_lib.escape(value) for key, value in values.items()}
        rows.append(f"""
        <tr>
            <td>{safe['timestamp']}</td>
            <td>{safe['actor_name']}</td>
            <td>{safe['actor_role']}</td>
            <td><b>{safe['action']}</b></td>
            <td>{safe['target_teacher']}</td>
            <td class='audit-wide'>{safe['old_value']}</td>
            <td class='audit-wide'>{safe['new_value']}</td>
            <td class='audit-wide'>{safe['details']}</td>
        </tr>
        """)

    shown_note = f"يعرض آخر {len(display_records)} سجل من أصل {len(records)} سجل مطابق" if len(records) > 200 else f"عدد السجلات المطابقة: {len(records)}"
    return f"""
    <div style='direction:rtl;'>
        <div style='background:#e8f5e9;color:#004d40;border-right:5px solid #2e7d32;padding:11px 14px;border-radius:10px;margin-bottom:10px;font-weight:800;'>
            {shown_note} | إجمالي السجلات المحفوظة: {int(total_records)}
        </div>
        <div style='overflow-x:auto;border:1px solid #dbe3e8;border-radius:12px;'>
            <table style='width:100%;min-width:1450px;border-collapse:collapse;text-align:center;font-family:Cairo,Arial,sans-serif;font-size:13px;'>
                <thead>
                    <tr style='background:#004d40;color:white;'>
                        <th>التاريخ والوقت</th><th>المنفذ</th><th>الدور</th><th>العملية</th>
                        <th>المعلم المتأثر</th><th>القيمة القديمة</th><th>القيمة الجديدة</th><th>التفاصيل</th>
                    </tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
    </div>
    """


def refresh_audit_dashboard(action_filter, actor_filter, teacher_filter, date_from, date_to, is_owner=False):
    if not bool(is_owner):
        denied = "<div style='color:#b91c1c;background:#ffebee;padding:12px;border-radius:10px;font-weight:800;'>هذه الأداة متاحة لمالك النظام فقط.</div>"
        return (
            gr.update(choices=["الكل"], value="الكل"),
            gr.update(choices=["الكل"], value="الكل"),
            gr.update(choices=["الكل"], value="الكل"),
            gr.update(value=denied),
        )

    records = load_audit_log_records()
    action_choices, actor_choices, teacher_choices = _audit_filter_choices(records)

    action_value = action_filter if action_filter in action_choices else "الكل"
    actor_value = actor_filter if actor_filter in actor_choices else "الكل"
    teacher_value = teacher_filter if teacher_filter in teacher_choices else "الكل"

    filtered, error = filter_audit_records(records, action_value, actor_value, teacher_value, date_from, date_to)
    return (
        gr.update(choices=action_choices, value=action_value),
        gr.update(choices=actor_choices, value=actor_value),
        gr.update(choices=teacher_choices, value=teacher_value),
        gr.update(value=render_audit_log_html(filtered, len(records), error)),
    )


def export_audit_log_excel(action_filter, actor_filter, teacher_filter, date_from, date_to, is_owner=False):
    if not bool(is_owner):
        return (
            gr.update(value=None),
            "<div style='color:#b91c1c;font-weight:800;'>هذه العملية متاحة لمالك النظام فقط.</div>",
        )

    try:
        records = load_audit_log_records()
        filtered, error = filter_audit_records(
            records,
            action_filter,
            actor_filter,
            teacher_filter,
            date_from,
            date_to,
        )

        if error:
            return (
                gr.update(value=None),
                f"<div style='color:#b91c1c;font-weight:800;'>{html_lib.escape(error)}</div>",
            )

        if not filtered:
            return (
                gr.update(value=None),
                "<div style='color:#a16207;font-weight:800;'>لا توجد سجلات مطابقة لتصديرها.</div>",
            )

        ensure_data_directories()
        rows = []
        for record in filtered:
            rows.append({
                "التاريخ والوقت": str(record.get("timestamp", "")),
                "اسم المنفذ": str(record.get("actor_name", "")),
                "دور المنفذ": str(record.get("actor_role", "")),
                "نوع العملية": str(record.get("action", "")),
                "المعلم المتأثر": str(record.get("target_teacher", "")),
                "القيمة القديمة": _audit_value_to_text(record.get("old_value")),
                "القيمة الجديدة": _audit_value_to_text(record.get("new_value")),
                "التفاصيل": str(record.get("details", "")),
                "المصدر": str(record.get("source", "")),
            })

        filename = os.path.join(
            EXPORTS_DIR,
            f"سجل_العمليات_الحساسة_{get_now_oman().strftime('%Y%m%d_%H%M%S_%f')}.xlsx",
        )

        df = pd.DataFrame(rows)
        with pd.ExcelWriter(filename, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="سجل العمليات")
            ws = writer.sheets["سجل العمليات"]

            header_fill = PatternFill(fill_type="solid", fgColor="004D40")
            header_font = Font(color="FFFFFF", bold=True)
            center = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

            for cell in ws[1]:
                cell.fill = header_fill
                cell.font = header_font
                cell.alignment = center

            for row in ws.iter_rows(min_row=2):
                for cell in row:
                    cell.alignment = center

            for column_cells in ws.columns:
                max_length = max(
                    len(str(cell.value or ""))
                    for cell in column_cells
                )
                ws.column_dimensions[
                    column_cells[0].column_letter
                ].width = min(max(max_length + 3, 14), 55)

            ws.freeze_panes = "A2"
            ws.sheet_view.rightToLeft = True

        absolute_filename = os.path.abspath(filename)
        if not os.path.isfile(absolute_filename):
            raise FileNotFoundError(
                "تم إنشاء طلب التصدير لكن ملف Excel غير موجود في مسار المخرجات."
            )

        return (
            gr.update(value=absolute_filename),
            (
                "<div style='color:#166534;background:#dcfce7;padding:10px;"
                "border-radius:8px;font-weight:800;'>"
                f"تم تجهيز ملف Excel ويحتوي على {len(filtered)} سجل."
                "</div>"
            ),
        )

    except Exception as exc:
        print(f"export_audit_log_excel error: {exc}")
        return (
            gr.update(value=None),
            (
                "<div style='color:#b91c1c;background:#fee2e2;padding:10px;"
                "border-radius:8px;font-weight:800;'>"
                "تعذر تصدير السجل. التفاصيل التقنية: "
                f"{html_lib.escape(str(exc))}"
                "</div>"
            ),
        )


def _backup_files_for_target(file_path):
    ensure_data_directories()
    base = os.path.basename(str(file_path))
    stem, ext = os.path.splitext(base)
    if not ext:
        ext = ".json"
    prefix = f"{stem}_"
    candidates = []
    for name in os.listdir(BACKUPS_DIR):
        full_path = os.path.join(BACKUPS_DIR, name)
        if os.path.isfile(full_path) and name.startswith(prefix) and name.endswith(ext):
            candidates.append(full_path)
    candidates.sort(key=lambda path: os.path.getmtime(path), reverse=True)
    return candidates


def _format_file_size(size_bytes):
    try:
        size = float(size_bytes)
    except Exception:
        return "—"
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def get_monitored_backup_files():
    return [
        ("قاعدة بيانات المعلمين والأرصدة", DB_FILE),
        ("التوزيع اليومي", DAILY_DB_FILE),
        ("التبادلات الودية", SWAP_DB_FILE),
        ("سجل الإعفاءات", EXEMPTIONS_LOG_FILE),
        ("سجل العمليات الحساسة", AUDIT_LOG_FILE),
        ("إعدادات المدرسة", SCHOOL_CONFIG_FILE),
        ("حسابات الدخول المشفرة", AUTH_ACCOUNTS_FILE),
    ]


def render_backup_status_html(is_owner=False):
    if not bool(is_owner):
        return "<div style='color:#b91c1c;background:#ffebee;padding:12px;border-radius:10px;font-weight:800;'>هذه الأداة متاحة لمالك النظام فقط.</div>"

    rows = []
    total_backups = 0
    for label, source_path in get_monitored_backup_files():
        backups = _backup_files_for_target(source_path)
        total_backups += len(backups)
        source_exists = os.path.exists(source_path)
        latest = backups[0] if backups else None
        latest_name = os.path.basename(latest) if latest else "—"
        latest_at = datetime.datetime.fromtimestamp(os.path.getmtime(latest), tz=tz_oman).strftime("%Y-%m-%d %H:%M:%S") if latest else "—"
        latest_size = _format_file_size(os.path.getsize(latest)) if latest else "—"
        source_status = "موجود" if source_exists else "غير موجود"
        source_color = "#166534" if source_exists else "#b91c1c"
        rows.append(f"""
        <tr>
            <td><b>{html_lib.escape(label)}</b></td>
            <td style='color:{source_color};font-weight:800;'>{source_status}</td>
            <td>{len(backups)}</td>
            <td>{html_lib.escape(latest_name)}</td>
            <td>{latest_at}</td>
            <td>{latest_size}</td>
        </tr>
        """)

    if PERSISTENT_STORAGE_ACTIVE:
        storage_note = (
            "<div style='margin-top:10px;background:#dcfce7;color:#166534;padding:10px;"
            "border-radius:8px;border-right:4px solid #16a34a;'>"
            "✅ النسخ محفوظة داخل التخزين الدائم: "
            + html_lib.escape(DATA_DIR)
            + "</div>"
        )
    else:
        storage_note = (
            "<div style='margin-top:10px;background:#fff7ed;color:#9a3412;padding:10px;"
            "border-radius:8px;border-right:4px solid #ea580c;'>"
            "⚠️ التطبيق يعمل على التخزين المحلي الاحتياطي؛ تحقّق من تركيب الـBucket."
            "</div>"
        )

    return f"""
    <div style='direction:rtl;'>
        <div style='background:#e0f2fe;color:#0c4a6e;border-right:5px solid #0284c7;padding:11px 14px;border-radius:10px;margin-bottom:10px;font-weight:800;'>
            إجمالي النسخ الاحتياطية المحلية: {total_backups}
        </div>
        <div style='overflow-x:auto;border:1px solid #dbe3e8;border-radius:12px;'>
            <table style='width:100%;min-width:1050px;border-collapse:collapse;text-align:center;font-family:Cairo,Arial,sans-serif;font-size:13px;'>
                <thead><tr style='background:#0f766e;color:white;'>
                    <th>الملف</th><th>حالة الأصل</th><th>عدد النسخ</th><th>أحدث نسخة</th><th>تاريخها</th><th>حجمها</th>
                </tr></thead>
                <tbody>{''.join(rows)}</tbody>
            </table>
        </div>
        {storage_note}
    </div>
    """


def refresh_backup_status(is_owner=False):
    return gr.update(value=render_backup_status_html(is_owner))


def _latest_backup_for(file_path):
    candidates = _backup_files_for_target(file_path)
    return candidates[0] if candidates else None


def create_backup_bundle(is_owner=False):
    if not bool(is_owner):
        return gr.update(value=None), "<div style='color:#b91c1c;font-weight:800;'>هذه العملية متاحة لمالك النظام فقط.</div>"

    ensure_data_directories()
    filename = os.path.join(EXPORTS_DIR, f"نسخة_احتياطية_منظومة_مسار_{get_now_oman().strftime('%Y%m%d_%H%M%S_%f')}.zip")
    manifest = {
        "created_at": get_now_oman().strftime("%Y-%m-%d %H:%M:%S"),
        "school_name": SCHOOL_NAME,
        "system_name": SYSTEM_NAME,
        "contents": [],
    }

    current_files = [path for _label, path in get_monitored_backup_files()]
    reference_files = [
        ADMIN_FILE,
        PHONES_FILE,
        REFERENCE_STATUS_FILE,
    ] + list(SCHEDULE_FILES.values())

    with STATE_LOCK:
        try:
            with zipfile.ZipFile(filename, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for path in current_files:
                    if os.path.exists(path) and os.path.isfile(path):
                        arcname = os.path.join("current", os.path.basename(path))
                        zf.write(path, arcname=arcname)
                        manifest["contents"].append(arcname)

                    latest_backup = _latest_backup_for(path)
                    if latest_backup:
                        arcname = os.path.join("latest_backups", os.path.basename(latest_backup))
                        zf.write(latest_backup, arcname=arcname)
                        manifest["contents"].append(arcname)

                for path in reference_files:
                    if os.path.exists(path) and os.path.isfile(path):
                        arcname = os.path.join("reference_files", os.path.basename(path))
                        zf.write(path, arcname=arcname)
                        manifest["contents"].append(arcname)

                zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

            return gr.update(value=os.path.abspath(filename)), "<div style='color:#166534;background:#dcfce7;padding:10px;border-radius:8px;font-weight:800;'>تم تجهيز النسخة الاحتياطية المضغوطة بنجاح.</div>"
        except Exception as e:
            print(f"create_backup_bundle error: {e}")
            try:
                if os.path.exists(filename):
                    os.remove(filename)
            except Exception:
                pass
            return gr.update(value=None), f"<div style='color:#b91c1c;font-weight:800;'>تعذر تجهيز النسخة الاحتياطية: {html_lib.escape(str(e))}</div>"


def refresh_owner_tools_dashboard(action_filter, actor_filter, teacher_filter, date_from, date_to, is_owner=False):
    action_upd, actor_upd, teacher_upd, audit_upd = refresh_audit_dashboard(
        action_filter, actor_filter, teacher_filter, date_from, date_to, is_owner
    )
    backup_upd = refresh_backup_status(is_owner)
    return action_upd, actor_upd, teacher_upd, audit_upd, backup_upd



def refresh_school_data_center_cards(is_owner=False):
    """
    إعادة قراءة حالة التخزين والملفات المرجعية عند كل دخول إلى مركز البيانات.
    يمنع رجوع البطاقات إلى القيم الحمراء الابتدائية بعد الخروج أو تحديث الصفحة.
    """
    if not bool(is_owner):
        denied = gr.update()
        return denied, denied, denied, denied, denied

    return (
        gr.update(value=render_persistent_storage_status_html()),
        gr.update(value=render_school_config_summary_html()),
        gr.update(value=render_admin_reference_card()),
        gr.update(value=render_phones_reference_card()),
        gr.update(value=render_schedule_reference_cards()),
    )


# ─────────────────────────────────────────────────────────────────────────────
# v1.8 — إعداد هوية المدرسة من الواجهة (مالك النظام فقط)
# ─────────────────────────────────────────────────────────────────────────────

IDENTITY_CONFIG_KEYS = (
    "school_name",
    "directorate_region",
    "logo_url",
    "theme_color",
    "theme_color_2",
    "accent_color",
)

FIXED_IDENTITY_KEYS = (
    "ministry_name",
    "directorate_prefix",
    "system_name",
    "system_subtitle",
    "developer_credit",
)

def _normalize_identity_text(value, fallback="", max_length=220):
    cleaned = re.sub(r"\s+", " ", str(value or "").strip())
    if not cleaned:
        cleaned = str(fallback or "").strip()
    return cleaned[:max_length]

def _normalize_hex_color(value, fallback):
    raw = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", raw):
        return raw.lower()
    fallback_raw = str(fallback or "#004d40").strip()
    return fallback_raw.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", fallback_raw) else "#004d40"

def _is_valid_identity_logo_value(value):
    raw = str(value or "").strip()
    if not raw:
        return False
    if raw.startswith("data:image/"):
        return True
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return True
    path = os.path.abspath(raw)
    return os.path.isfile(path)

def _resolve_identity_logo_source(value=None):
    raw = str(value if value is not None else SCHOOL_LOGO_URL).strip()
    if not raw:
        raw = str(DEFAULT_SCHOOL_CONFIG["logo_url"])

    if raw.startswith(("http://", "https://", "data:image/")):
        return raw

    candidate = os.path.abspath(raw)
    if not os.path.isfile(candidate):
        return str(DEFAULT_SCHOOL_CONFIG["logo_url"])

    mime_type = mimetypes.guess_type(candidate)[0] or "image/png"
    try:
        encoded = base64.b64encode(Path(candidate).read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"
    except Exception:
        return str(DEFAULT_SCHOOL_CONFIG["logo_url"])

def _save_uploaded_identity_logo(uploaded_file):
    if uploaded_file is None:
        return None

    source_path = getattr(uploaded_file, "name", uploaded_file)
    source_path = str(source_path or "").strip()
    if not source_path or not os.path.isfile(source_path):
        raise ValueError("ملف الشعار المرفوع غير صالح.")

    try:
        with Image.open(source_path) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("الملف المرفوع ليس صورة صالحة.") from exc

    ext = Path(source_path).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        ext = ".png"

    ensure_data_directories()
    destination = os.path.join(BRANDING_DIR, f"school_logo{ext}")
    shutil.copy2(source_path, destination)
    return os.path.relpath(destination, os.getcwd())

def _identity_directorate_full_name(region=None):
    region_clean = _normalize_identity_text(
        region,
        DEFAULT_SCHOOL_CONFIG["directorate_region"],
        80,
    )
    return f"{DEFAULT_SCHOOL_CONFIG['directorate_prefix']} {region_clean}".strip()

def _apply_school_identity_globals(config):
    global SCHOOL_CONFIG
    global MINISTRY_NAME, DIRECTORATE_PREFIX, DIRECTORATE_REGION, DIRECTORATE_FULL_NAME
    global SYSTEM_NAME, SYSTEM_SUBTITLE, SCHOOL_NAME, DEVELOPER_CREDIT
    global SCHOOL_LOGO_URL, THEME_COLOR, THEME_COLOR_2, ACCENT_COLOR

    SCHOOL_CONFIG = dict(config)

    MINISTRY_NAME = str(DEFAULT_SCHOOL_CONFIG["ministry_name"])
    DIRECTORATE_PREFIX = str(DEFAULT_SCHOOL_CONFIG["directorate_prefix"])
    SYSTEM_NAME = str(DEFAULT_SCHOOL_CONFIG["system_name"])
    SYSTEM_SUBTITLE = str(DEFAULT_SCHOOL_CONFIG["system_subtitle"])
    DEVELOPER_CREDIT = str(DEFAULT_SCHOOL_CONFIG["developer_credit"])

    DIRECTORATE_REGION = _normalize_identity_text(
        SCHOOL_CONFIG.get("directorate_region"),
        DEFAULT_SCHOOL_CONFIG["directorate_region"],
        80,
    )
    DIRECTORATE_FULL_NAME = _identity_directorate_full_name(DIRECTORATE_REGION)

    SCHOOL_NAME = _normalize_identity_text(
        SCHOOL_CONFIG.get("school_name"),
        DEFAULT_SCHOOL_CONFIG["school_name"],
        140,
    )
    SCHOOL_LOGO_URL = str(
        SCHOOL_CONFIG.get("logo_url") or DEFAULT_SCHOOL_CONFIG["logo_url"]
    ).strip()
    THEME_COLOR = _normalize_hex_color(
        SCHOOL_CONFIG.get("theme_color"),
        DEFAULT_SCHOOL_CONFIG["theme_color"],
    )
    THEME_COLOR_2 = _normalize_hex_color(
        SCHOOL_CONFIG.get("theme_color_2"),
        DEFAULT_SCHOOL_CONFIG["theme_color_2"],
    )
    ACCENT_COLOR = _normalize_hex_color(
        SCHOOL_CONFIG.get("accent_color"),
        DEFAULT_SCHOOL_CONFIG["accent_color"],
    )

def _current_identity_config():
    return {
        "ministry_name": MINISTRY_NAME,
        "directorate_prefix": DIRECTORATE_PREFIX,
        "directorate_region": DIRECTORATE_REGION,
        "directorate_full_name": DIRECTORATE_FULL_NAME,
        "system_name": SYSTEM_NAME,
        "system_subtitle": SYSTEM_SUBTITLE,
        "school_name": SCHOOL_NAME,
        "developer_credit": DEVELOPER_CREDIT,
        "logo_url": SCHOOL_LOGO_URL,
        "theme_color": THEME_COLOR,
        "theme_color_2": THEME_COLOR_2,
        "accent_color": ACCENT_COLOR,
    }

def build_login_branding_html(config=None):
    cfg = dict(_current_identity_config())
    if isinstance(config, dict):
        cfg.update(config)

    system_name = html_lib.escape(str(cfg.get("system_name", SYSTEM_NAME)))
    school_name = html_lib.escape(str(cfg.get("school_name", SCHOOL_NAME)))
    logo_src = html_lib.escape(
        _resolve_identity_logo_source(cfg.get("logo_url")),
        quote=True,
    )
    theme = _normalize_hex_color(cfg.get("theme_color"), THEME_COLOR)
    theme_2 = _normalize_hex_color(cfg.get("theme_color_2"), THEME_COLOR_2)
    accent = _normalize_hex_color(cfg.get("accent_color"), ACCENT_COLOR)

    return f"""
<div style="
    background:linear-gradient(145deg,#003d33 0%,{theme} 40%,{theme_2} 80%,{theme} 100%);
    margin:0px 0px 20px 0px;
    padding:30px 20px 25px;
    padding-bottom:0 !important;
    overflow:hidden;
    border-radius:16px 16px 0 0;
    text-align:center;
    border-bottom:none;
">
    <img id="main-logo" src="{logo_src}" alt="{system_name}" style="
        width:115px;height:115px;
        border-radius:50%;
        border:3px solid {accent};
        background:white;
        padding:3px;
        display:inline-block;
        margin-bottom:14px;
        object-fit:contain;
        box-shadow:
            0 15px 40px rgba(0,0,0,0.6),
            0 6px 15px rgba(0,0,0,0.4),
            0 0 0 5px rgba(255,202,40,0.3),
            0 0 0 10px rgba(0,77,64,0.15),
            4px -4px 15px rgba(255,255,255,0.2),
            -4px 4px 15px rgba(0,0,0,0.3);
        animation:logo4d 4s ease-in-out infinite;
        cursor:pointer;
    ">
    <div style="font-size:26px;font-weight:900;color:{accent};text-shadow:0 2px 8px rgba(0,0,0,0.4);margin-bottom:6px;">
        بوابة الدخول
    </div>
    <div style="font-size:13px;color:rgba(255,255,255,0.92);font-weight:700;">
        {school_name}
    </div>
    <div style="font-size:11px;color:rgba(255,255,255,0.78);font-weight:600;margin-top:4px;">
        {system_name}
    </div>

    <div style="margin-bottom:-4px;line-height:0;overflow:hidden;margin-left:-28px;margin-right:-28px;">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2880 60" preserveAspectRatio="none" style="display:block;width:200%;height:15px;animation:waveMove 4s linear infinite;">
            <path fill="{accent}" fill-opacity="1" d="M-10,35 C180,5 360,55 540,25 C720,0 900,50 1080,20 C1260,-5 1400,45 1450,30 C1620,5 1800,55 1980,25 C2160,0 2340,50 2520,20 C2700,-5 2840,45 2890,30 L2890,65 L-10,65 Z"/>
        </svg>
    </div>
</div>
"""

def build_login_credits_html(config=None):
    cfg = dict(_current_identity_config())
    if isinstance(config, dict):
        cfg.update(config)
    credit = html_lib.escape(str(cfg.get("developer_credit", DEVELOPER_CREDIT)))
    return (
        "<div style='text-align:center;'>"
        f"<div class='credits-box' style='font-size:10px;padding:5px 10px;'>{credit}</div>"
        "</div>"
    )

def build_header_html(config=None):
    cfg = dict(_current_identity_config())
    if isinstance(config, dict):
        cfg.update(config)

    system_name = html_lib.escape(str(DEFAULT_SCHOOL_CONFIG["system_name"]))
    subtitle = html_lib.escape(str(DEFAULT_SCHOOL_CONFIG["system_subtitle"]))
    school_name = html_lib.escape(str(cfg.get("school_name", SCHOOL_NAME)))
    credit = html_lib.escape(str(DEFAULT_SCHOOL_CONFIG["developer_credit"]))
    ministry = html_lib.escape(str(DEFAULT_SCHOOL_CONFIG["ministry_name"]))
    directorate = html_lib.escape(
        _identity_directorate_full_name(
            cfg.get("directorate_region", DIRECTORATE_REGION)
        )
    )
    logo_src = html_lib.escape(
        _resolve_identity_logo_source(cfg.get("logo_url")),
        quote=True,
    )
    accent = _normalize_hex_color(cfg.get("accent_color"), ACCENT_COLOR)

    return f"""
<div class='main-header'>
    <div class='header-grid'>
        <div class='h-logo'><img src='{logo_src}' alt='Logo'></div>
        <div class='h-ministry'>{ministry}<br>{directorate}</div>
        <div class='h-title'>
            <div class='h-title-main'>{system_name}</div>
            <div class='h-title-sub'>{subtitle}</div>
        </div>
        <div class='h-school' style='color:{accent} !important;-webkit-text-fill-color:{accent} !important;white-space:nowrap;'>{school_name}</div>
        <div class='h-credits'><div class='credits-box'>{credit}</div></div>
    </div>
</div>
"""

def _home_hero_account_note(record):
    context = _account_profile_context(record)
    role = context.get("role", "")
    official = context.get("official_title", "")
    department_label = context.get("department_label", "")

    if bool(record.get("is_owner", False)) or role == OWNER_ROLE:
        return "لوحة التحكم العليا جاهزة لإدارة المنظومة."

    if official and department_label:
        return f"{official} — {department_label}"

    if official:
        return f"صلاحية الدخول: {official}"

    if department_label:
        return f"{department_label} جاهز لك."

    return "تم تجهيز لوحة العمل حسب صلاحيتك."


def _resolve_home_hero_account_record(
    account_id="",
    user_name="",
    user_role="",
    is_owner=False,
):
    account_id = str(account_id or "").strip()

    if bool(is_owner) or account_id == OWNER_ACCOUNT_ID:
        owner_name = (
            str(user_name or "").strip()
            or os.getenv("SYSTEM_OWNER_NAME", "صاحب النظام").strip()
            or "صاحب النظام"
        )
        return {
            "account_id": OWNER_ACCOUNT_ID,
            "name": owner_name,
            "display_name": owner_name,
            "role": OWNER_ROLE,
            "official_title": OWNER_ROLE,
            "whatsapp_title": OWNER_ROLE,
            "department_label": "غرفة القيادة",
            "welcome_title": "صاحب النظام",
            "welcome_phrase": "لوحة التحكم العليا جاهزة لإدارة المنظومة",
            "welcome_template": "مرحبًا بك يا {welcome_title} ({display_name})",
            "is_owner": True,
        }

    if account_id:
        payload = load_auth_accounts()
        record = payload.get("accounts", {}).get(account_id)
        if isinstance(record, dict):
            return dict(record)

    fallback_name = str(user_name or "").strip()
    fallback_role = str(user_role or "").strip()
    if fallback_name or fallback_role:
        return {
            "account_id": account_id,
            "name": fallback_name,
            "display_name": fallback_name,
            "role": fallback_role,
            "official_title": fallback_role,
            "whatsapp_title": fallback_role,
            "department_label": "",
            "welcome_title": "",
            "welcome_phrase": "",
            "welcome_template": "",
            "is_owner": False,
        }

    return {}


def build_home_hero_html(config=None, account_record=None):
    cfg = dict(_current_identity_config())
    if isinstance(config, dict):
        cfg.update(config)

    system_name = html_lib.escape(str(cfg.get("system_name", SYSTEM_NAME)))
    school_name = html_lib.escape(str(cfg.get("school_name", SCHOOL_NAME)))

    if isinstance(account_record, dict) and account_record:
        title_text = build_account_welcome_text(account_record)
        note_text = _home_hero_account_note(account_record)
    else:
        title_text = f"مرحبًا بك في {system_name}"
        note_text = "تم تجهيز لوحة العمل حسب صلاحيتك. اختر القسم المناسب للبدء."

    title_html = html_lib.escape(str(title_text or f"مرحبًا بك في {system_name}"))
    note_html = html_lib.escape(str(note_text or ""))

    return f"""
<div class='masar-home-hero'>
    <div class='masar-home-title'>{title_html}</div>
    <div class='masar-home-subtitle'>{school_name}</div>
    <div class='masar-home-note'>{note_html}</div>
</div>
"""


def update_home_hero_after_login(
    account_id,
    user_name,
    user_role,
    is_owner=False,
):
    record = _resolve_home_hero_account_record(
        account_id=account_id,
        user_name=user_name,
        user_role=user_role,
        is_owner=is_owner,
    )
    return gr.update(value=build_home_hero_html(account_record=record))


def render_school_config_summary_html(config=None):
    cfg = dict(_current_identity_config())
    if isinstance(config, dict):
        cfg.update(config)

    directorate = _identity_directorate_full_name(
        cfg.get("directorate_region", DIRECTORATE_REGION)
    )

    return f"""
<div style='background:#fffde7;color:#4d3b00;padding:12px;border-radius:10px;border-right:5px solid {html_lib.escape(str(cfg.get("accent_color", ACCENT_COLOR)))};margin-bottom:12px;font-weight:800;line-height:1.8;'>
    ملف إعدادات المدرسة: <b>{html_lib.escape(SCHOOL_CONFIG_FILE)}</b><br>
    المدرسة الحالية: <b>{html_lib.escape(str(cfg.get("school_name", SCHOOL_NAME)))}</b><br>
    الوزارة: <b>{html_lib.escape(DEFAULT_SCHOOL_CONFIG["ministry_name"])}</b><br>
    المديرية: <b>{html_lib.escape(directorate)}</b><br>
    اسم النظام: <b>{html_lib.escape(DEFAULT_SCHOOL_CONFIG["system_name"])} - {html_lib.escape(DEFAULT_SCHOOL_CONFIG["system_subtitle"])}</b><br>
    عدد الحصص اليومية: <b>{MAX_PERIODS}</b>
</div>
"""

def render_school_identity_preview_html(
    system_name,
    system_subtitle,
    school_name,
    developer_credit,
    logo_url,
    theme_color,
    theme_color_2,
    accent_color,
    directorate_region=None,
):
    preview_cfg = {
        "system_name": DEFAULT_SCHOOL_CONFIG["system_name"],
        "system_subtitle": DEFAULT_SCHOOL_CONFIG["system_subtitle"],
        "ministry_name": DEFAULT_SCHOOL_CONFIG["ministry_name"],
        "directorate_region": _normalize_identity_text(
            directorate_region if directorate_region is not None else DIRECTORATE_REGION,
            DEFAULT_SCHOOL_CONFIG["directorate_region"],
            80,
        ),
        "school_name": _normalize_identity_text(
            school_name, DEFAULT_SCHOOL_CONFIG["school_name"], 140
        ),
        "developer_credit": DEFAULT_SCHOOL_CONFIG["developer_credit"],
        "logo_url": str(logo_url or SCHOOL_LOGO_URL).strip(),
        "theme_color": _normalize_hex_color(
            theme_color, DEFAULT_SCHOOL_CONFIG["theme_color"]
        ),
        "theme_color_2": _normalize_hex_color(
            theme_color_2, DEFAULT_SCHOOL_CONFIG["theme_color_2"]
        ),
        "accent_color": _normalize_hex_color(
            accent_color, DEFAULT_SCHOOL_CONFIG["accent_color"]
        ),
    }

    directorate_full = _identity_directorate_full_name(
        preview_cfg["directorate_region"]
    )
    logo_src = html_lib.escape(
        _resolve_identity_logo_source(preview_cfg["logo_url"]),
        quote=True,
    )
    return f"""
<div style='direction:rtl;border:1px solid #d1d5db;border-radius:16px;overflow:hidden;background:#ffffff;box-shadow:0 8px 22px rgba(0,0,0,0.08);'>
    <div style='background:linear-gradient(145deg,#003d33 0%,{preview_cfg["theme_color"]} 45%,{preview_cfg["theme_color_2"]} 100%);padding:22px;text-align:center;'>
        <div style='font-size:13px;font-weight:800;color:rgba(255,255,255,0.92);margin-bottom:6px;'>{html_lib.escape(preview_cfg["ministry_name"])}</div>
        <div style='font-size:12px;font-weight:700;color:rgba(255,255,255,0.86);margin-bottom:12px;'>{html_lib.escape(directorate_full)}</div>
        <img src='{logo_src}' style='width:92px;height:92px;object-fit:contain;border-radius:50%;background:#fff;padding:4px;border:3px solid {preview_cfg["accent_color"]};'>
        <div style='font-size:24px;font-weight:900;color:{preview_cfg["accent_color"]};margin-top:12px;'>{html_lib.escape(preview_cfg["system_name"])}</div>
        <div style='font-size:14px;font-weight:700;color:#fff;margin-top:4px;'>{html_lib.escape(preview_cfg["system_subtitle"])}</div>
        <div style='font-size:13px;font-weight:700;color:rgba(255,255,255,0.9);margin-top:8px;'>{html_lib.escape(preview_cfg["school_name"])}</div>
    </div>
    <div style='padding:12px;text-align:center;color:#334155;font-weight:700;'>{html_lib.escape(preview_cfg["developer_credit"])}</div>
</div>
"""

def preview_school_identity_settings(
    system_name,
    system_subtitle,
    school_name,
    developer_credit,
    directorate_region,
    logo_url,
    theme_color,
    theme_color_2,
    accent_color,
    is_owner=False,
):
    if not bool(is_owner):
        return (
            gr.update(),
            "<div style='color:#b91c1c;font-weight:800;'>هذه الأداة مخصصة لمالك النظام فقط.</div>",
        )

    preview = render_school_identity_preview_html(
        DEFAULT_SCHOOL_CONFIG["system_name"],
        DEFAULT_SCHOOL_CONFIG["system_subtitle"],
        school_name,
        DEFAULT_SCHOOL_CONFIG["developer_credit"],
        logo_url,
        theme_color,
        theme_color_2,
        accent_color,
        directorate_region,
    )
    return (
        gr.update(value=preview),
        "<div style='color:#0f766e;font-weight:800;'>تم تحديث المعاينة فقط، ولم تُحفظ التغييرات بعد.</div>",
    )

def _identity_full_output(config, status_html):
    preview = render_school_identity_preview_html(
        DEFAULT_SCHOOL_CONFIG["system_name"],
        DEFAULT_SCHOOL_CONFIG["system_subtitle"],
        config["school_name"],
        DEFAULT_SCHOOL_CONFIG["developer_credit"],
        config["logo_url"],
        config["theme_color"],
        config["theme_color_2"],
        config["accent_color"],
        config.get("directorate_region", DEFAULT_SCHOOL_CONFIG["directorate_region"]),
    )
    return (
        gr.update(value=DEFAULT_SCHOOL_CONFIG["system_name"]),
        gr.update(value=DEFAULT_SCHOOL_CONFIG["system_subtitle"]),
        gr.update(value=config["school_name"]),
        gr.update(value=config.get("directorate_region", DEFAULT_SCHOOL_CONFIG["directorate_region"])),
        gr.update(value=DEFAULT_SCHOOL_CONFIG["developer_credit"]),
        gr.update(value=config["logo_url"]),
        gr.update(value=None),
        gr.update(value=config["theme_color"]),
        gr.update(value=config["theme_color_2"]),
        gr.update(value=config["accent_color"]),
        gr.update(value=status_html),
        gr.update(value=preview),
        gr.update(value=build_login_branding_html(config)),
        gr.update(value=build_login_credits_html(config)),
        gr.update(value=build_header_html(config)),
        gr.update(value=build_home_hero_html(config)),
        gr.update(value=render_school_config_summary_html(config)),
    )

@state_locked
def save_school_identity_settings(
    system_name,
    system_subtitle,
    school_name,
    developer_credit,
    directorate_region,
    logo_url,
    logo_upload,
    theme_color,
    theme_color_2,
    accent_color,
    is_owner=False,
):
    if not bool(is_owner):
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رفض الحفظ: إعدادات الهوية مخصصة لمالك النظام فقط.</div>",
        )

    school_name_clean = _normalize_identity_text(school_name, "", 140)
    directorate_region_clean = _normalize_identity_text(
        directorate_region,
        DEFAULT_SCHOOL_CONFIG["directorate_region"],
        80,
    )
    if not school_name_clean:
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>اسم المدرسة حقل إلزامي.</div>",
        )

    colors = {
        "theme_color": str(theme_color or "").strip(),
        "theme_color_2": str(theme_color_2 or "").strip(),
        "accent_color": str(accent_color or "").strip(),
    }
    invalid_colors = [
        key for key, value in colors.items()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value)
    ]
    if invalid_colors:
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>ألوان الهوية يجب أن تكون بصيغة HEX مثل #004d40.</div>",
        )

    saved_logo_value = str(logo_url or "").strip()
    try:
        uploaded_logo = _save_uploaded_identity_logo(logo_upload)
        if uploaded_logo:
            saved_logo_value = uploaded_logo
    except Exception as exc:
        return _identity_full_output(
            _current_identity_config(),
            f"<div style='color:#b91c1c;font-weight:800;'>{html_lib.escape(str(exc))}</div>",
        )

    if not saved_logo_value:
        saved_logo_value = str(DEFAULT_SCHOOL_CONFIG["logo_url"])

    if not _is_valid_identity_logo_value(saved_logo_value):
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رابط أو ملف الشعار غير صالح.</div>",
        )

    new_config = load_school_config()
    for fixed_key in FIXED_IDENTITY_KEYS:
        new_config[fixed_key] = DEFAULT_SCHOOL_CONFIG[fixed_key]

    new_config.update({
        "school_name": school_name_clean,
        "directorate_region": directorate_region_clean,
        "logo_url": saved_logo_value,
        "theme_color": colors["theme_color"].lower(),
        "theme_color_2": colors["theme_color_2"].lower(),
        "accent_color": colors["accent_color"].lower(),
    })

    if not safe_write_json(SCHOOL_CONFIG_FILE, new_config):
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر حفظ ملف إعدادات المدرسة.</div>",
        )

    _apply_school_identity_globals(new_config)
    return _identity_full_output(
        _current_identity_config(),
        "<div style='color:#166534;background:#dcfce7;padding:10px;border-radius:8px;font-weight:800;'>تم حفظ هوية المدرسة بنجاح. العناصر الثابتة بقيت كما هي، وتغيرت المدرسة والمحافظة والشعار والألوان فقط.</div>",
    )

@state_locked
def reset_school_identity_settings(is_owner=False):
    if not bool(is_owner):
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>رفض الاستعادة: هذه الأداة مخصصة لمالك النظام فقط.</div>",
        )

    config = load_school_config()

    for key in FIXED_IDENTITY_KEYS:
        config[key] = DEFAULT_SCHOOL_CONFIG[key]
    for key in IDENTITY_CONFIG_KEYS:
        config[key] = DEFAULT_SCHOOL_CONFIG[key]

    if not safe_write_json(SCHOOL_CONFIG_FILE, config):
        return _identity_full_output(
            _current_identity_config(),
            "<div style='color:#b91c1c;font-weight:800;'>تعذر استعادة الهوية الافتراضية.</div>",
        )

    _apply_school_identity_globals(config)
    return _identity_full_output(
        _current_identity_config(),
        "<div style='color:#166534;background:#dcfce7;padding:10px;border-radius:8px;font-weight:800;'>تمت استعادة الهوية الافتراضية. تُطبق الألوان العامة بالكامل بعد إعادة تشغيل التطبيق.</div>",
    )


def load_exemptions_log():
    global exemptions_log
    if os.path.exists(EXEMPTIONS_LOG_FILE):
        try:
            with open(EXEMPTIONS_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                exemptions_log = data if isinstance(data, list) else []
        except Exception as e:
            print(f"load_exemptions_log error: {e}")
            exemptions_log = []
    else:
        exemptions_log = []

def render_exemptions_log_html():
    active_rows = []

    for teacher_name, info in teachers_db.items():
        if info.get("dept") == "الهيئة الإدارية" or info.get("role", "معلم") in ADMIN_ROLES:
            continue
        days = info.get("exempt_days", []) or []
        periods = info.get("exempt_periods", []) or []

        clean_days = [str(d).strip() for d in days if str(d).strip()]
        clean_periods = []
        for p in periods:
            try:
                clean_periods.append(int(p))
            except Exception:
                if str(p).strip():
                    clean_periods.append(str(p).strip())

        if not clean_days and not clean_periods:
            continue

        active_rows.append({
            "teacher": teacher_name,
            "dept": info.get("dept", "—"),
            "days": clean_days,
            "periods": clean_periods,
            "updated_at": info.get("exemption_updated_at", "محفوظ")
        })

    if not active_rows:
        return "<div style='background:#f8fafc; border:1px dashed #cbd5e1; border-radius:10px; padding:14px; text-align:center; color:#475569;'>🗂️ لا يوجد سجل إعفاءات محفوظ حتى الآن.</div>"

    active_rows.sort(key=lambda item: (str(item.get("dept", "")), str(item.get("teacher", ""))))

    rows_html = ""
    for item in active_rows:
        teacher_name = format_teacher_name(str(item.get("teacher", "")).strip()) if str(item.get("teacher", "")).strip() else "—"
        dept = str(item.get("dept", "—")).strip() or "—"
        days = item.get("days", [])
        periods = item.get("periods", [])
        updated_at = str(item.get("updated_at", "محفوظ")).strip() or "محفوظ"
        days_text = "، ".join(days) if days else "—"
        periods_text = "، ".join(str(p) for p in periods) if periods else "—"
        rows_html += f"""
        <tr>
            <td style='padding:8px; border:1px solid #d1d5db;'>{teacher_name}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{dept}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{days_text}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{periods_text}</td>
            <td style='padding:8px; border:1px solid #d1d5db;'>{updated_at}</td>
        </tr>
        """

    return f"""
    <div style='margin-top:14px; background:#f8fafc; border:1px solid #dbeafe; border-radius:12px; padding:14px;'>
        <div style='font-weight:bold; color:#0f172a; margin-bottom:10px;'>🗂️ سجل حالات الإعفاء الحالية</div>
        <div style='overflow-x:auto;'>
            <table style='width:100%; border-collapse:collapse; text-align:center; direction:rtl; font-size:14px;'>
                <thead>
                    <tr style='background:#0f766e; color:#ffffff;'>
                        <th style='padding:9px; border:1px solid #d1d5db;'>المعلم</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>القسم</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>الأيام</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>الحصص</th>
                        <th style='padding:9px; border:1px solid #d1d5db;'>آخر تحديث</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
    """

load_exemptions_log()


def save_daily_db():
    payload = {
        "daily": daily_db,
        "processed": [list(x) if isinstance(x, tuple) else x for x in processed_absences]
    }
    if not safe_write_json(DAILY_DB_FILE, payload):
        print("save_daily_db error: safe_write_json failed")
        
def load_daily_db():
    global daily_db, processed_absences

    daily_db = []
    processed_absences = set()

    if not os.path.exists(DAILY_DB_FILE):
        return

    try:
        with open(DAILY_DB_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            daily_db = data
            processed_absences = set()
        else:
            daily_db = data.get("daily", [])
            processed_raw = data.get("processed", [])
            processed_absences = set(
                tuple(x) for x in processed_raw if isinstance(x, (list, tuple))
            )

    except Exception as e:
        print(f"load_daily_db error: {e}")
        daily_db = []
        processed_absences = set()
load_daily_db()
SWAP_EMPTY_MSG = "💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا..."
swap_db = {}

def load_swap_db():
    global swap_db
    if os.path.exists(SWAP_DB_FILE):
        try:
            with open(SWAP_DB_FILE, "r", encoding="utf-8") as f:
                swap_db = json.load(f)
        except Exception:
            swap_db = {}
    else:
        swap_db = {}

def save_swap_db():
    if not safe_write_json(SWAP_DB_FILE, swap_db):
        print("save_swap_db error: safe_write_json failed")

load_swap_db()
os.makedirs(SWAP_IMG_DIR, exist_ok=True)

def sync_current_school_days():
    current_day = get_current_day_oman()
    return gr.update(value=current_day), gr.update(value=current_day)

def build_swap_button_html(candidate_name, message_text):
    phone = teachers_db.get(candidate_name, {}).get("phone", "")
    btn_color = "#25D366"

    if phone:
        phone = "".join(filter(str.isdigit, str(phone)))
        if len(phone) == 8:
            phone = "968" + phone
        btn_text = f"✅ إرسال للأستاذ {candidate_name}"
    else:
        phone = ""
        btn_text = f"⚠️ إرسال (لا يوجد رقم)"

    encoded_msg = urllib.parse.quote(message_text)
    wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}" if phone else f"https://api.whatsapp.com/send?text={encoded_msg}"

    return (
        f'<div style="margin-top: 10px; border: 2px solid {btn_color}; border-radius: 8px; padding: 2px;">'
        f'<a href="{wa_link}" target="_blank" '
        f'style="display: block; width: 100%; text-align: center; background-color: {btn_color}; color: white; '
        f'padding: 12px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 16px;">'
        f'{btn_text}</a></div>'
    )

def extract_swap_choice_details(choice):
    candidate = ""
    comp_day = "يحدد لاحقاً"
    comp_period = "يحدد لاحقاً"

    try:
        parts = choice.split("|", 2)

        if len(parts) > 1 and ":" in parts[1]:
            candidate = parts[1].split(":", 1)[1].strip()
        else:
            candidate = str(choice).strip()

        details = parts[2].strip() if len(parts) > 2 else ""

        if "وتغطيه " in details:
            rep_part = details.split("وتغطيه ", 1)[1].split(")", 1)[0].replace("(", "")
            rep_day, rep_period = rep_part.split(" ح", 1)
            comp_day = rep_day.strip()
            comp_period = f"الحصة {rep_period.strip()}"

    except Exception:
        candidate = str(choice).strip()

    return candidate, comp_day, comp_period

def render_swap_table_html(state):
    if not isinstance(state, dict) or not state:
        return """
        <div style='background:#f8fafc; border:1px dashed #cbd5e1; border-radius:10px; padding:14px; text-align:center; color:#64748b; direction:rtl;'>
            لا توجد تبادلات معتمدة بعد.
        </div>
        """

    rows_html = ""
    for p, info in sorted(state.items(), key=lambda x: int(x[0])):
        rows_html += f"""
        <tr>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('requester', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('class', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>الحصة {p}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('candidate', '')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('comp_day', 'يحدد لاحقاً')}</td>
            <td style='padding:12px; border:1px solid #d1d5db;'>{info.get('comp_period', 'يحدد لاحقاً')}</td>
        </tr>
        """

    return f"""
    <div style='overflow-x:auto; direction:rtl; margin-top:12px;'>
        <table style='width:100%; min-width:900px; border-collapse:collapse; text-align:center; font-family:Cairo, Arial, sans-serif;'>
            <thead>
                <tr style='background:#e8f5e9; color:#0f5132;'>
                    <th style='padding:12px; border:1px solid #d1d5db;'>المعلم الطالب للتبادل</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>الصف</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>الحصة</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>المعلم البديل</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>يوم التعويض</th>
                    <th style='padding:12px; border:1px solid #d1d5db;'>حصة التعويض</th>
                </tr>
            </thead>
            <tbody>
                {rows_html}
            </tbody>
        </table>
    </div>
    """

def generate_swap_table_image(state, teacher_name, day_name):
    """v1.4.2 — صورة التبادل الودي بـ PIL مطابقة لمنهج draw_schedule_image في رسم العربية."""
    if not isinstance(state, dict) or not state:
        return gr.update(value=None)

    try:
        ensure_data_directories()
        os.makedirs(SWAP_IMG_DIR, exist_ok=True)

        target_date = get_date_of_weekday(day_name)

        rows = []
        for p, info in sorted(state.items(), key=lambda x: int(x[0])):
            rows.append({
                "المعلم الطالب": str(info.get("requester", "")),
                "الصف": str(info.get("class", "")),
                "الحصة": f"الحصة {p}",
                "المعلم البديل": str(info.get("candidate", "")),
                "يوم التعويض": str(info.get("comp_day", "يحدد لاحقاً")),
                "حصة التعويض": str(info.get("comp_period", "يحدد لاحقاً")),
            })

        pil_font_path = None
        for candidate in [
            image_font_path,
            os.path.join(APP_DIR, "Amiri-Regular.ttf"),
            "/app/Amiri-Regular.ttf",
            "./Amiri-Regular.ttf",
            font_path,
            os.path.join(APP_DIR, "Cairo-Regular.ttf"),
            "/app/Cairo-Regular.ttf",
            "./Cairo-Regular.ttf",
        ]:
            if candidate and os.path.exists(candidate):
                pil_font_path = candidate
                break

        def load_font(size, bold=False):
            try:
                if pil_font_path:
                    return ImageFont.truetype(pil_font_path, size=size)
            except Exception:
                pass
            try:
                fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
                return ImageFont.truetype(fallback, size=size)
            except Exception:
                return ImageFont.load_default()

        font_title = load_font(40, bold=True)
        font_subtitle = load_font(27, bold=False)
        font_header = load_font(25, bold=True)
        font_cell = load_font(24, bold=False)
        font_footer = load_font(24, bold=True)

        temp_img = Image.new("RGB", (10, 10), "white")
        temp_draw = ImageDraw.Draw(temp_img)

        def text_size(value, font):
            text_value = "" if value is None else str(value)
            bbox = temp_draw.textbbox((0, 0), text_value, font=font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]

        def draw_text_right(draw, x_right, y_top, value, font, fill):
            text_value = "" if value is None else str(value)
            w, h = text_size(text_value, font)
            draw.text((x_right - w, y_top), text_value, font=font, fill=fill)
            return w, h

        def draw_text_center(draw, box, value, font, fill):
            x1, y1, x2, y2 = box
            text_value = "" if value is None else str(value)
            w, h = text_size(text_value, font)
            draw.text((x1 + ((x2 - x1) - w) / 2, y1 + ((y2 - y1) - h) / 2 - 2), text_value, font=font, fill=fill)

        def wrap_text_by_width(value, font, max_width):
            text_value = "" if value is None else str(value).strip()
            if not text_value:
                return [""]

            words = text_value.split()
            if len(words) <= 1:
                return [text_value]

            lines = []
            current = words[0]
            for word in words[1:]:
                trial = current + " " + word
                trial_w, _ = text_size(trial, font)
                if trial_w <= max_width:
                    current = trial
                else:
                    lines.append(current)
                    current = word
            lines.append(current)
            return lines if lines else [text_value]

        def draw_multiline_center(draw, box, value, font, fill, line_gap=5):
            x1, y1, x2, y2 = box
            max_width = max(40, int((x2 - x1) - 18))
            lines = wrap_text_by_width(value, font, max_width)
            line_heights = [text_size(line, font)[1] for line in lines]
            total_h = sum(line_heights) + max(0, len(lines) - 1) * line_gap
            y = y1 + ((y2 - y1) - total_h) / 2

            for line, h in zip(lines, line_heights):
                w, _ = text_size(line, font)
                draw.text((x1 + ((x2 - x1) - w) / 2, y), line, font=font, fill=fill)
                y += h + line_gap

        columns = [
            ("المعلم الطالب", 245),
            ("الصف", 270),
            ("الحصة", 125),
            ("المعلم البديل", 245),
            ("يوم التعويض", 165),
            ("حصة التعويض", 165),
        ]

        margin = 42
        table_width = sum(width for _, width in columns)
        image_width = table_width + margin * 2
        header_h = 135
        table_header_h = 58
        base_row_h = 64

        row_heights = []
        for row in rows:
            max_lines = 1
            for col_name, col_w in columns:
                max_lines = max(max_lines, len(wrap_text_by_width(row.get(col_name, ""), font_cell, col_w - 18)))
            row_heights.append(max(base_row_h, 44 + max_lines * 30))

        image_height = header_h + table_header_h + sum(row_heights) + 58
        image = Image.new("RGB", (image_width, image_height), "#ffffff")
        draw = ImageDraw.Draw(image)

        # Header
        header_bg = THEME_COLOR
        draw.rectangle((0, 0, image_width, header_h), fill=header_bg)

        title = "جدول التبادلات الودية المعتمدة"
        subtitle = f"{teacher_name or 'الكل'} | {day_name} | {target_date}"

        title_w, title_h = text_size(title, font_title)
        subtitle_w, subtitle_h = text_size(subtitle, font_subtitle)
        draw.text(((image_width - title_w) / 2, 24), title, font=font_title, fill=ACCENT_COLOR)
        draw.text(((image_width - subtitle_w) / 2, 78), subtitle, font=font_subtitle, fill="#ffffff")

        # Table: RTL columns
        y = header_h
        x_right = image_width - margin

        header_fill = "#e8f5e9"
        header_text = "#004d40"
        border = "#cbd5e1"
        row_fill_1 = "#ffffff"
        row_fill_2 = "#f8faf8"
        text_fill = "#1f2937"

        x = x_right
        for col_name, col_w in columns:
            x1 = x - col_w
            draw.rectangle((x1, y, x, y + table_header_h), fill=header_fill, outline=border)
            draw_multiline_center(draw, (x1, y, x, y + table_header_h), col_name, font_header, header_text)
            x = x1

        y += table_header_h

        for idx, row in enumerate(rows):
            row_h = row_heights[idx]
            bg = row_fill_1 if idx % 2 == 0 else row_fill_2
            x = x_right

            for col_name, col_w in columns:
                x1 = x - col_w
                draw.rectangle((x1, y, x, y + row_h), fill=bg, outline=border)
                draw_multiline_center(draw, (x1, y, x, y + row_h), row.get(col_name, ""), font_cell, text_fill)
                x = x1

            y += row_h

        footer_text = f"{SYSTEM_NAME} {SYSTEM_SUBTITLE}"
        footer_w, footer_h = text_size(footer_text, font_footer)
        draw.text(((image_width - footer_w) / 2, image_height - 39), footer_text, font=font_footer, fill=THEME_COLOR)

        filename = os.path.join(
            SWAP_IMG_DIR,
            f"swap_table_{get_now_oman().strftime('%Y%m%d_%H%M%S_%f')}.png"
        )
        image.save(filename)
        return gr.update(value=filename)

    except Exception as e:
        print(f"generate_swap_table_image error: {e}")
        return gr.update(value=None)

def format_period_label(period_value):
    raw = str(period_value or "").strip()
    if not raw:
        return ""
    if raw.startswith("الحصة"):
        return raw
    return f"الحصة {raw}"


def export_confirmed_swaps_excel():
    if not isinstance(swap_db, dict) or not swap_db:
        return gr.update(value=None)

    rows = []
    for _, info in sorted(swap_db.items(), key=lambda item: (
        str(item[1].get("updated_at", "")),
        str(item[1].get("requester", "")),
        str(item[1].get("day", "")),
        str(item[1].get("period", "")),
    )):
        updated_at = str(info.get("updated_at", "")).strip()
        approval_date = updated_at.split(" ")[0] if updated_at else ""
        rows.append({
            "المعلم الطالب للتبادل": str(info.get("requester", "")),
            "المعلم البديل": str(info.get("candidate", "")),
            "الصف": str(info.get("class", "")),
            "اليوم الأصلي": str(info.get("day", "")),
            "الحصة الأصلية": format_period_label(info.get("period", "")),
            "يوم التعويض": str(info.get("comp_day", "")),
            "حصة التعويض": str(info.get("comp_period", "")),
            "التاريخ": approval_date,
        })

    if not rows:
        return gr.update(value=None)

    df = pd.DataFrame(rows)
    filename = f"سجل_التبادلات_الودية_المعتمدة_{get_now_oman().strftime('%Y%m%d_%H%M%S')}.xlsx"

    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='التبادلات المعتمدة')
        ws = writer.sheets['التبادلات المعتمدة']

        header_fill = PatternFill(fill_type='solid', fgColor='0B6E4F')
        header_font = Font(color='FFFFFF', bold=True)
        center_alignment = Alignment(horizontal='center', vertical='center')

        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = center_alignment

        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = center_alignment

        for column_cells in ws.columns:
            max_length = 0
            column_letter = column_cells[0].column_letter
            for cell in column_cells:
                cell_value = "" if cell.value is None else str(cell.value)
                max_length = max(max_length, len(cell_value))
            ws.column_dimensions[column_letter].width = min(max(max_length + 4, 14), 40)

        ws.freeze_panes = 'A2'
        ws.sheet_view.rightToLeft = True

    return gr.update(value=filename)
        
def load_confirmed_swaps_for_context(t, d):
    t = str(t or "").split(" (")[0].strip()
    state = {}

    if not t or not d:
        return state, gr.update(value=render_swap_table_html(state))

    for _, info in swap_db.items():
        if info.get("requester") == t and info.get("day") == d:
            p = str(info.get("period", "")).strip()
            if not p:
                continue

            state[p] = {
                "requester": info.get("requester", ""),
                "class": info.get("class", ""),
                "candidate": info.get("candidate", ""),
                "choice": info.get("choice", ""),
                "message": info.get("message", ""),
                "comp_day": info.get("comp_day", "يحدد لاحقاً"),
                "comp_period": info.get("comp_period", "يحدد لاحقاً"),
            }

    return state, gr.update(value=render_swap_table_html(state))

def clear_swap_detail_ui():
    return (
        gr.update(choices=[], value=None, visible=True),
        gr.update(value=SWAP_EMPTY_MSG, visible=True),
        gr.update(value="", visible=True),
        gr.update(visible=True, interactive=False)
    )









        

@state_locked
def confirm_swap(t, period_value, choice, d, msg_text, state, actor_name="", actor_role=""):
    t = str(t or "").split(" (")[0].strip()
    current_state = dict(state) if isinstance(state, dict) else {}

    if not t or not period_value or not choice or "❌" in str(choice):
        return current_state, gr.update(value=render_swap_table_html(current_state))

    p_clean = extract_clean_period_number(period_value)

    req_class_raw = teachers_db.get(t, {}).get(
        d, {}
    ).get(
        p_clean,
        teachers_db.get(t, {}).get(d, {}).get(int(p_clean) if p_clean.isdigit() else p_clean, "")
    )

    elegant_class = format_elegant_class(req_class_raw)
    candidate, comp_day, comp_period = extract_swap_choice_details(choice)

    # ── فحص محلي (داخل الحالة الحالية للمعلم) ──
    for p_ex, info_ex in current_state.items():
        if (
            info_ex.get("comp_day") == comp_day
            and info_ex.get("comp_period") == comp_period
            and p_ex != p_clean
        ):
            return current_state, gr.update(
                value=render_swap_table_html(current_state)
                + f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً لهذا المعلم.</div>"
            )

    # ── فحص عالمي (على جميع التبادلات المعتمدة) ──
    current_key = f"{t}|{d}|{p_clean}"
    for key, info in swap_db.items():
        same_comp = (
            info.get("comp_day") == comp_day
            and info.get("comp_period") == comp_period
        )
        if not same_comp:
            continue

        if info.get("requester") == t and key != current_key:
            return current_state, gr.update(
                value=render_swap_table_html(current_state)
                + f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً لهذا المعلم.</div>"
            )

        if info.get("candidate") == candidate and key != current_key:
            return current_state, gr.update(
                value=render_swap_table_html(current_state)
                + f"<div style='color:red; padding:10px; text-align:center;'>⚠️ موعد التعويض ({comp_day} - {comp_period}) محجوز مسبقاً على المعلم البديل.</div>"
            )

    current_state[p_clean] = {
        "requester": t,
        "class": elegant_class,
        "candidate": candidate,
        "choice": choice,
        "message": msg_text,
        "comp_day": comp_day,
        "comp_period": comp_period
    }

    swap_db[current_key] = {
        "requester": t,
        "day": d,
        "period": p_clean,
        "class": elegant_class,
        "candidate": candidate,
        "choice": choice,
        "message": msg_text,
        "comp_day": comp_day,
        "comp_period": comp_period,
        "updated_at": get_now_oman().strftime("%Y-%m-%d %H:%M")
    }
    save_swap_db()

    write_audit_log(
        "اعتماد تبادل ودي",
        target_teacher=t,
        old_value="",
        new_value={
            "day": d,
            "period": p_clean,
            "class": elegant_class,
            "candidate": candidate,
            "comp_day": comp_day,
            "comp_period": comp_period
        },
        details=f"اعتماد تبادل ودي بين {t} و {candidate}",
        actor_name=actor_name,
        actor_role=actor_role
    )

    return current_state, gr.update(value=render_swap_table_html(current_state))

def format_teacher_name(t_name):
    if t_name in teachers_db:
        role = teachers_db[t_name].get("role", "معلم")
        if role in ["معلم أول"] + ADMIN_ROLES: return f"{t_name} ({role})"
    return t_name

def resolve_teacher_display_value(raw_name, choices):
    if not raw_name:
        return None
    if raw_name in choices:
        return raw_name
    for c in choices:
        if str(c).startswith(str(raw_name) + " ("):
            return c
    return None

def resolve_teacher_display_values(raw_names, choices):
    if not raw_names:
        return []
    out = []
    for name in raw_names:
        resolved = resolve_teacher_display_value(name, choices)
        if resolved:
            out.append(resolved)
    return out

def get_teacher_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    t_list = sorted([
        t for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and d.get("dept") != "الهيئة الإدارية"
        and d.get("role", "معلم") not in ADMIN_ROLES
    ])
    choices = []
    for t in t_list:
        role = teachers_db[t].get("role", "معلم")
        if role != "معلم": choices.append(f"{t} ({role})")
        else: choices.append(t)
    return choices

def get_teacher_schedule_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    choices = []
    for t, d in sorted(teachers_db.items(), key=lambda item: item[0]):
        dept = str(d.get("dept", "")).strip()
        role = str(d.get("role", "معلم")).strip() or "معلم"
        if dept == "الهيئة الإدارية":
            continue
        if role in ADMIN_ROLES:
            continue
        if dept_filter != "الكل" and dept != dept_filter:
            continue
        choices.append(f"{t} ({role})" if role != "معلم" else t)
    return choices

def get_absentee_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    t_list = sorted([t for t, d in teachers_db.items() if (dept_filter == "الكل" or d.get("dept") == dept_filter) and d.get("dept") != "الهيئة الإدارية"])
    choices = []
    for t in t_list:
        role = teachers_db[t].get("role", "معلم")
        if role in ["معلم أول", "منسق مادة"]: choices.append(f"{t} ({role})")
        else: choices.append(t)
    return choices

def clean_teacher_name(val):
    val = str(val).strip()
    val = val.replace('ﷲ', 'الله').replace('ﷻ', 'جل جلاله')
    val = re.sub(r'[\ue000-\uf8ff\ufffd]', '', val) 
    val = re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', val)
    val = re.sub(r'\s+', ' ', val)
    return val

def validate_reference_filename(file_name, expected_keywords):
    if not file_name:
        return False, "❌ لم يتم العثور على اسم الملف."

    base_name = os.path.basename(str(file_name)).strip().lower()

    if "." in base_name:
        base_name = ".".join(base_name.split(".")[:-1]).strip()

    normalized_name = (
        base_name
        .replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )

    normalized_keywords = []
    for kw in expected_keywords:
        kw_norm = (
            str(kw).strip().lower()
            .replace("أ", "ا")
            .replace("إ", "ا")
            .replace("آ", "ا")
            .replace("ى", "ي")
            .replace("ة", "ه")
        )
        normalized_keywords.append(kw_norm)

    for kw in normalized_keywords:
        if kw in normalized_name:
            return True, ""

    expected_str = " / ".join(expected_keywords)
    return False, f"❌ اسم الملف لا يطابق المطلوب. يجب أن يحتوي على: {expected_str}"

def get_name_fingerprint(val):
    val = str(val).strip()
    val = val.replace('عبد ', 'عبد') 
    val = val.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا') 
    val = val.replace('ى', 'ي').replace('ة', 'ه') 
    words = val.split()
    words = [w for w in words if w != 'بن'] 
    if not words: return "", set()
    return words[0], set(words) 

def extract_class_info(val, dept):
    val = str(val).strip().replace('\r', '\n')
    lines = [x.strip() for x in val.split('\n') if x.strip()]
    if not lines or "اليوم" in val or "الحصة" in val: return ""
    cls_clean = " ".join(lines)
    return re.sub(r'[\u200b-\u200f\u202a-\u202e\ufeff]', '', cls_clean).strip()

def get_class_dna(class_string):
    s = str(class_string).strip()
    s = s.translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')) 
    s = s.replace("ـ", "") 
    if not s: return ""
    
    nums = re.findall(r'\d+', s)
    section = nums[-1] if nums else ""
    
    grade = ""
    if any(x in s for x in ["عاشر", "10", "١٠"]): grade = "10"
    elif any(x in s for x in ["تاسع", "9", "٩"]): grade = "9"
    elif any(x in s for x in ["ثامن", "8", "٨"]): grade = "8"
    elif any(x in s for x in ["سابع", "7", "٧"]): grade = "7"
    elif any(x in s for x in ["حادي", "11", "١١"]): grade = "11"
    elif any(x in s for x in ["ثاني", "12", "١٢"]): grade = "12"
    
    if grade and section: return f"G{grade}-{section}"
    return re.sub(r'[^\w\dأ-ي]', '', s) 

def check_teacher_load(teacher_name, day_name, period_to_add):
    try:
        if teacher_name not in teachers_db: return ""
        info = teachers_db[teacher_name]
        base_p = {int(k) for k in info.get(day_name, {}).keys() if str(k).isdigit()}
        
        if str(period_to_add).isdigit():
            all_slots = sorted(list(base_p | {int(period_to_add)}))
        else:
            all_slots = sorted(list(base_p))
            
        consecutive = max_con = 1
        for i in range(len(all_slots)-1):
            if all_slots[i+1] == all_slots[i] + 1:
                consecutive += 1
                max_con = max(max_con, consecutive)  # ← داخل الحلقة
            else:
                consecutive = 1
            
        warns = []
        if max_con >= 3: warns.append("⚠️ إجهاد بدني")
        if len(all_slots) >= 6: warns.append("⚠️ كثافة عالية")
        return " | ".join(warns)
    except Exception:
        return ""

def get_falcon_eye_candidates(absent_t, period, day_name):
    try:
        if not absent_t or not period: return []
        
        p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
        if not p_str_clean.isdigit(): return [] 
        p_int = int(p_str_clean)
        
        target_class = teachers_db.get(absent_t, {}).get(day_name, {}).get(str(p_int), "")
        if not target_class: target_class = teachers_db.get(absent_t, {}).get(day_name, {}).get(p_int, "")
        if not target_class: return []
        
        target_fingerprint = get_class_dna(target_class)
        candidates = []
        
        for name, info in teachers_db.items():
            if name == absent_t: continue
            if str(p_int) in info.get(day_name, {}) or p_int in info.get(day_name, {}): continue
            
            teaches_same = False
            for d in SCHOOL_WEEK_DAYS:
                for c in info.get(d, {}).values():
                    if target_fingerprint == get_class_dna(c) and target_fingerprint != "": teaches_same = True
                    
            if teaches_same:
                warn = check_teacher_load(name, day_name, p_int)
                warn_str = f" {warn}" if warn else ""
                candidates.append(f"🦅 {name} (يدرس نفس الصف){warn_str}")
        return candidates
    except Exception as e:
        return []

@state_locked
def add_manual_staff(name, dept, phone, role, dept_filter, is_owner=False):
    if not bool(is_owner):
        return "<div style='color:red; font-weight:bold;'>❌ الإضافة اليدوية للطاقم متاحة لمالك النظام فقط.</div>", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    if not name or not str(name).strip():
        return "<div style='color:red; font-weight:bold;'>❌ الرجاء إدخال الاسم.</div>", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    t_name = clean_teacher_name(name)
    if t_name not in teachers_db:
        teachers_db[t_name] = {"dept": dept, "cover_count": 0, "absent_count": 0, "shortcoming_count": 0, "phone": "", "specialty": "", "role": role, "exempt_days": [], "exempt_periods": [], "absence_dates": [], "الأحد": {}, "الإثنين": {}, "الثلاثاء": {}, "الأربعاء": {}, "الخميس": {}}
    else:
        teachers_db[t_name]["dept"] = dept
        teachers_db[t_name]["role"] = role
    if phone:
        phone_clean = re.sub(r'\D', '', str(phone))
        if len(phone_clean) == 8:
            phone_clean = "968" + phone_clean
        teachers_db[t_name]["phone"] = phone_clean
    save_db()
    choices_all = get_teacher_choices(dept_filter)
    abs_choices = get_absentee_choices(dept_filter)
    t_names_filtered = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])
    msg = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>✅ تم إضافة/تحديث ({t_name}) بنجاح كطاقم إداري!</div>"
    return msg, gr.update(choices=abs_choices), gr.update(choices=choices_all, value=None), gr.update(choices=choices_all, value=None), gr.update(choices=t_names_filtered, value=None), gr.update(value=""), gr.update(value="")
def process_admin_excel(file, dept_filter):
    if file is None:
        return (
            "<div style='color:red; font-weight:bold;'>❌ الرجاء رفع ملف الإداريين أولاً.</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update()
        )

    try:
        df = pd.read_excel(file.name, header=None) if not file.name.endswith('.csv') else pd.read_csv(file.name, header=None)
        df = df.fillna("")

        added_or_updated = 0
        found_names = []

        for r in range(len(df)):
            raw_phone = str(df.iloc[r, 0]).strip() if df.shape[1] > 0 else ""
            raw_role = str(df.iloc[r, 1]).strip() if df.shape[1] > 1 else ""
            raw_name = str(df.iloc[r, 2]).strip() if df.shape[1] > 2 else ""

            if not raw_name or raw_name == "nan":
                continue

            t_name = clean_teacher_name(raw_name)
            if not t_name or len(t_name) < 3:
                continue

            if raw_phone.endswith(".0"):
                raw_phone = raw_phone[:-2]

            phone_digits = re.sub(r"\D", "", raw_phone)
            if len(phone_digits) == 8:
                phone_digits = "968" + phone_digits

            role_val = raw_role if raw_role else "أخصائي اجتماعي"

            if t_name not in teachers_db:
                teachers_db[t_name] = {
                    "dept": "الهيئة الإدارية",
                    "cover_count": 0,
                    "absent_count": 0,
                    "shortcoming_count": 0,
                    "phone": "",
                    "specialty": "",
                    "role": role_val,
                    "exempt_days": [],
                    "exempt_periods": [],
                    "absence_dates": [],
                    "الأحد": {},
                    "الإثنين": {},
                    "الثلاثاء": {},
                    "الأربعاء": {},
                    "الخميس": {}
                }
            else:
                teachers_db[t_name]["dept"] = "الهيئة الإدارية"
                teachers_db[t_name]["role"] = role_val

            teachers_db[t_name]["phone"] = phone_digits if phone_digits else teachers_db[t_name].get("phone", "")
            found_names.append(t_name)
            added_or_updated += 1

        save_db()

        dept_filter = resolve_effective_dept(dept_filter)
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        t_names_filtered = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])

        names_list_str = "، ".join(found_names) if found_names else "لا توجد أسماء صالحة"
        msg = (
            f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>"
            f"✅ تم استيراد/تحديث ({added_or_updated}) من الإداريين بنجاح."
            f"<br>👥 الأسماء: {names_list_str}"
            f"</div>"
        )

        return (
            msg,
            gr.update(choices=abs_choices),
            gr.update(choices=choices_all, value=None),
            gr.update(choices=choices_all, value=None),
            gr.update(choices=t_names_filtered, value=None),
            gr.update(value=None),
            gr.update(value=get_updated_balance(dept_filter))
        )

    except Exception as e:
        return (
            f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء رفع ملف الإداريين: {str(e)}</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update()
        )
def process_phone_excel(file):
    if file is None: return "<div style='color:red; font-weight:bold;'>❌ الرجاء رفع ملف أرقام الهواتف.</div>", gr.update()
    try:
        df = pd.read_excel(file.name, header=None) if not file.name.endswith('.csv') else pd.read_csv(file.name, header=None)
        updated = 0
        db_fingerprints = {k: get_name_fingerprint(k) for k in teachers_db.keys()}
        for r in range(len(df)):
            raw_name = str(df.iloc[r, 0]).strip()
            raw_phone = str(df.iloc[r, 1]).strip()
            if not raw_name or raw_name == 'nan': continue
            
            if raw_phone.endswith('.0'):
                raw_phone = raw_phone[:-2]
                
            phone_digits = re.sub(r'\D', '', raw_phone) 
            if len(phone_digits) == 8: phone_digits = "968" + phone_digits
            if not phone_digits: continue
            
            phone_first_name, phone_name_fingerprint = get_name_fingerprint(raw_name)
            if not phone_first_name: continue
            
            for db_key, (db_first_name, db_words) in db_fingerprints.items():
                if db_first_name == phone_first_name and len(db_words) > 0 and db_words.issubset(phone_name_fingerprint):
                    teachers_db[db_key]["phone"] = phone_digits
                    updated += 1
                    break
                    
        save_db()
        return f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>✅ تم بنجاح ربط أرقام ({updated}) معلماً بفضل الرادار الذكي!</div>", gr.update(value=None)
    except Exception as e: return f"<div style='color:red;'>❌ خطأ: {str(e)}</div>", gr.update()

def process_uploaded_excel(file, selected_dept, current_day):
    global teachers_db
    if file is None: return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), "<div style='color:red; font-weight:bold;'>❌ الرجاء رفع ملف الإكسل أولاً.</div>", gr.update(), gr.update())
    try:
        df = pd.read_excel(file.name, header=None) if not file.name.endswith('.csv') else pd.read_csv(file.name, header=None)
        df = df.fillna('')
        found_in_file = []
        start_row = 0
        for i in range(min(15, len(df))):
            row_str = " ".join([str(x) for x in df.iloc[i].values])
            if "اليوم" in row_str and ("الأولى" in row_str or "الاولى" in row_str):
                start_row = i - 2 
                break
        if start_row < 0: start_row = 0

        for r in range(start_row, len(df), 10):
            if r + 2 >= len(df): break 
            for base_col in [0, 9]:
                if base_col + 7 >= len(df.columns): continue 
                t_name_raw = str(df.iloc[r, base_col]).strip()
                if not t_name_raw or "ALBATINAH" in t_name_raw.upper() or "اليوم" in t_name_raw: continue
                t_name = clean_teacher_name(t_name_raw)
                if not t_name or len(t_name) < 3: continue
                if t_name not in found_in_file: found_in_file.append(t_name)
                
                if t_name not in teachers_db:
                    teachers_db[t_name] = {"dept": selected_dept, "cover_count": 0, "absent_count": 0, "shortcoming_count": 0, "phone": "", "specialty": "", "role": "معلم", "exempt_days": [], "exempt_periods": [], "absence_dates": [], "الأحد": {}, "الإثنين": {}, "الثلاثاء": {}, "الأربعاء": {}, "الخميس": {}}
                else: teachers_db[t_name]["dept"] = selected_dept

                col_to_p = {}
                day_col = -1
                for c in range(base_col, min(base_col + 8, len(df.columns))):
                    val = str(df.iloc[r+2, c]).strip().replace("أ", "ا").replace("إ", "ا")
                    if "اليوم" in val: day_col = c
                    elif "الاولى" in val: col_to_p[c] = 1
                    elif "الثانية" in val: col_to_p[c] = 2
                    elif "الثالثة" in val: col_to_p[c] = 3
                    elif "الرابعة" in val: col_to_p[c] = 4
                    elif "الخامسة" in val: col_to_p[c] = 5
                    elif "السادسة" in val: col_to_p[c] = 6
                    elif "السابعة" in val: col_to_p[c] = 7
                    
                if day_col == -1: day_col = base_col + 7
                if day_col >= len(df.columns): continue

                for dr in range(r+3, min(r+8, len(df))):
                    day_cell = str(df.iloc[dr, day_col]).replace("أ", "ا").replace("إ", "ا")
                    current_day_val = next((d for d in ["الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس"] if d in day_cell), None)
                    if not current_day_val: continue
                    current_day_val = current_day_val.replace("الاحد", "الأحد").replace("الاثنين", "الإثنين").replace("الاربعاء", "الأربعاء")
                    for c, pnum in col_to_p.items():
                        if c < len(df.columns):
                            val = str(df.iloc[dr, c]).strip()
                            cls = extract_class_info(val, selected_dept)
                            if cls: teachers_db[t_name][current_day_val][pnum] = cls
                                
        save_db()
        t_names_all = sorted(list(teachers_db.keys()))
        choices_all = get_teacher_choices("الكل")
        abs_choices = get_absentee_choices("الكل")
        names_list_str = "، ".join(found_in_file)
        current_time = get_now_oman().strftime("%H:%M:%S")
        success_msg = f"<div style='color:#004d40; background:#e0f2f1; padding:15px; border-radius:10px; border-right: 5px solid #004d40;'><b style='font-size:1.2em;'>✅ تمت معالجة مصفوفة ({selected_dept}) بنجاح فائق!</b> 🕒 {current_time}<br>📌 <b>المعلمون المستخرجون:</b> {len(found_in_file)} معلمين<br>👨‍🏫 <b>الأسماء:</b> {names_list_str}<br><hr style='border-top:1px solid #b2dfdb; margin:10px 0;'>📊 إجمالي المعلمين في المنظومة: {len(t_names_all)}</div>"
        return (gr.update(choices=["الكل"] + OFFICIAL_DEPTS), gr.update(choices=abs_choices), gr.update(choices=choices_all, value=None), gr.update(choices=choices_all, value=None), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), success_msg, gr.update(choices=t_names_all), gr.update(value=None))
    except Exception as e: return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء الرفع: {str(e)}</div>", gr.update(), gr.update())

def delete_department_data(dept_to_delete, current_day):
    global teachers_db
    if not dept_to_delete: return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), "<div style='color:red; font-weight:bold;'>❌ الرجاء تحديد القسم أولاً.</div>", gr.update(), gr.update())
    teachers_to_delete = [t for t, d in teachers_db.items() if d.get("dept") == dept_to_delete]
    for t in teachers_to_delete: del teachers_db[t]
    save_db()
    t_names_all = sorted(list(teachers_db.keys()))
    msg = f"<div style='color:#c62828; background:#ffebee; padding:15px; border-radius:10px; border-right: 5px solid #c62828;'><b style='font-size:1.2em;'>🗑️ تمت عملية المسح بنجاح!</b><br>تم حذف جميع بيانات وسجلات معلمي قسم ({dept_to_delete}).</div>"
    return (gr.update(choices=["الكل"] + OFFICIAL_DEPTS), gr.update(choices=get_absentee_choices("الكل")), gr.update(choices=get_teacher_choices("الكل"), value=None), gr.update(choices=get_teacher_choices("الكل"), value=None), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), msg, gr.update(choices=t_names_all, value=None), gr.update(value=None))

def render_compact_rtl_table_html(df, empty_message="لا توجد بيانات للعرض."):
    if df is None or df.empty:
        return f"""
        <div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>
            {empty_message}
        </div>
        """

    safe_df = df.fillna("-").copy()

    headers_html = "".join(
        f"<th style='padding:10px 12px; background:#0f766e; color:#ffffff; border:1px solid #d1d5db; white-space:nowrap; font-size:14px; font-weight:900;'>{col}</th>"
        for col in safe_df.columns
    )

    rows_html = ""
    for idx, (_, row) in enumerate(safe_df.iterrows()):
        bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
        cells_html = ""
        for col in safe_df.columns:
            value = row[col]
            align = "center" if col != "المعلم" else "right"
            weight = "900" if col == "المعلم" else "800"
            color = "#0f172a" if col == "المعلم" else "#0f766e"
            cells_html += (
                f"<td style='padding:9px 12px; border:1px solid #d1d5db; "
                f"white-space:nowrap; font-size:14px; color:{color}; font-weight:{weight}; text-align:{align};'>{value}</td>"
            )
        rows_html += f"<tr style='background:{bg};'>{cells_html}</tr>"

    return f"""
    <div style='background:#ffffff; border:1px solid #dbeafe; border-radius:12px; overflow:hidden; box-shadow:0 1px 2px rgba(15,23,42,0.05); direction:rtl;'>
        <div style='overflow-x:auto; width:100%; -webkit-overflow-scrolling:touch;'>
            <table style='width:100%; min-width:360px; border-collapse:collapse; text-align:center; direction:rtl; font-family:Cairo, Arial, sans-serif;'>
                <thead><tr>{headers_html}</tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
    """

def get_updated_balance(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    if str(dept_filter).strip() == "الهيئة الإدارية":
        return "<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>ℹ️ لا تُعرض أرصدة الاحتياط للهيئة الإدارية.</div>"
    data = [
        {"المعلم": format_teacher_name(t), "الرصيد": d["cover_count"]}
        for t, d in teachers_db.items()
        if dept_filter == "الكل" or d.get("dept") == dept_filter
    ]
    df = pd.DataFrame(data).sort_values("الرصيد", ascending=False) if data else pd.DataFrame(columns=["المعلم", "الرصيد"])
    return render_compact_rtl_table_html(df, "لا توجد أرصدة احتياط للعرض.")

def get_updated_absences(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    if str(dept_filter).strip() == "الهيئة الإدارية":
        return "<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>ℹ️ لا يُعرض حصر الغياب للهيئة الإدارية.</div>"
    data = [
        {"المعلم": format_teacher_name(t), "مرات الغياب": d.get("absent_count", 0)}
        for t, d in teachers_db.items()
        if dept_filter == "الكل" or d.get("dept") == dept_filter
    ]
    df = pd.DataFrame(data).sort_values("مرات الغياب", ascending=False) if data else pd.DataFrame(columns=["المعلم", "مرات الغياب"])
    return render_compact_rtl_table_html(df, "لا توجد بيانات غياب للعرض.")

def get_updated_shortcomings(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    if str(dept_filter).strip() == "الهيئة الإدارية":
        return "<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>ℹ️ لا تُعرض حالات التقصير للهيئة الإدارية.</div>"
    data = [
        {"المعلم": format_teacher_name(t), "حالات التقصير": int(d.get("shortcoming_count", 0) or 0)}
        for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and d.get("dept") != "الهيئة الإدارية"
        and d.get("role", "معلم") not in ADMIN_ROLES
        and int(d.get("shortcoming_count", 0) or 0) > 0
    ]
    df = pd.DataFrame(data).sort_values("حالات التقصير", ascending=False) if data else pd.DataFrame(columns=["المعلم", "حالات التقصير"])
    return render_compact_rtl_table_html(df, "لا توجد حالات تقصير مسجلة للعرض.")

def get_day_overview(day, dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    rows = [
        {"المعلم": format_teacher_name(t), **{f"ح {p}": d.get(day, {}).get(p, "-") for p in range(1, MAX_PERIODS + 1)}}
        for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and d.get("dept") != "الهيئة الإدارية"
        and d.get("role", "معلم") not in ADMIN_ROLES
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["المعلم"] + [f"ح {p}" for p in range(1, MAX_PERIODS + 1)])

def render_day_table_html(df, page=0, page_size=PAGE_SIZE):
    if df is None or df.empty:
        empty_html = "<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px;'>لا توجد بيانات لعرضها في جدول اليوم.</div>"
        return empty_html, 0, 1, 0

    safe_df = df.fillna("-").copy()
    total_rows = len(safe_df)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)

    try:
        safe_page = int(page or 0)
    except Exception:
        safe_page = 0
    safe_page = max(0, min(safe_page, total_pages - 1))

    start = safe_page * page_size
    end = start + page_size
    page_df = safe_df.iloc[start:end]

    headers_html = "".join(
        f"<th style='padding:10px 12px; background:#0f766e; color:#ffffff; border:1px solid #d1d5db; white-space:nowrap; font-size:13px;'>{col}</th>"
        for col in page_df.columns
    )

    rows_html = ""
    for _, row in page_df.iterrows():
        row_cells = "".join(
            f"<td style='padding:9px 10px; border:1px solid #d1d5db; white-space:nowrap; font-size:13px; color:#0f172a;'>{row[col]}</td>"
            for col in page_df.columns
        )
        rows_html += f"<tr>{row_cells}</tr>"

    table_html = f"""
    <div style='background:#ffffff; border:1px solid #dbeafe; border-radius:12px; overflow:hidden; box-shadow:0 1px 2px rgba(15,23,42,0.05);'>
        <div style='overflow-x:auto; width:100%; -webkit-overflow-scrolling:touch;'>
            <table style='width:100%; min-width:760px; border-collapse:collapse; text-align:center; direction:rtl;'>
                <thead>
                    <tr>{headers_html}</tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
    """
    return table_html, safe_page, total_pages, total_rows

def get_day_table_updates(day_name, dept_filter, page=0):
    effective_dept = resolve_effective_dept(dept_filter)
    df = get_day_overview(day_name, effective_dept)

    if df is None or df.empty:
        load_db()
        df = get_day_overview(day_name, effective_dept)

    table_html, safe_page, total_pages, total_rows = render_day_table_html(df, page, PAGE_SIZE)
    label = "إجمالي المعلمين المعروضين" if effective_dept == "الكل" else f"إجمالي معلمي {effective_dept}"
    page_html = f"<div style='text-align:center; color:#0f766e; font-weight:bold; padding:8px 0;'>{label}: {total_rows} | صفحة {safe_page + 1} من {total_pages}</div>"

    return (
        gr.update(value=df, visible=False),
        gr.update(value=table_html, visible=True),
        gr.update(visible=True),
        gr.update(interactive=safe_page > 0),
        gr.update(interactive=safe_page < total_pages - 1),
        gr.update(value=page_html, visible=True),
        safe_page,
    )

def change_day_page(delta, day_name, dept_filter, current_page):
    effective_dept = resolve_effective_dept(dept_filter)
    df = get_day_overview(day_name, effective_dept)
    total_pages = max(1, (len(df) + PAGE_SIZE - 1) // PAGE_SIZE)

    try:
        safe_page = int(current_page or 0)
    except Exception:
        safe_page = 0

    new_page = max(0, min(safe_page + delta, total_pages - 1))
    return get_day_table_updates(day_name, effective_dept, new_page)

def get_teacher_weekly_schedule(teacher_name):
    teacher_name = str(teacher_name or "").split(" (")[0].strip()

    if (
        not teacher_name
        or teacher_name not in teachers_db
        or teachers_db[teacher_name].get("dept") == "الهيئة الإدارية"
        or teachers_db[teacher_name].get("role", "معلم") in ADMIN_ROLES
    ):
        return pd.DataFrame(columns=["اليوم", "ح 1", "ح 2", "ح 3", "ح 4", "ح 5", "ح 6", "ح 7"])

    rows = [
        {
            "اليوم": day,
            **{f"ح {p}": teachers_db[teacher_name].get(day, {}).get(p, "-") for p in range(1, MAX_PERIODS + 1)}
        }
        for day in SCHOOL_WEEK_DAYS
    ]
    return pd.DataFrame(rows)


def render_weekly_schedule_html(df):
    if df is None or df.empty:
        return """
        <div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>
            لا توجد بيانات لعرضها في جدول المعلم.
        </div>
        """

    safe_df = df.fillna("-").copy()

    headers_html = "".join(
        f"<th style='padding:10px 12px; background:#0f766e; color:#ffffff; border:1px solid #d1d5db; white-space:nowrap; font-size:13px;'>{col}</th>"
        for col in safe_df.columns
    )

    rows_html = ""
    for _, row in safe_df.iterrows():
        row_cells = "".join(
            f"<td style='padding:10px 12px; border:1px solid #d1d5db; white-space:nowrap; font-size:13px; color:#0f172a; font-weight:600;'>{row[col]}</td>"
            for col in safe_df.columns
        )
        rows_html += f"<tr>{row_cells}</tr>"

    return f"""
    <div style='background:#ffffff; border:1px solid #dbeafe; border-radius:12px; overflow:hidden; box-shadow:0 1px 2px rgba(15,23,42,0.05); direction:rtl;'>
        <div style='overflow-x:auto; width:100%; -webkit-overflow-scrolling:touch;'>
            <table style='width:100%; min-width:760px; border-collapse:collapse; text-align:center; direction:rtl; font-family:Cairo, Arial, sans-serif;'>
                <thead>
                    <tr>{headers_html}</tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </div>
    """


def get_teacher_weekly_schedule_html(teacher_name):
    return render_weekly_schedule_html(get_teacher_weekly_schedule(teacher_name))

def get_dynamic_header(day_name):
    target_date = get_date_of_weekday(day_name)
    return f"<div style='background:#004d40; padding:15px; border-radius:10px; text-align:center;'><div style='font-size:1.4em; font-weight:bold; color:#ffffff !important;'>📅 {day_name} | {target_date}</div></div>"

def get_initial_header(): return get_dynamic_header(get_current_day_oman())

def draw_schedule_image(df, day_name):
    target_date = get_date_of_weekday(day_name)
    absent_list = df["المعلم الغائب"].astype(str).unique().tolist() if df is not None and not df.empty else []
    absent_list = [str(name).strip() for name in absent_list if str(name).strip()]

    def chunk_absent_names(names, chunk_size=3):
        if not names:
            return ["لا يوجد"]
        return ["، ".join(names[i:i + chunk_size]) for i in range(0, len(names), chunk_size)]

    absent_lines = chunk_absent_names(absent_list, 3)

    display_df = df[["المعلم الغائب", "الصف", "الحصة", "المعلم البديل عرض"]].copy()
    display_df.columns = ["المعلم الغائب", "الصف", "الحصة", "المعلم البديل"]

    title_text = f"📅 {day_name} | {target_date}"
    absent_label_text = "المعلمون الغائبون:"

    pil_font_path = None
    for candidate in [image_font_path, os.path.join(APP_DIR, "Amiri-Regular.ttf"), "/app/Amiri-Regular.ttf", "./Amiri-Regular.ttf"]:
        if candidate and os.path.exists(candidate):
            pil_font_path = candidate
            break

    def load_font(size, bold=False):
        try:
            if pil_font_path:
                return ImageFont.truetype(pil_font_path, size=size)
        except Exception:
            pass
        try:
            fallback = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            return ImageFont.truetype(fallback, size=size)
        except Exception:
            return ImageFont.load_default()

    font_title = load_font(40, bold=True)
    font_subtitle = load_font(28, bold=False)
    font_header = load_font(26, bold=True)
    font_cell = load_font(24, bold=False)

    temp_img = Image.new("RGB", (10, 10), "white")
    temp_draw = ImageDraw.Draw(temp_img)

    def text_size(value, font):
        bbox = temp_draw.textbbox((0, 0), str(value), font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def draw_text_right(draw, x_right, y_top, value, font, fill):
        text = "" if value is None else str(value)
        w, h = text_size(text, font)
        draw.text((x_right - w, y_top), text, font=font, fill=fill)
        return w, h

    def wrap_text_by_width(value, font, max_width):
        text = "" if value is None else str(value).strip()
        if not text:
            return [""]
        words = text.split()
        if len(words) <= 1:
            return [text]

        lines = []
        current = words[0]
        for word in words[1:]:
            trial = current + " " + word
            trial_w, _ = text_size(trial, font)
            if trial_w <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        return lines if lines else [text]

    def draw_multiline_right(draw, x_right, y_top, lines, font, fill, line_gap=4):
        y = y_top
        max_w = 0
        total_h = 0
        for line in lines:
            w, h = text_size(line, font)
            draw.text((x_right - w, y), line, font=font, fill=fill)
            y += h + line_gap
            total_h += h + line_gap
            max_w = max(max_w, w)
        if total_h > 0:
            total_h -= line_gap
        return max_w, total_h

    pad_x = 40
    pad_y = 30
    title_h = text_size(title_text, font_title)[1]
    label_h = text_size(absent_label_text, font_subtitle)[1]
    absent_line_h = text_size("نص", font_subtitle)[1]
    absent_line_gap = 6
    header_h = 28 + title_h + 14 + label_h + 8 + (len(absent_lines) * absent_line_h) + max(0, len(absent_lines) - 1) * absent_line_gap + 24
    header_h = max(130, header_h)
    gap_after_header = 20
    base_row_h = 58
    border_color = "#cfd8dc"
    outer_border = "#b0bec5"
    header_bg = "#004d40"
    header_fg = "#ffffff"
    alt_bg = "#f8faf8"
    white_bg = "#ffffff"
    red_bg = "#ffebee"
    teal_bg = "#e0f2f1"
    orange_bg = "#ffebee"
    text_dark = "#1f2937"
    title_fg = "#ffffff"

    columns = [
        ("المعلم الغائب", 280),
        ("الصف", 360),
        ("الحصة", 110),
        ("المعلم البديل", 340),
    ]
    col_width_map = dict(columns)

    prepared_rows = []
    row_heights = []
    line_height = text_size("نص", font_cell)[1]

    for _, row in display_df.iterrows():
        sub_display = str(row.get("المعلم البديل", ""))
        status = ""
        if "❌" in sub_display:
            status = "تقصير"
        elif "🤝" in sub_display:
            status = "تبادل"
        elif "إشراف" in sub_display:
            status = "إشراف"

        class_lines = wrap_text_by_width(
            str(row.get("الصف", "")),
            font_cell,
            max_width=col_width_map["الصف"] - 24
        )

        row_values = {
            "المعلم الغائب": str(row.get("المعلم الغائب", "")),
            "الصف": class_lines,
            "الحصة": str(row.get("الحصة", "")),
            "المعلم البديل": sub_display,
            "_status": status,
        }
        prepared_rows.append(row_values)
        dynamic_h = max(base_row_h, (len(class_lines) * line_height) + 22)
        row_heights.append(dynamic_h)

    table_w = sum(width for _, width in columns)
    img_w = table_w + pad_x * 2
    img_h = header_h + gap_after_header + base_row_h + sum(row_heights) + pad_y * 2 + 10

    image = Image.new("RGB", (img_w, img_h), "white")
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle((pad_x, pad_y, img_w - pad_x, pad_y + header_h), radius=18, fill=header_bg)
    header_x_right = img_w - pad_x - 20
    title_y = pad_y + 18
    draw_text_right(draw, header_x_right, title_y, title_text, font_title, title_fg)

    label_y = title_y + title_h + 14
    draw_text_right(draw, header_x_right, label_y, absent_label_text, font_subtitle, title_fg)

    line_y = label_y + label_h + 8
    for line in absent_lines:
        draw_text_right(draw, header_x_right, line_y, line, font_subtitle, title_fg)
        line_y += absent_line_h + absent_line_gap

    table_top = pad_y + header_h + gap_after_header
    header_y2 = table_top + base_row_h

    x_cursor = img_w - pad_x
    for col_name, col_w in columns:
        x1 = x_cursor - col_w
        x2 = x_cursor
        draw.rectangle((x1, table_top, x2, header_y2), fill=header_bg, outline=outer_border, width=1)
        draw_text_right(draw, x2 - 16, table_top + 12, col_name, font_header, header_fg)
        x_cursor = x1

    current_y = header_y2
    for idx, row in enumerate(prepared_rows, start=1):
        row_h = row_heights[idx - 1]
        y1 = current_y
        y2 = y1 + row_h

        status = row["_status"]
        if status == "تقصير":
            row_bg = red_bg
        elif status == "تبادل":
            row_bg = teal_bg
        elif status == "إشراف":
            row_bg = orange_bg
        else:
            row_bg = alt_bg if idx % 2 == 0 else white_bg

        row_values = [
            row["المعلم الغائب"],
            row["الصف"],
            row["الحصة"],
            row["المعلم البديل"],
        ]

        x_cursor = img_w - pad_x
        for (_, col_w), value in zip(columns, row_values):
            x1 = x_cursor - col_w
            x2 = x_cursor
            draw.rectangle((x1, y1, x2, y2), fill=row_bg, outline=border_color, width=1)

            if isinstance(value, list):
                content_h = (len(value) * line_height) + ((len(value) - 1) * 4)
                text_y = y1 + max(10, (row_h - content_h) / 2)
                draw_multiline_right(draw, x2 - 12, text_y, value, font_cell, text_dark, line_gap=4)
            else:
                _, text_h = text_size(value, font_cell)
                text_y = y1 + max(10, (row_h - text_h) / 2)
                draw_text_right(draw, x2 - 12, text_y, value, font_cell, text_dark)

            x_cursor = x1

        current_y = y2

    ensure_data_directories()
    filename = os.path.join(IMG_DIR, f"output_{day_name}_{target_date}_{datetime.datetime.now(tz_oman).strftime('%H%M%S_%f')}.png")
    image.save(filename)
    return filename

def generate_styled_html_table(df):
    if df is None or df.empty: return "<div style='text-align:center; color:gray; padding:20px; border: 1px dashed #ccc; border-radius: 10px;'>لا توجد تكليفات للعرض. اختر معلماً غائباً واضغط توليد.</div>"
    html = "<div style='overflow-x: auto; margin-top: 15px;'><table style='width: 100%; border-collapse: collapse; text-align: center; font-family: Cairo, Arial, sans-serif; direction: rtl; border: 1px solid #e5e7eb; box-shadow: 0 4px 6px rgba(0,0,0,0.05);'>"
    html += "<tr style='background-color: #004d40; color: white; font-size: 16px; border-bottom: 3px solid #ffca28;'><th style='padding: 15px;'>المعلم الغائب</th><th style='padding: 15px;'>الصف</th><th style='padding: 15px;'>الحصة</th><th style='padding: 15px;'>المعلم البديل</th></tr>"
    for index, row in df.iterrows():
        sub_teacher_display = str(row.get("المعلم البديل عرض", row["المعلم البديل"]))
        abs_teacher = str(row["المعلم الغائب"])
        status = row.get("حالة_التكليف", "")

        if status == "تقصير" or "❌" in sub_teacher_display: bg_color, text_color, border_style = "#ffebee", "#c62828", "border-bottom: 2px solid #ef9a9a;"
        elif status == "تبادل" or "🤝" in sub_teacher_display: bg_color, text_color, border_style = "#e0f2f1", "#00695c", "border-bottom: 2px solid #80cbc4;"
        elif "إشراف" in sub_teacher_display: bg_color, text_color, border_style = "#ffebee", "#c62828", "border-bottom: 2px solid #ef9a9a;"
        else: bg_color, text_color, border_style = "#f1f8e9" if index % 2 == 0 else "#ffffff", "#333333", "border-bottom: 1px solid #e5e7eb;"

        html += f"<tr style='background-color: {bg_color}; color: {text_color}; {border_style}'>"
        html += f"<td style='padding: 12px; font-size: 15px; font-weight: bold;'>{abs_teacher}</td>"
        html += f"<td style='padding: 12px; font-size: 15px; font-weight: bold;'>{row['الصف']}</td>"
        html += f"<td style='padding: 12px; font-size: 15px; font-weight: bold;'>{row['الحصة']}</td>"
        html += f"<td style='padding: 12px; font-size: 15px; font-weight: bold;'>{sub_teacher_display}</td></tr>"
    html += "</table></div>"
    return html

def format_sub_display(row):
    sub = str(row.get("المعلم البديل", ""))
    status = str(row.get("حالة_التكليف", ""))
    name_fmt = format_teacher_name(sub) if sub != "إشراف إداري" else sub
    if status == "تبادل": return f"{name_fmt} (تبادل 🤝)"
    elif status == "تقصير": return f"{name_fmt} (لم يُنفذ التكليف ❌)"
    return name_fmt

def generate_image_only(dept, day_name):
    effective_dept = resolve_effective_dept(dept)
    target_date = get_date_of_weekday(day_name)
    display_records = [r for r in daily_db if r["date"] == target_date and (effective_dept == "الكل" or r["dept"] == effective_dept)]
    df = pd.DataFrame(display_records, columns=["المعلم الغائب", "الصف", "الحصة", "المعلم البديل", "dept", "date", "حالة_التكليف"]).sort_values(["المعلم الغائب", "الحصة"])
    if not df.empty:
        df["المعلم البديل عرض"] = df.apply(format_sub_display, axis=1)
        df["المعلم الغائب"] = df["المعلم الغائب"].apply(format_teacher_name)
        img_path = draw_schedule_image(df, day_name)
        return gr.update(value=img_path)
    return gr.update(value=None)

# ✂️ المقص الرياضي الحاسم
def format_elegant_class(raw_class):
    raw_class = str(raw_class).strip()
    if not raw_class: return "الصف غير محدد"
    words = raw_class.split()
    if len(words) < 2: return raw_class 
    grade_part = ""
    subject_part = ""
    for i, word in enumerate(reversed(words)):
        if any(g in word for g in ["ثامن", "تاسع", "عاشر", "حادي", "ثاني", "1", "2", "3", "4", "5", "6", "7", "8", "9"]):
            grade_part = word
            subject_part = " ".join(words[:len(words) - 1 - i])
            break
    if grade_part and subject_part:
        return f"{grade_part} - مادة {subject_part}"
    return raw_class

def generate_whatsapp_html(df_state, day_name, absent_list):
    if df_state is None or df_state.empty: return "", "<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>"
    absents_str = "، ".join([format_teacher_name(a) for a in absent_list]) if absent_list else "لا يوجد"
    summary = f" ملخص احتياط اليوم: {day_name}\n المعلم الغائب: {absents_str}\n تم توزيع حصص الاحتياط بنجاح عبر منظومة الباسط.. يعطيكم العافية جميعاً! "
    html_cards = ""
    for _, row in df_state.iterrows():
        sub_raw = str(row["المعلم البديل"])
        abs_raw = str(row["المعلم الغائب"])
        status = str(row.get("حالة_التكليف", ""))
        
        if status == "تقصير" or "إشراف" in sub_raw: continue
        
        sub_fmt = format_teacher_name(sub_raw)
        abs_fmt = format_teacher_name(abs_raw)
 
        spec = teachers_db.get(sub_raw, {}).get("specialty", "")
        sub_display = f"{sub_fmt} [{spec}]" if spec else sub_fmt
        
        elegant_class = format_elegant_class(row['الصف'])
        
        if status == "تبادل":
            msg = f"أهلاً بك أستاذنا المتعاون 🤝 {sub_display}،\nتم اعتماد التكليف كحصة (تبادلية) للصف ({elegant_class}) في الحصة ({row['الحصة']})، بدلاً من الأستاذ {abs_fmt}.\nعلى أن يتم التنسيق بينكما ليعوض الأستاذ {abs_fmt} حصته.\nإدارة مدرسة الباسط تشكر لكم هذا التعاون المثمر! 💐"
            btn_color = "#00897b"
        else:
            msg = f"أهلاً بك أستاذنا المبدع 🌟 {sub_display}،\nتم تكليفك اليوم بمهمة قيادة الصف ({elegant_class}) في الحصة ({row['الحصة']})، بدلاً من الأستاذ {abs_fmt}.\nشاكرين لك مبادرتك وتعاونك الدائم! 💐\n- إدارة مدرسة الباسط"
            btn_color = "#25D366" if teachers_db.get(sub_raw, {}).get("phone", "") else "#075e54"
            
        encoded_msg = urllib.parse.quote(msg)
        phone = teachers_db.get(sub_raw, {}).get("phone", "")
        wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}" if phone else f"https://api.whatsapp.com/send?text={encoded_msg}"
        btn_text = f"✅ إرسال للأستاذ {sub_raw}" if phone else f"⚠️ إرسال (لا يوجد رقم)"
        
        card = f"<div style='background:#ffffff; border: 2px solid {btn_color}; border-radius: 10px; padding: 15px; margin-bottom: 15px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); direction: rtl; text-align: right;'><h4 style='color: {btn_color}; margin-top: 0; font-size: 1.1em;'>👤 {'المعلم المتعاون' if status=='تبادل' else 'المعلم البديل'}: {sub_display}</h4><p style='white-space: pre-wrap; font-size: 14px; background: #f1f8e9; padding: 10px; border-radius: 5px; color:#333; line-height: 1.6;'>{msg}</p><a href='{wa_link}' target='_blank' style='display: inline-block; background-color: {btn_color}; color: white; padding: 10px 20px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px;'>{btn_text}</a></div>"
        html_cards += card
    if not html_cards: html_cards = "<div style='text-align:center; color:gray; padding:20px; border: 1px dashed #ccc; border-radius: 10px;'>جميع التكليفات إدارية أو تقصير ولا توجد رسائل فردية للمكلفين.</div>"
    return summary, html_cards

def force_refresh_data(dept, day_name, is_admin_logged_in, current_abs):
    load_db()         
    load_daily_db()   
    return refresh_ui_on_change(dept, day_name, is_admin_logged_in, current_abs)
def get_empty_generation_state():
    return {
        "day": "",
        "dept": "",
        "absents": [],
        "signature": "",
        "generated": False,
        "has_results": False,
    }

def normalize_absent_names(absent_list):
    if not absent_list:
        return []

    if isinstance(absent_list, str):
        absent_list = [absent_list]

    cleaned = []
    for name in absent_list:
        raw = str(name).split(" (")[0].strip()
        if raw and raw not in cleaned:
            cleaned.append(raw)

    return sorted(cleaned)

def build_generation_signature(absent_list, day_name, dept_filter):
    cleaned = normalize_absent_names(absent_list)
    return f"{day_name}||{dept_filter}||{'|'.join(cleaned)}"

def same_generation_context(generation_state, day_name, dept_filter):
    return (
        isinstance(generation_state, dict)
        and generation_state.get("day") == day_name
        and generation_state.get("dept") == dept_filter
    )

def get_existing_absents_for_context(day_name, dept_filter):
    target_date = get_date_of_weekday(day_name)
    found = []

    for row in daily_db:
        if row.get("date") == target_date and (dept_filter == "الكل" or row.get("dept") == dept_filter):
            name = str(row.get("المعلم الغائب", "")).strip()
            if name and name not in found:
                found.append(name)

    return sorted(found)
    
def get_generation_button_updates(absent_list, day_name, dept_filter, generation_state):
    cleaned = normalize_absent_names(absent_list)
    has_selection = bool(cleaned)
    target_date = get_date_of_weekday(day_name)

    has_existing = any(
        r.get("date") == target_date and (dept_filter == "الكل" or r.get("dept") == dept_filter)
        for r in daily_db
    )

    same_context = same_generation_context(generation_state, day_name, dept_filter)

    prev_absents = normalize_absent_names(
        generation_state.get("absents", [])
    ) if same_context else []

    if has_existing and not prev_absents:
        prev_absents = get_existing_absents_for_context(day_name, dept_filter)

    selection_changed = set(cleaned) != set(prev_absents)

    # الأصفر: يعمل فقط إذا لا يوجد توليد سابق أو إذا تغيرت القائمة
    generate_enabled = has_selection and (not has_existing or selection_changed)

    # البرتقالي: ظاهر بعد أول توليد، لكنه لا يتفعل إلا عند تغير القائمة
    regen_visible = has_existing
    regen_enabled = has_existing and has_selection and selection_changed

    return (
        gr.update(interactive=generate_enabled),
        gr.update(visible=regen_visible, interactive=regen_enabled),
    )


@state_locked
def rollback_auto_assignments_for_absentees(absent_list, day_name, actor_name="", actor_role=""):
    global daily_db

    cleaned = set(normalize_absent_names(absent_list))
    if not cleaned or not day_name:
        return

    audit_entries = []
    target_date = get_date_of_weekday(day_name)
    kept_rows = []

    for row in daily_db:
        if row["date"] == target_date and row["المعلم الغائب"] in cleaned:
            old_sub = str(row.get("المعلم البديل", "")).replace(" 🔄", "").replace("🔄", "").strip()
            old_status = row.get("حالة_التكليف", "")

            if old_sub != "إشراف إداري" and old_sub in teachers_db and old_status == "":
                old_count = int(teachers_db[old_sub].get("cover_count", 0) or 0)
                new_count = max(0, old_count - 1)
                teachers_db[old_sub]["cover_count"] = new_count
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    old_sub,
                    old_count,
                    new_count,
                    f"إلغاء إسناد آلي أثناء إعادة التوليد ليوم {day_name}",
                )
            continue

        kept_rows.append(row)

    daily_db = kept_rows
    save_db()
    save_daily_db()
    _flush_audit_changes(audit_entries, actor_name, actor_role)

def clear_generated_image():
    return gr.update(value=None)




def show_school_data_panel(panel_name="overview"):
    panel = str(panel_name or "overview").strip()
    labels = {
        "overview": "يعرض مركز البيانات الآن حالة التخزين الدائم وملف إعدادات المدرسة فقط. اختر بطاقة من الأعلى لفتح القسم المطلوب.",
        "references": "🗂️ الملفات المرجعية: الجداول، أرقام المعلمين، الإداريون، والأدوات الإدارية الإضافية.",
        "identity": "🎨 هوية المدرسة: اسم المدرسة، المحافظة، الشعار، والألوان.",
        "accounts": "🔐 حسابات الدخول: الرموز، تفعيل الحسابات، وتخصيص الترحيب في صفحة واحدة.",
        "audit": "🛡️ السجل والنسخ: سجل العمليات الحساسة والنسخ الاحتياطية.",
    }

    show_overview_cards = panel == "overview"

    return (
        gr.update(value=f"<div class='school-data-panel-title'>{labels.get(panel, labels['overview'])}</div>"),
        gr.update(visible=show_overview_cards),
        gr.update(visible=show_overview_cards),
        gr.update(visible=(panel == "references")),
        gr.update(visible=(panel == "identity")),
        gr.update(visible=(panel == "accounts")),
        gr.update(visible=(panel == "audit")),
    )

def detect_absence_assignment_conflicts_for_context(day_name, dept_filter, current_abs=None):
    effective_dept = resolve_effective_dept(dept_filter)
    cleaned = normalize_absent_names(current_abs) if current_abs else []
    if not cleaned:
        cleaned = get_existing_absents_for_context(day_name, effective_dept)

    if not cleaned or not day_name:
        return []

    target_date = get_date_of_weekday(day_name)
    conflict_rows = {}

    for row in daily_db:
        if row.get("date") != target_date:
            continue
        if effective_dept != "الكل" and row.get("dept") != effective_dept:
            continue
        if str(row.get("المعلم البديل", "")).strip() in ["", "إشراف إداري"]:
            continue
        if row.get("حالة_التكليف") == "تقصير":
            continue

        sub_name = str(row.get("المعلم البديل", "")).split(" (")[0].strip()
        if sub_name not in cleaned:
            continue

        conflict_rows.setdefault(sub_name, [])
        period_val = str(row.get("الحصة", "")).strip()
        if period_val and period_val not in conflict_rows[sub_name]:
            conflict_rows[sub_name].append(period_val)

    conflicts = []
    for teacher_name, periods in conflict_rows.items():
        conflicts.append({
            "name": teacher_name,
            "periods": sorted(periods, key=lambda x: int(x) if str(x).isdigit() else str(x))
        })

    conflicts.sort(key=lambda item: item["name"])
    return conflicts


def build_absence_conflict_warning_html(conflicts, day_name):
    if not conflicts:
        return ""

    if len(conflicts) == 1:
        conflict = conflicts[0]
        formatted_name = format_teacher_name(conflict["name"])
        subject_text = f"المعلم الغائب <b>({formatted_name})</b> لديه حصص احتياط مسندة إليه مسبقًا في يوم <b>({day_name})</b>."
    else:
        names_text = "، ".join([format_teacher_name(item["name"]) for item in conflicts])
        subject_text = f"المعلمون الغائبون <b>({names_text})</b> لديهم حصص احتياط مسندة إليهم مسبقًا في يوم <b>({day_name})</b>."

    return (
        "<div style='background:#eaf4ff; color:#0f3d91; padding:15px; border-radius:10px; "
        "border:2px solid #64b5f6; text-align:center; font-weight:bold; font-size:15px; margin-top:12px; margin-bottom:15px;'>"
        "ℹ️ تنبيه تعارض في الاحتياط<br>"
        f"{subject_text}<br>"
        "لضمان صحة جميع الإسنادات، استخدم <b>\"لوحة القيادة\"</b> أو <b>\"إعادة توليد من جديد\"</b>."
        "</div>"
    )


def clean_teacher_name_from_ui(value):
    text = str(value or "").strip()
    for mark in ["🚨", "🔷", "✅", "⚠️", "🟦", "🦅"]:
        text = text.replace(mark, "")
    text = " ".join(text.split())
    if " (" in text:
        text = text.split(" (")[0].strip()
    return text.strip()

def detect_conflicted_absence_slots(display_records):
    absent_names = {
        str(r.get("المعلم الغائب", "")).split(" (")[0].strip()
        for r in display_records
        if str(r.get("المعلم الغائب", "")).strip()
    }

    conflicted_teachers = set()
    conflicted_slots = set()

    for row in display_records:
        sub_name = str(row.get("المعلم البديل", "")).split(" (")[0].strip()
        abs_name = str(row.get("المعلم الغائب", "")).split(" (")[0].strip()
        period_val = str(row.get("الحصة", "")).strip()

        if not sub_name or sub_name == "إشراف إداري":
            continue
        if row.get("حالة_التكليف") == "تقصير":
            continue

        if sub_name in absent_names:
            conflicted_teachers.add(abs_name)
            conflicted_slots.add((abs_name, period_val))

    return conflicted_teachers, conflicted_slots


def run_main_generation(absent_list, day_name, dept_filter, max_reserves, is_admin_logged_in, generation_state, actor_name="", actor_role=""):
    cleaned = normalize_absent_names(absent_list)

    if not cleaned or not day_name:
        ui = refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=cleaned)
        btn_upd, regen_upd = get_generation_button_updates(cleaned, day_name, dept_filter, generation_state)
        return tuple(ui) + (btn_upd, regen_upd, generation_state)

    same_context = same_generation_context(generation_state, day_name, dept_filter)
    prev_absents = normalize_absent_names(
        generation_state.get("absents", [])
    ) if same_context else []

    if not prev_absents:
        prev_absents = get_existing_absents_for_context(day_name, dept_filter)

    if not prev_absents:
        run_list = cleaned
    else:
        run_list = [name for name in cleaned if name not in prev_absents]

    if run_list:
        assign_logic(run_list, day_name, dept_filter, max_reserves, False, is_admin_logged_in, actor_name, actor_role)

    new_state = {
        "day": day_name,
        "dept": dept_filter,
        "absents": cleaned,
        "signature": build_generation_signature(cleaned, day_name, dept_filter),
        "generated": True,
        "has_results": True,
    }

    ui = list(refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=cleaned))
    btn_upd, regen_upd = get_generation_button_updates(cleaned, day_name, dept_filter, new_state)

    return tuple(ui) + (btn_upd, regen_upd, new_state)

def run_full_regeneration(absent_list, day_name, dept_filter, max_reserves, is_admin_logged_in, generation_state, actor_name="", actor_role=""):
    cleaned = normalize_absent_names(absent_list)

    if not cleaned or not day_name:
        ui = refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=cleaned)
        btn_upd, regen_upd = get_generation_button_updates(cleaned, day_name, dept_filter, generation_state)
        return tuple(ui) + (btn_upd, regen_upd, generation_state)

    rollback_auto_assignments_for_absentees(cleaned, day_name, actor_name, actor_role)
    assign_logic(cleaned, day_name, dept_filter, max_reserves, False, is_admin_logged_in, actor_name, actor_role)

    new_state = {
        "day": day_name,
        "dept": dept_filter,
        "absents": cleaned,
        "signature": build_generation_signature(cleaned, day_name, dept_filter),
        "generated": True,
        "has_results": True,
    }

    ui = refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=cleaned)
    btn_upd, regen_upd = get_generation_button_updates(cleaned, day_name, dept_filter, new_state)

    return tuple(ui) + (btn_upd, regen_upd, new_state)
    
def refresh_ui_on_change(dept, day_name, is_admin_logged_in, current_abs=None):
    if not teachers_db:
        load_db()
    if not daily_db:
        load_daily_db()

    effective_dept = resolve_effective_dept(dept)
    is_shared_teacher_view = str(dept or "").strip() == "المعلمون"
    target_date = get_date_of_weekday(day_name)
    display_records = [r for r in daily_db if r["date"] == target_date and (effective_dept == "الكل" or r["dept"] == effective_dept)]
    df = pd.DataFrame(display_records, columns=["المعلم الغائب", "الصف", "الحصة", "المعلم البديل", "dept", "date", "حالة_التكليف"]).sort_values(["المعلم الغائب", "الحصة"])
    
    if not df.empty:
        df["المعلم البديل عرض"] = df.apply(format_sub_display, axis=1)
        df["المعلم الغائب"] = df["المعلم الغائب"].apply(format_teacher_name)
    
    is_visible = not df.empty
    warning_html = ""
    
    if is_admin_logged_in:
        global_records = [r for r in daily_db if r["date"] == target_date]
        uncovered = len([r for r in global_records if r["المعلم البديل"] == "إشراف إداري"])
        if uncovered > 0: warning_html = f"<div style='background:#ffebee; color:#c62828; padding:15px; border-radius:10px; border:2px solid #c62828; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px; animation: pulse 2s infinite;'>🚨 رادار القيادة: بقي لديك ({uncovered}) حصص إشراف إداري تتطلب التدخل العاجل!</div>"
        else:
            if len(global_records) > 0: warning_html = f"<div style='background:#e8f5e9; color:#2e7d32; padding:15px; border-radius:10px; border:2px solid #2e7d32; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>✅ رادار القيادة: تم تأمين المدرسة بالكامل! جميع الحصص مغطاة.</div>"
            else: warning_html = f"<div style='background:#f1f8e9; color:#388e3c; padding:15px; border-radius:10px; border:1px dashed #388e3c; text-align:center; font-weight:bold; font-size:15px; margin-bottom:15px;'>🛡️ النظام جاهز: لا توجد حالات غياب مسجلة حتى الآن.</div>"
    else:
        uncovered = len([r for r in display_records if r["المعلم البديل"] == "إشراف إداري"])
        if uncovered > 0: warning_html = f"<div style='background:#fff3e0; color:#e65100; padding:15px; border-radius:10px; border:2px solid #e65100; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>⚠️ تنبيه للقسم: يوجد ({uncovered}) حصص غير مغطاة تم تحويلها للإدارة.</div>"
        else:
            if len(display_records) > 0: warning_html = f"<div style='background:#e8f5e9; color:#2e7d32; padding:15px; border-radius:10px; border:2px solid #2e7d32; text-align:center; font-weight:bold; font-size:16px; margin-bottom:15px;'>✅ اكتملت المهمة: تم تأمين جميع حصص القسم بنجاح.</div>"
            else: warning_html = f"<div style='background:#f1f8e9; color:#388e3c; padding:15px; border-radius:10px; border:1px dashed #388e3c; text-align:center; font-weight:bold; font-size:15px; margin-bottom:15px;'>🛡️ القسم جاهز: لا توجد حالات غياب.</div>"

    exhausted_msgs = []
    checked_exhausted = set()
    for r in display_records:
        sub = r["المعلم البديل"]
        if sub != "إشراف إداري" and r.get("حالة_التكليف") != "تقصير" and sub not in checked_exhausted:
            checked_exhausted.add(sub)
            if sub in teachers_db:
                base_p = {int(p) for p in teachers_db[sub].get(day_name, {}).keys()}
                sub_p = {int(r2["الحصة"]) for r2 in daily_db if r2["date"] == target_date and r2["المعلم البديل"] == sub and r2.get("حالة_التكليف") != "تقصير"}
                all_p = base_p | sub_p
                consecutive_groups = []
                for i in range(1, 7):
                    if i in all_p and i+1 in all_p and i+2 in all_p: consecutive_groups.append(f"{i}، {i+1}، {i+2}")
                if consecutive_groups:
                    grp_str = consecutive_groups[0]
                    exhausted_msgs.append(f"<li style='margin-bottom:5px;'>⚠️ الأستاذ <b>{sub}</b> سيدرس الحصص ({grp_str}) متتالية!</li>")
    
    if exhausted_msgs:
        radar_alert = f"<div style='background:#fff8e1; color:#e65100; padding:15px; border-radius:10px; border:2px solid #ffb74d; margin-bottom:15px; text-align:right;'><b style='font-size:16px;'>🫀 الرادار الإنساني (تنبيه إرهاق):</b><ul style='margin-top:8px; margin-bottom:0; padding-right:20px; font-size:14px;'>" + "".join(exhausted_msgs) + "</ul></div>"
        warning_html = radar_alert + warning_html

    persistent_conflict_html = ""
    if not is_shared_teacher_view:
        persistent_conflict_html = build_absence_conflict_warning_html(
            detect_absence_assignment_conflicts_for_context(day_name, effective_dept, current_abs),
            day_name,
        )
        if persistent_conflict_html:
            warning_html = warning_html + persistent_conflict_html

    actual_abs = sorted(list(set([r["المعلم الغائب"] for r in display_records])))
    conflicted_teachers, conflicted_slots = detect_conflicted_absence_slots(display_records)
    opts_abs = []
    
    if is_admin_logged_in:
        admin_title_val = "<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة العمليات الإدارية والقيادة العليا</h4><p style='text-align:center; color:#555; font-size:13px;'>صلاحيات مطلقة: يمكنك إسناد أي حصة لأي معلم، واعتماد التبادلات، ورصد التقصير.</p>"
        admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b; direction:rtl; text-align:right; line-height:1.9; font-weight:800;'>💡 <b>توضيح:</b><br>لإلغاء غياب معلم من اليوم بالكامل، اختر <b>المعلم الغائب</b> ثم اضغط زر <b>إلغاء غياب اليوم بالكامل</b>.<br>لعمل <b>تكليف احتياط رسمي</b> أو <b>اعتماد كتبادل</b>، اختر المعلم الغائب، ثم الحصة، ثم <b>البديل المنقذ</b>.<br>لعمل <b>رصد تقصير في التكليف</b>، اختر المعلم الغائب ثم الحصة، ثم اضغط زر <b>رصد تقصير في التكليف</b>.</div>"
        period_update = gr.update(choices=[], value=None, label="2️⃣ اختر الحصة", interactive=is_visible)
        cb_cross_update = gr.update(visible=False, value=False)
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            clean_c = str(c).split(" (")[0].strip()
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == clean_c)
            has_conflict_sup = clean_c in conflicted_teachers

            if has_admin_sup and has_conflict_sup:
                radar_icon = " 🚨🔷 "
            elif has_admin_sup:
                radar_icon = " 🚨 "
            elif has_conflict_sup:
                radar_icon = " 🔷 "
            else:
                radar_icon = " ✅ "

            opts_abs.append(f"{c} ({role}){radar_icon}" if role != "معلم" else f"{c}{radar_icon}")
    else:
        if is_shared_teacher_view:
            admin_title_val = "<h4 style='color:#004d40; text-align:center; margin-top:0;'>📘 التبادل الودي الأسبوعي</h4><p style='text-align:center; color:#555; font-size:13px;'>عرض التبادلات الودية الأسبوعية وجداول المدرسة المتاحة للحساب العام للمعلمين.</p>"
            admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b;'>💡 <b>توضيح:</b> هذا الحساب مخصص للعرض المحدود فقط للوصول إلى التبادل الودي الأسبوعي، جدول اليوم، وجدول المعلم الأسبوعي.</div>"
            period_update = gr.update(choices=[], value=None, label="2️⃣ الحصة", interactive=False)
            cb_cross_update = gr.update(visible=False, value=False, interactive=False)
        else:
            dept_leader_title = "المعلم الأول"
            for t_info in teachers_db.values():
                if str(t_info.get("dept", "")).strip() == str(dept).strip():
                    role = str(t_info.get("role", "")).strip()
                    if "منسق" in role:
                        dept_leader_title = "منسق المادة"
                        break
                    elif "معلم أول" in role:
                        dept_leader_title = "المعلم الأول"
                        break
            admin_title_val = f"<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة عمليات {dept_leader_title} ({dept})</h4><p style='text-align:center; color:#555; font-size:13px;'>استبدل المعلم الغائب بمعلم آخر، أو فعّل التعاون للوصول لأقسام أخرى.</p>"
            admin_help_val = "<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b; direction:rtl; text-align:right; line-height:1.9; font-weight:800;'>💡 <b>توضيح:</b><br>⚫️ لإلغاء غياب معلم من اليوم بالكامل، اختر <b>المعلم الغائب</b> ثم اضغط زر <b>إلغاء غياب اليوم بالكامل</b>.<br>⚫️ لعمل <b>تكليف احتياط رسمي</b> أو <b>اعتماد كتبادل</b>، اختر المعلم الغائب، ثم اختر الحصة، ثم اختر <b>البديل المنقذ</b> من نفس القسم.<br>⚫️ إذا لم يظهر بديل مناسب من نفس القسم، اختر <b>إشراف إداري</b>، أو فعّل خيار <b>التعاون مع قسم آخر</b> لتظهر لك بدائل من الأقسام الأخرى.<br>⚫️ لعمل <b>رصد تقصير في التكليف</b>، اختر المعلم الغائب ثم اختر الحصة، ثم اضغط زر <b>رصد تقصير في التكليف</b>.</div>"
            period_update = gr.update(choices=[], value=None, label="2️⃣ الحصة المراد تعديلها", interactive=is_visible)
            cb_cross_update = gr.update(visible=True, value=False, interactive=True)
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            clean_c = str(c).split(" (")[0].strip()
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == clean_c)
            has_conflict_sup = clean_c in conflicted_teachers

            if has_admin_sup and has_conflict_sup:
                radar_icon = " 🚨🔷 "
            elif has_admin_sup:
                radar_icon = " 🚨 "
            elif has_conflict_sup:
                radar_icon = " 🔷 "
            else:
                radar_icon = " ✅ "

            opts_abs.append(f"{c} ({role}){radar_icon}" if role != "معلم" else f"{c}{radar_icon}")
            
    t_names_filtered = sorted([t for t, d in teachers_db.items() if effective_dept == "الكل" or d.get("dept") == effective_dept])
    choices = get_teacher_choices(effective_dept) 
    teacher_schedule_choices = get_teacher_schedule_choices(effective_dept)
    abs_choices = get_absentee_choices(effective_dept)
    summary_txt, html_cards = generate_whatsapp_html(df, day_name, actual_abs) if not df.empty else ("", "<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>")
    styled_table_html = generate_styled_html_table(df)
    if isinstance(current_abs, str):
        current_abs = [current_abs]
    elif not current_abs:
        current_abs = []
 
    safe_abs_value = resolve_teacher_display_values(current_abs, abs_choices)
    fallback_abs_value = resolve_teacher_display_values(actual_abs, abs_choices)
    day_table_updates = get_day_table_updates(day_name, effective_dept, 0)
    
    return (
        gr.update(choices=abs_choices, value=safe_abs_value if safe_abs_value else fallback_abs_value),
        gr.update(value=get_updated_balance(effective_dept)),      
        gr.update(value=get_updated_absences(effective_dept)),     
        gr.update(value=get_updated_shortcomings(effective_dept)),
        *day_table_updates,
        gr.update(choices=t_names_filtered, value=None), 
        gr.update(choices=teacher_schedule_choices, value=None),          
        gr.update(choices=choices, value=None),          
        warning_html,                         
        gr.update(value=styled_table_html),              
        gr.update(choices=opts_abs, value=None),         
        df,                                         
        summary_txt,                                     
        html_cards,                                      
        get_dynamic_header(day_name),                    
        admin_title_val,
        gr.update(value=admin_help_val),
        period_update,                           
        cb_cross_update,
        gr.update(interactive=is_visible),               
        gr.update(interactive=is_visible)                
    )

@state_locked
def assign_logic(absent_list, day_name, dept_filter, max_reserves, is_alt, is_admin_logged_in, actor_name="", actor_role=""):
    global last_assigned_teachers, processed_absences, daily_db

    audit_entries = []

    if isinstance(absent_list, str):
        raw_absents = [absent_list]
    else:
        raw_absents = list(absent_list or [])

    absent_list_clean = []
    for item in raw_absents:
        clean_name = clean_teacher_name_from_ui(item)
        if clean_name and clean_name not in absent_list_clean:
            absent_list_clean.append(clean_name)

    target_date = get_date_of_weekday(day_name)

    existing_absent_today = []
    for row in daily_db:
        if row.get("date") != target_date:
            continue
        row_absent = clean_teacher_name_from_ui(row.get("المعلم الغائب", ""))
        if row_absent and row_absent not in existing_absent_today:
            existing_absent_today.append(row_absent)

    target_absents = []
    for name in absent_list_clean + existing_absent_today:
        if name and name not in target_absents:
            target_absents.append(name)

    if is_alt:
        records_to_keep = []
        records_to_delete = []

        for row in daily_db:
            row_absent = clean_teacher_name_from_ui(row.get("المعلم الغائب", ""))
            row_status = str(row.get("حالة_التكليف", "")).strip()
            should_replace_auto = (
                row.get("date") == target_date
                and row_absent in target_absents
                and row_status == ""
            )
            if should_replace_auto:
                records_to_delete.append(row)
            else:
                records_to_keep.append(row)

        daily_db = records_to_keep

        for row in records_to_delete:
            old_sub = clean_teacher_name_from_ui(row.get("المعلم البديل", ""))
            if old_sub != "إشراف إداري" and old_sub in teachers_db:
                old_count = int(teachers_db[old_sub].get("cover_count", 0) or 0)
                new_count = max(0, old_count - 1)
                teachers_db[old_sub]["cover_count"] = new_count
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    old_sub,
                    old_count,
                    new_count,
                    f"إلغاء إسناد آلي بسبب طلب مقترح آخر ليوم {day_name}",
                )

        generation_absents = target_absents
    else:
        generation_absents = absent_list_clean
        for abs_t in absent_list_clean:
            if (target_date, abs_t) not in processed_absences:
                if abs_t in teachers_db:
                    old_absent = int(teachers_db[abs_t].get("absent_count", 0) or 0)
                    new_absent = old_absent + 1
                    teachers_db[abs_t]["absent_count"] = new_absent
                    _queue_audit_change(
                        audit_entries,
                        "تعديل مرات الغياب",
                        abs_t,
                        old_absent,
                        new_absent,
                        f"تسجيل غياب يوم {day_name} ({target_date})",
                    )
                    if "absence_dates" not in teachers_db[abs_t]:
                        teachers_db[abs_t]["absence_dates"] = []
                    date_entry = f"{day_name} ({target_date})"
                    if date_entry not in teachers_db[abs_t]["absence_dates"] and target_date not in teachers_db[abs_t]["absence_dates"]:
                        teachers_db[abs_t]["absence_dates"].append(date_entry)
                processed_absences.add((target_date, abs_t))

    all_absent_today = set(target_absents if is_alt else (existing_absent_today + absent_list_clean))

    preserved_slots = {
        (
            clean_teacher_name_from_ui(row.get("المعلم الغائب", "")),
            str(row.get("الحصة", "")).strip()
        )
        for row in daily_db
        if row.get("date") == target_date
        and clean_teacher_name_from_ui(row.get("المعلم الغائب", "")) in all_absent_today
    }

    res, current_assigned = [], []
    daily_assigned_count = {t: 0 for t in teachers_db}
    assigned_periods_today = {t: set() for t in teachers_db}
    for r in daily_db:
        if r["date"] == target_date and r["المعلم البديل"] != "إشراف إداري" and r.get("حالة_التكليف") != "تقصير":
            t = r["المعلم البديل"]
            if t in daily_assigned_count:
                daily_assigned_count[t] += 1
                assigned_periods_today[t].add(int(r["الحصة"]))

    for abs_t in generation_absents:
        abs_dept = teachers_db.get(abs_t, {}).get("dept", "عام")
        for p_str, cl in teachers_db.get(abs_t, {}).get(day_name, {}).items():
            p_int = int(p_str)
            p_key = str(p_int)
            if (abs_t, p_key) in preserved_slots:
                continue

            cands = []
            for t, t_info in teachers_db.items():
                if t in all_absent_today:
                    continue
                if t_info.get("dept") != abs_dept:
                    continue
                if p_int in t_info.get(day_name, {}):
                    continue
                role = t_info.get("role", "معلم")
                if role in ADMIN_ROLES:
                    continue
                if p_int in assigned_periods_today[t]:
                    continue
                if daily_assigned_count[t] >= max_reserves:
                    continue
                if day_name in t_info.get("exempt_days", []):
                    continue
                if p_int in t_info.get("exempt_periods", []):
                    continue
                cands.append(t)
            if not cands:
                res.append({"المعلم الغائب": abs_t, "الصف": cl, "الحصة": str(p_int), "المعلم البديل": "إشراف إداري", "حالة_التكليف": ""})
            else:
                random.shuffle(cands)
                cands.sort(key=lambda t: teachers_db[t]["cover_count"])
                sel = cands[0]
                old_cover = int(teachers_db[sel].get("cover_count", 0) or 0)
                new_cover = old_cover + 1
                teachers_db[sel]["cover_count"] = new_cover
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    sel,
                    old_cover,
                    new_cover,
                    f"إسناد احتياط آلي بدل {abs_t} في الحصة {p_int} يوم {day_name}",
                )
                daily_assigned_count[sel] += 1
                assigned_periods_today[sel].add(p_int)
                current_assigned.append(sel)
                res.append({"المعلم الغائب": abs_t, "الصف": cl, "الحصة": str(p_int), "المعلم البديل": sel, "حالة_التكليف": ""})

    last_assigned_teachers = current_assigned
    save_db()
    for r in res:
        r["date"] = target_date
        r["dept"] = teachers_db.get(r["المعلم الغائب"], {}).get("dept", "عام")
        is_dup = any(
            x["date"] == r["date"] and
            x["المعلم الغائب"] == r["المعلم الغائب"] and
            x["الحصة"] == r["الحصة"]
            for x in daily_db
        )
        if not is_dup:
            daily_db.append(r)
    save_daily_db()
    _flush_audit_changes(audit_entries, actor_name, actor_role)
    return refresh_ui_on_change(
        dept_filter,
        day_name,
        is_admin_logged_in,
        current_abs=(target_absents if is_alt else absent_list_clean)
    )
    
@state_locked
def cancel_teacher_absence(abs_t, day_name, dept_filter, is_admin_logged_in, current_abs, actor_name="", actor_role=""):
    global daily_db, processed_absences, teachers_db
    if not abs_t or not day_name:
        return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    if not abs_t_clean:
        return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)

    audit_entries = []
    target_date = get_date_of_weekday(day_name)
    records_to_keep, records_to_delete = [], []

    for r in daily_db:
        row_date = r.get("date")
        row_absent = str(r.get("المعلم الغائب", "")).split(" (")[0].strip()
        if row_date == target_date and row_absent == abs_t_clean:
            records_to_delete.append(r)
        else:
            records_to_keep.append(r)

    daily_db = records_to_keep

    for r in records_to_delete:
        sub = str(r.get("المعلم البديل", "")).replace(" 🔄", "").replace("🔄", "").strip()
        status = r.get("حالة_التكليف", "")
        if sub != "إشراف إداري" and sub in teachers_db and status == "":
            old_cover = int(teachers_db[sub].get("cover_count", 0) or 0)
            new_cover = max(0, old_cover - 1)
            teachers_db[sub]["cover_count"] = new_cover
            _queue_audit_change(
                audit_entries,
                "تعديل رصيد الاحتياط",
                sub,
                old_cover,
                new_cover,
                f"إلغاء احتياط بسبب إلغاء غياب {abs_t_clean} يوم {day_name}",
            )

    if abs_t_clean in teachers_db:
        old_absent = int(teachers_db[abs_t_clean].get("absent_count", 0) or 0)
        new_absent = max(0, old_absent - 1)
        teachers_db[abs_t_clean]["absent_count"] = new_absent
        _queue_audit_change(
            audit_entries,
            "تعديل مرات الغياب",
            abs_t_clean,
            old_absent,
            new_absent,
            f"إلغاء غياب يوم {day_name} ({target_date})",
        )
        date_entry = f"{day_name} ({target_date})"
        if "absence_dates" in teachers_db[abs_t_clean]:
            if date_entry in teachers_db[abs_t_clean]["absence_dates"]:
                teachers_db[abs_t_clean]["absence_dates"].remove(date_entry)
            elif target_date in teachers_db[abs_t_clean]["absence_dates"]:
                teachers_db[abs_t_clean]["absence_dates"].remove(target_date)

    if (target_date, abs_t_clean) in processed_absences:
        processed_absences.remove((target_date, abs_t_clean))

    save_db()
    save_daily_db()
    _flush_audit_changes(audit_entries, actor_name, actor_role)

    updated_abs = []
    if current_abs:
        updated_abs = [t for t in current_abs if clean_teacher_name_from_ui(t) != abs_t_clean]

    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=updated_abs)
def cancel_teacher_absence_with_generation_state(abs_t, day_name, dept_filter, is_admin_logged_in, current_abs, actor_name="", actor_role=""):
    ui = cancel_teacher_absence(abs_t, day_name, dept_filter, is_admin_logged_in, current_abs, actor_name, actor_role)

    remaining_absents = get_existing_absents_for_context(day_name, dept_filter)

    if remaining_absents:
        new_state = {
            "day": day_name,
            "dept": dept_filter,
            "absents": remaining_absents,
            "signature": build_generation_signature(remaining_absents, day_name, dept_filter),
            "generated": True,
            "has_results": True,
        }
    else:
        new_state = get_empty_generation_state()

    btn_upd, regen_upd = get_generation_button_updates(
        remaining_absents,
        day_name,
        dept_filter,
        new_state
    )

    return tuple(ui) + (btn_upd, regen_upd, new_state)
    
def on_abs_t_change(df_state, abs_t, is_admin_logged_in):
    if not abs_t or df_state is None or df_state.empty:
        cb_update = gr.update(visible=False, value=False) if is_admin_logged_in else gr.update(visible=True, value=False)
        return gr.update(choices=[], value=None), gr.update(choices=[], value=None), cb_update
    
    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    periods_elegant = []
    df_filtered = df_state[
        df_state["المعلم الغائب"].apply(lambda x: str(x).split(" (")[0].strip()) == abs_t_clean
    ]
    _, conflicted_slots = detect_conflicted_absence_slots(df_state.to_dict("records"))

    for _, row in df_filtered.iterrows():
        elegant_class = format_elegant_class(row['الصف'])
        is_admin_sup = str(row.get("المعلم البديل", "")) == "إشراف إداري"
        slot_key = (abs_t_clean, str(row['الحصة']).strip())

        if is_admin_sup:
            radar_icon = " 🚨 "
        elif slot_key in conflicted_slots:
            radar_icon = " 🔷 "
        else:
            radar_icon = " ✅ "

        display_text = f"الحصة {row['الحصة']} - ({elegant_class}){radar_icon}"
        periods_elegant.append(display_text)
        
    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    base_choice = f"نفس القسم ({abs_dept})"
    if is_admin_logged_in:
        choices = [base_choice, "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية", "معلمو الصف", "الهيئة التدريسية", "الهيئة الإدارية"]
        filtered = [c for c in choices if not (c in OFFICIAL_DEPTS and c == abs_dept)]
        return gr.update(choices=periods_elegant, value=None), gr.update(choices=filtered, value=base_choice, interactive=True), gr.update(visible=False, value=False)
    else:
        return gr.update(choices=periods_elegant, value=None), gr.update(choices=[base_choice], value=base_choice, interactive=False), gr.update(visible=True, value=False)
        
def toggle_cross_dept(is_checked, abs_t):
    if not abs_t: return gr.update()
    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    base_choice = f"نفس القسم ({abs_dept})"
    if is_checked:
        choices = [base_choice, "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية", "معلمو الصف", "الهيئة التدريسية"]
        filtered = [c for c in choices if not (c in OFFICIAL_DEPTS and c == abs_dept)]
        return gr.update(choices=filtered, value=base_choice, interactive=True)
    else:
        return gr.update(choices=[base_choice], value=base_choice, interactive=False)

def is_teacher_exempt_for_slot(teacher_name, day_name, period_int):
    teacher_name = str(teacher_name or "").split(" (")[0].strip()
    info = teachers_db.get(teacher_name, {})
    exempt_days = info.get("exempt_days", []) or []
    exempt_periods = info.get("exempt_periods", []) or []

    try:
        exempt_periods = [int(p) for p in exempt_periods]
    except Exception:
        exempt_periods = [p for p in exempt_periods]

    if day_name in exempt_days:
        return True
    if period_int in exempt_periods:
        return True
    return False

def update_available_subs_smart(abs_t, period, intervention_type, day_name, df_state, is_admin):
    # 1 — لم يُختر معلم بعد
    if not abs_t:
        return gr.update(choices=[], value=None, interactive=False)

    # 2 — لم تُختر الحصة بعد
    if not period:
        return gr.update(
            choices=["ℹ️ اختر الحصة أولًا"],
            value="ℹ️ اختر الحصة أولًا",
            interactive=False
        )

    fallback_msg = "⚠️ لا يوجد بديل متاح"
    fallback = gr.update(
        choices=[fallback_msg],
        value=fallback_msg,
        interactive=False
    )

    if not day_name or not intervention_type:
        return fallback

    try:
        p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
        p_int = int(p_str_clean)
    except:
        return fallback

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    target_date = get_date_of_weekday(day_name)
    already_subbing, absent_today = set(), set()
    if df_state is not None and not df_state.empty:
        subs = df_state[df_state["الحصة"] == str(p_int)]["المعلم البديل"].tolist()
        already_subbing.update(subs)
        absent_today.update(
            df_state["المعلم الغائب"].apply(lambda x: str(x).split(" (")[0].strip()).tolist()
        )

    # ✅ استبعاد المعلمين المكلفين في نفس الحصة من جميع الأقسام
    for r in daily_db:
        if r["date"] == target_date and r["الحصة"] == str(p_int) and r.get("حالة_التكليف") != "تقصير":
            already_subbing.add(r["المعلم البديل"])

    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    target_dept = intervention_type
    if "نفس القسم" in target_dept:
        target_dept = abs_dept

    def no_result_update(label):
        msg = f"⚠️ لا يوجد بديل متاح من {label}"
        return gr.update(
            choices=[msg],
            value=msg,
            interactive=False
        )

    def admin_supervision_only_update():
        return gr.update(
            choices=["إشراف إداري"],
            value=None,
            interactive=True
        )

    opts = []

    # 🚀 الهيئة التدريسية (يستبعد الإداريين)
    if target_dept == "الهيئة التدريسية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today:
                continue
            if is_teacher_exempt_for_slot(t, day_name, p_int):
                continue
            if info.get("dept") == "الهيئة الإدارية":
                continue
            if p_int not in info.get(day_name, {}):
                available_cands.append(t)

        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            c_dept = teachers_db[c].get("dept", "عام")
            warn_str = check_teacher_load(c, day_name, p_int)
            warn_str = f" ⚠️ {warn_str}" if warn_str else ""
            opts.append(f"{c} ({c_dept}){warn_str}")

        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None, interactive=True)

    # 🚀 الهيئة الإدارية (خاص بالمدير)
    if target_dept == "الهيئة الإدارية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today:
                continue
            if is_teacher_exempt_for_slot(t, day_name, p_int):
                continue
            if info.get("dept") == "الهيئة الإدارية" and p_int not in info.get(day_name, {}):
                available_cands.append(t)

        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            role = teachers_db[c].get("role", "إداري")
            opts.append(f"{c} ({role})")

        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None, interactive=True)

    # --- بقية الأقسام ومعلمو الصف ---
    falcon_cands = get_falcon_eye_candidates(abs_t_clean, period, day_name)

    if target_dept != abs_dept:
        for cand_str in falcon_cands:
            name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
            if is_teacher_exempt_for_slot(name_part, day_name, p_int):
                continue
            if name_part not in already_subbing and name_part not in absent_today:
                if target_dept == "معلمو الصف" or teachers_db.get(name_part, {}).get("dept") == target_dept:
                    opts.append(cand_str)
        if not opts:
            if not is_admin:
                return admin_supervision_only_update()
            return no_result_update(target_dept)
        if not is_admin:
            opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None, interactive=True)

    for cand_str in falcon_cands:
        name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
        if is_teacher_exempt_for_slot(name_part, day_name, p_int):
            continue
        if name_part not in already_subbing and name_part not in absent_today and teachers_db.get(name_part, {}).get("dept") == abs_dept:
            opts.append(cand_str)

    available_cands = []
    for t, info in teachers_db.items():
        if t == abs_t_clean or t in already_subbing or t in absent_today:
            continue
        if is_teacher_exempt_for_slot(t, day_name, p_int):
            continue
        if p_int not in info.get(day_name, {}):
            if info.get("dept") == target_dept:
                available_cands.append(t)

    available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
    for c in available_cands:
        is_falcon = False
        for opt in opts:
            if c in opt:
                is_falcon = True
                break
        if is_falcon:
            continue

        warn_str = check_teacher_load(c, day_name, p_int)
        warn_str = f" ⚠️ {warn_str}" if warn_str else ""
        opts.append(f"{c} ({abs_dept}){warn_str}")

    if not opts:
        if not is_admin:
            return admin_supervision_only_update()
        return no_result_update(target_dept)
    if not is_admin:
        opts.append("إشراف إداري")
    return gr.update(choices=opts, value=None, interactive=True)

@state_locked
def process_admin_action(df_state, abs_t, period, new_sub, day_name, dept_filter, is_admin_logged_in, current_abs, action_type, actor_name="", actor_role=""):
    global daily_db
    if df_state is None or df_state.empty or not abs_t or not period:
        return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
    if action_type != "penalty":
        if not new_sub or str(new_sub).startswith("⚠️") or str(new_sub).startswith("ℹ️"):
            return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
    target_date = get_date_of_weekday(day_name)
    audit_entries = []

    abs_t_clean = clean_teacher_name_from_ui(abs_t)
    p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()

    for r in daily_db:
        if r["date"] == target_date and r["المعلم الغائب"] == abs_t_clean and r["الحصة"] == p_str_clean:
            old_sub = r["المعلم البديل"]
            old_status = r.get("حالة_التكليف", "")

            if action_type == "penalty":
                target_sub = old_sub
            else:
                if not new_sub:
                    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
                if new_sub == "إشراف إداري":
                    target_sub = new_sub
                else:
                    target_sub = new_sub.split(" (")[0].replace("🦅 ", "").strip()

            if old_sub == target_sub and action_type == "normal" and old_status == "":
                break

            if old_sub != "إشراف إداري" and old_sub in teachers_db and old_status == "":
                old_count = int(teachers_db[old_sub].get("cover_count", 0) or 0)
                new_count = max(0, old_count - 1)
                teachers_db[old_sub]["cover_count"] = new_count
                _queue_audit_change(
                    audit_entries,
                    "تعديل رصيد الاحتياط",
                    old_sub,
                    old_count,
                    new_count,
                    f"إلغاء تكليف سابق للحصة {p_str_clean} يوم {day_name}",
                )

            if action_type == "penalty":
                if target_sub != "إشراف إداري" and old_status != "تقصير" and target_sub in teachers_db:
                    old_short = int(teachers_db[target_sub].get("shortcoming_count", 0) or 0)
                    new_short = old_short + 1
                    teachers_db[target_sub]["shortcoming_count"] = new_short
                    _queue_audit_change(
                        audit_entries,
                        "تعديل حالات التقصير",
                        target_sub,
                        old_short,
                        new_short,
                        f"رصد تقصير في تكليف الحصة {p_str_clean} يوم {day_name}",
                    )
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = "تقصير"

            elif action_type == "tabadul":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = "تبادل"

            elif action_type == "normal":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = ""
                if target_sub != "إشراف إداري" and target_sub in teachers_db:
                    old_count = int(teachers_db[target_sub].get("cover_count", 0) or 0)
                    new_count = old_count + 1
                    teachers_db[target_sub]["cover_count"] = new_count
                    _queue_audit_change(
                        audit_entries,
                        "تعديل رصيد الاحتياط",
                        target_sub,
                        old_count,
                        new_count,
                        f"تكليف احتياط رسمي للحصة {p_str_clean} يوم {day_name}",
                    )

            save_db()
            save_daily_db()
            _flush_audit_changes(audit_entries, actor_name, actor_role)
            break
    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
    
def load_teacher_data_for_edit(selected_teacher, is_admin=False, is_owner=False):
    permissions = get_permissions_from_flags(is_admin=is_admin, is_owner=is_owner)
    can_edit_vault = permissions["can_edit_vault_basic"]
    owner_mode = permissions["can_edit_sensitive_teacher_data"]

    if selected_teacher and selected_teacher in teachers_db: 
        dept = teachers_db[selected_teacher].get("dept", "عام")
        spec = teachers_db[selected_teacher].get("specialty", "")
        role = teachers_db[selected_teacher].get("role", "معلم")
        is_admin_staff = dept == "الهيئة الإدارية"
        is_spec_visible = dept in ["العلوم", "المهارات الفردية"]
        return (
            gr.update(value=dept, visible=not is_admin_staff),
            gr.update(value=teachers_db[selected_teacher].get("cover_count", 0), interactive=can_edit_vault),
            gr.update(value=teachers_db[selected_teacher].get("absent_count", 0), interactive=can_edit_vault),
            gr.update(value=teachers_db[selected_teacher].get("shortcoming_count", 0), interactive=can_edit_vault),
            gr.update(value=teachers_db[selected_teacher].get("phone", ""), interactive=owner_mode),
            gr.update(value=spec, visible=is_spec_visible and not is_admin_staff, interactive=owner_mode),
            gr.update(value=role, interactive=owner_mode)
        )

    return (
        gr.update(value="", visible=True),
        gr.update(value=0, interactive=can_edit_vault),
        gr.update(value=0, interactive=can_edit_vault),
        gr.update(value=0, interactive=can_edit_vault),
        gr.update(value="", interactive=owner_mode),
        gr.update(value="", interactive=owner_mode),
        gr.update(value="معلم", interactive=owner_mode)
    )
    
def toggle_specialty_visibility(dept): return gr.update(visible=dept in ["العلوم", "المهارات الفردية"])

@state_locked
def update_manual_count(name, new_val, new_abs_val, new_short_val, new_phone, new_specialty, new_role, dept_filter, day_val, df_state, abs_in_list, is_admin=False, is_owner=False, actor_name="", actor_role=""):
    permissions = get_permissions_from_flags(is_admin=is_admin, is_owner=is_owner)
    can_edit_vault = permissions["can_edit_vault_basic"]
    owner_mode = permissions["can_edit_sensitive_teacher_data"]

    if not can_edit_vault:
        return (
            gr.update(value=get_updated_balance(dept_filter)),
            gr.update(value=get_updated_absences(dept_filter)),
            gr.update(value=get_updated_shortcomings(dept_filter)),
            gr.update(value=get_day_overview(day_val, dept_filter)),
            "<div style='color:#c62828; font-weight:bold; background:#ffebee; padding:10px; border-radius:5px; text-align:center;'>❌ لا تملك صلاحية تعديل الخزنة.</div>",
            gr.update(),
            gr.update(),
            gr.update()
        )

    if name and name in teachers_db:
        old_cover_count = teachers_db[name].get("cover_count", 0)
        old_absent_count = teachers_db[name].get("absent_count", 0)
        old_shortcoming_count = teachers_db[name].get("shortcoming_count", 0)

        if new_val is not None:
            try:
                parsed_cover = int(new_val)
                if parsed_cover != old_cover_count:
                    teachers_db[name]["cover_count"] = parsed_cover
                    write_audit_log(
                        "تعديل رصيد الاحتياط",
                        target_teacher=name,
                        old_value=old_cover_count,
                        new_value=parsed_cover,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if new_abs_val is not None:
            try:
                parsed_absent = int(new_abs_val)
                if parsed_absent != old_absent_count:
                    teachers_db[name]["absent_count"] = parsed_absent
                    write_audit_log(
                        "تعديل مرات الغياب",
                        target_teacher=name,
                        old_value=old_absent_count,
                        new_value=parsed_absent,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if new_short_val is not None:
            try:
                parsed_short = int(new_short_val)
                if parsed_short != old_shortcoming_count:
                    teachers_db[name]["shortcoming_count"] = parsed_short
                    write_audit_log(
                        "تعديل حالات التقصير",
                        target_teacher=name,
                        old_value=old_shortcoming_count,
                        new_value=parsed_short,
                        details="تعديل من الخزنة",
                        actor_name=actor_name,
                        actor_role=actor_role
                    )
            except Exception:
                pass

        if owner_mode and new_phone is not None:
            phone_clean = re.sub(r'\D', '', str(new_phone))
            if phone_clean:
                if len(phone_clean) == 8: phone_clean = "968" + phone_clean
                teachers_db[name]["phone"] = phone_clean
            else:
                teachers_db[name]["phone"] = ""
        if owner_mode and new_specialty is not None:
            teachers_db[name]["specialty"] = str(new_specialty).strip()
        if owner_mode and new_role is not None:
            teachers_db[name]["role"] = str(new_role).strip() 
        save_db()
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        permission_note = "" if owner_mode else "<br><span style='color:#6b7280;'>ℹ️ تم تجاهل تعديل المنصب ورقم الواتساب والتخصص الدقيق لأن هذه الحقول مخصصة لصاحب النظام فقط.</span>"
        return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_updated_shortcomings(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم حفظ التعديلات للأستاذ ({name}) بنجاح!{permission_note}</div>", gr.update(choices=abs_choices), gr.update(choices=choices_all, value=None), gr.update(choices=choices_all, value=None))
    return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_updated_shortcomings(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), "<div style='color:red;'>❌ لم يتم الحفظ</div>", gr.update(), gr.update(), gr.update())

@state_locked
def delete_single_teacher(name, dept_filter, day_val, is_owner=False):
    global teachers_db
    if not bool(is_owner):
        return (gr.update(), gr.update(), gr.update(), gr.update(), "<div style='color:red;'>❌ حذف السجل متاح لمالك النظام فقط.</div>", gr.update(), gr.update(), gr.update(), gr.update())
    if name and name in teachers_db:
        del teachers_db[name]
        save_db()
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        msg = f"<div style='color:#c62828; font-weight:bold; background:#ffebee; padding:10px; border-radius:5px; text-align:center;'>🗑️ تم حذف ({name}) نهائياً من النظام!</div>"
        return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_updated_shortcomings(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), msg, gr.update(choices=abs_choices), gr.update(choices=choices_all, value=None), gr.update(choices=choices_all, value=None), gr.update(choices=list(teachers_db.keys()), value=None))
    return (gr.update(), gr.update(), gr.update(), gr.update(), "<div style='color:red;'>❌ المعلم غير موجود</div>", gr.update(), gr.update(), gr.update(), gr.update())


def resolve_teacher_key_from_ui(value):
    raw = str(value or "").strip()
    if not raw:
        return ""

    cleaned = clean_teacher_name_from_ui(raw)

    if cleaned in teachers_db:
        return cleaned

    if raw in teachers_db:
        return raw

    for key in teachers_db.keys():
        if str(raw).startswith(str(key) + " (") or str(cleaned).startswith(str(key) + " ("):
            return key

    try:
        target_fp = get_name_fingerprint(cleaned)
        for key in teachers_db.keys():
            if get_name_fingerprint(key) == target_fp:
                return key
    except Exception:
        pass

    return cleaned

def load_teacher_rules(t_name):
    t_key = resolve_teacher_key_from_ui(t_name)
    if t_key and t_key in teachers_db:
        return (
            gr.update(value=teachers_db[t_key].get("exempt_days", [])),
            gr.update(value=teachers_db[t_key].get("exempt_periods", []))
        )
    return gr.update(value=[]), gr.update(value=[])

@state_locked
def save_teacher_rules(t_name, days, periods, actor_name="", actor_role="", is_admin=False, is_owner=False):
    permissions = get_permissions(role=actor_role, is_owner=is_owner, is_admin_flag=is_admin)
    if not permissions["can_manage_exemptions"]:
        return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ لا تملك صلاحية تعديل حالات الإعفاء.</div>", gr.update(value=render_exemptions_log_html())

    t_key = resolve_teacher_key_from_ui(t_name)
    if t_key and t_key in teachers_db:
        if teachers_db[t_key].get("dept") == "الهيئة الإدارية" or teachers_db[t_key].get("role", "معلم") in ADMIN_ROLES:
            return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ لا يمكن تسجيل حالات إعفاء للهيئة الإدارية أو الإداريين.</div>", gr.update(value=render_exemptions_log_html())
        clean_days = list(days) if days else []
        clean_periods = []
        for p in (periods or []):
            try:
                clean_periods.append(int(p))
            except Exception:
                continue

        old_days = list(teachers_db[t_key].get("exempt_days", []) or [])
        old_periods = list(teachers_db[t_key].get("exempt_periods", []) or [])

        teachers_db[t_key]["exempt_days"] = clean_days
        teachers_db[t_key]["exempt_periods"] = clean_periods

        if old_days != clean_days or old_periods != clean_periods:
            write_audit_log(
                "تعديل حالات الإعفاء",
                target_teacher=t_key,
                old_value={"days": old_days, "periods": old_periods},
                new_value={"days": clean_days, "periods": clean_periods},
                details="تعديل أيام/حصص الإعفاء",
                actor_name=actor_name,
                actor_role=actor_role
            )

        if clean_days or clean_periods:
            teachers_db[t_key]["exemption_updated_at"] = get_now_oman().strftime("%Y-%m-%d %H:%M")
            status_html = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم تثبيت قوانين الإعفاء للأستاذ ({format_teacher_name(t_key)}) بنجاح!</div>"
        else:
            teachers_db[t_key]["exemption_updated_at"] = ""
            status_html = f"<div style='color:#b45309; font-weight:bold; background:#fff7ed; padding:10px; border-radius:5px; text-align:center;'>ℹ️ تم إلغاء إعفاءات الأستاذ ({format_teacher_name(t_key)}) لأنه لا توجد أيام أو حصص محددة.</div>"

        save_db()
        return status_html, gr.update(value=render_exemptions_log_html())

    return "<div style='color:#b91c1c; font-weight:bold; background:#fee2e2; padding:10px; border-radius:5px; text-align:center;'>❌ اختر معلمًا أولًا قبل حفظ الإعفاء.</div>", gr.update(value=render_exemptions_log_html())

def export_excel_report(dept_filter):
    data = []
    for t, d in teachers_db.items():
        effective_dept = resolve_effective_dept(dept_filter)
        if effective_dept == "الكل" or d.get("dept") == effective_dept:
            absence_dates_str = " \n ".join(d.get("absence_dates", [])) if d.get("absence_dates") else "-"
            data.append({
                "المعلم": format_teacher_name(t), 
                "المنصب": d.get("role", "معلم"), 
                "القسم": d.get("dept", "عام"),
                "التخصص الدقيق": d.get("specialty", "-"), 
                "رصيد الاحتياط": d.get("cover_count", 0),
                "مرات الغياب": d.get("absent_count", 0),
                "أيام وتواريخ الغياب": absence_dates_str,
                "حالات التقصير في الاحتياط": d.get("shortcoming_count", 0),
                "رقم الهاتف": d.get("phone", "")
            })
            
    df = pd.DataFrame(data).sort_values("رصيد الاحتياط", ascending=False) if data else pd.DataFrame(columns=["المعلم", "المنصب", "القسم", "التخصص الدقيق", "رصيد الاحتياط", "مرات الغياب", "أيام وتواريخ الغياب", "حالات التقصير في الاحتياط", "رقم الهاتف"])
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"تقرير_العدالة_والغياب_{timestamp}.xlsx"
    with pd.ExcelWriter(filename, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='تقرير المدرسة')
        worksheet = writer.sheets['تقرير المدرسة']
        worksheet.sheet_view.rightToLeft = True
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="004D40", end_color="004D40", fill_type="solid")
        for col in worksheet.columns:       # ← داخل الكتلة ✅
            max_length = 0
            column = col[0].column_letter
            for cell in col:
                try:
                    if len(str(cell.value)) > max_length: max_length = len(str(cell.value))
                except: pass
                cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                if cell.row == 1:
                    cell.font = header_font
                    cell.fill = header_fill
            adjusted_width = min(max_length + 4, 40)
            worksheet.column_dimensions[column].width = adjusted_width
    return gr.update(value=filename)

@state_locked
def reset_monthly_balances(dept_filter, day_val, is_admin=False, is_owner=False, actor_name="", actor_role=""):
    global daily_db, processed_absences, last_assigned_teachers

    permissions = get_permissions(role=actor_role, is_owner=is_owner, is_admin_flag=is_admin)
    if not permissions["can_close_month"]:
        return (
            gr.update(value=get_updated_balance(dept_filter)),
            gr.update(value=get_updated_absences(dept_filter)),
            gr.update(value=get_updated_shortcomings(dept_filter)),
            gr.update(value=get_day_overview(day_val, dept_filter)),
            "<div style='color:#c62828; font-weight:bold; background:#ffebee; padding:12px; border-radius:8px; text-align:center;'>❌ إقفال الشهر متاح لمالك النظام والإدارة فقط.</div>"
        )

    old_cover = {t: int(info.get("cover_count", 0) or 0) for t, info in teachers_db.items() if int(info.get("cover_count", 0) or 0) != 0}
    old_absent = {t: int(info.get("absent_count", 0) or 0) for t, info in teachers_db.items() if int(info.get("absent_count", 0) or 0) != 0}
    old_short = {t: int(info.get("shortcoming_count", 0) or 0) for t, info in teachers_db.items() if int(info.get("shortcoming_count", 0) or 0) != 0}

    for t in teachers_db:
        teachers_db[t]["cover_count"] = 0
        teachers_db[t]["absent_count"] = 0
        teachers_db[t]["absence_dates"] = []
        teachers_db[t]["shortcoming_count"] = 0

    save_db()

    daily_db = []
    processed_absences = set()
    last_assigned_teachers = []
    save_daily_db()

    if old_cover:
        write_audit_log("تعديل رصيد الاحتياط", "جميع المعلمين", old_cover, 0, "إقفال الشهر وتصفير أرصدة الاحتياط", actor_name, actor_role)
    if old_absent:
        write_audit_log("تعديل مرات الغياب", "جميع المعلمين", old_absent, 0, "إقفال الشهر وتصفير مرات وتواريخ الغياب", actor_name, actor_role)
    if old_short:
        write_audit_log("تعديل حالات التقصير", "جميع المعلمين", old_short, 0, "إقفال الشهر وتصفير حالات التقصير", actor_name, actor_role)

    msg = "<div style='color:#1565c0; font-weight:bold; background:#e3f2fd; padding:15px; border-radius:10px; text-align:center; margin-bottom:10px;'>✅ تم إقفال الشهر بنجاح! تم حفظ نسخ احتياطية ثم تصفير الأرصدة والغياب والتقصير.</div>"

    return (
        gr.update(value=get_updated_balance(dept_filter)),
        gr.update(value=get_updated_absences(dept_filter)),
        gr.update(value=get_updated_shortcomings(dept_filter)),
        gr.update(value=get_day_overview(day_val, dept_filter)),
        msg
    )
    
@state_locked
def clear_all_data(is_owner_logged_in):
    global teachers_db, daily_db, processed_absences, last_assigned_teachers

    empty_balance_df = pd.DataFrame(columns=["المعلم", "الرصيد"])
    empty_absence_df = pd.DataFrame(columns=["المعلم", "مرات الغياب"])
    empty_shortcomings_html = render_compact_rtl_table_html(
        pd.DataFrame(columns=["المعلم", "حالات التقصير"]),
        "لا توجد حالات تقصير مسجلة للعرض."
    )
    empty_day_df = pd.DataFrame(columns=["المعلم"] + [f"ح {p}" for p in range(1, MAX_PERIODS + 1)])
    empty_day_html, _, _, _ = render_day_table_html(empty_day_df, 0, PAGE_SIZE)
    empty_generation_state = get_empty_generation_state()

    if not is_owner_logged_in:
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            "<div style='color:red; font-weight:bold;'>❌ هذه العملية متاحة لمالك النظام فقط.</div>",
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update()
        )

    teachers_db = {}
    daily_db = []
    processed_absences = set()
    last_assigned_teachers = []

    # الحفظ الآمن ينشئ نسخة احتياطية قبل كتابة الحالة الفارغة.
    save_db()
    save_daily_db()

    return (
        gr.update(choices=["الكل"] + OFFICIAL_DEPTS, value="الكل"),
        gr.update(choices=[], value=[]),
        gr.update(choices=[], value=None),
        gr.update(choices=[], value=None),
        gr.update(value=empty_balance_df),
        gr.update(value=empty_absence_df),
        gr.update(value=empty_shortcomings_html),
        gr.update(value=empty_day_df),
        gr.update(value=empty_day_html, visible=True),
        gr.update(visible=False),
        gr.update(interactive=False),
        gr.update(interactive=False),
        gr.update(value="", visible=False),
        0,
        "<div style='color:orange; font-weight:bold;'>⚠️ تم تصفير المنظومة بالكامل ومسح بقايا الواجهة السابقة.</div>",
        gr.update(choices=[], value=None),
        gr.update(value=""),
        gr.update(value="", visible=False),
        gr.update(value=None),
        pd.DataFrame(),
        empty_generation_state,
        gr.update(value=""),
        gr.update(value="<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>"),
        get_initial_header(),
        "",
        gr.update(choices=[], value=None),
        gr.update(choices=[], value=None),
        gr.update(interactive=False),
        gr.update(visible=False, interactive=False),
        gr.update(interactive=False),
        gr.update(interactive=False)
    )  

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


def resolve_effective_dept(dept_value):
    return "الكل" if str(dept_value or "").strip() == "المعلمون" else dept_value


def attempt_login(pin, day_val):
    load_db()
    load_daily_db()

    pin = str(pin or "").strip()
    login_account_id, user_info, auth_error = authenticate_login_pin(pin)

    if user_info:
        role = user_info.get("role", "")
        dept = user_info.get("dept", "الكل")
        if role == "مستخدم عام":
            dept = "المعلمون"

        name = user_info.get("name", "")
        session_display_name = get_account_session_display_name(user_info)
        is_owner = bool(
            user_info.get("is_owner", False)
            or role == "صاحب النظام"
        )

        ui_vis = get_ui_visibility_updates(pin, role, is_owner)
        is_shared_teacher = ui_vis["is_shared_teacher"]

        effective_dept = resolve_effective_dept(dept)
        dept_for_ui = effective_dept
        is_admin = bool(ui_vis["is_admin"])

        temporary_note = ""
        if bool(user_info.get("must_change_pin", False)):
            temporary_note = (
                "<div style='margin-top:8px;color:#fff;background:#b45309;"
                "padding:7px;border-radius:7px;font-size:14px;'>"
                "تنبيه: رمز الدخول مؤقت. غيّره من قسم «تغيير رمز دخولي»."
                "</div>"
            )

        welcome_msg = render_account_welcome_html(user_info, temporary_note)

        if is_admin:
            up_dept_update = gr.update(interactive=True)
            manual_entry_visibility = gr.update(visible=is_owner)
        else:
            up_dept_update = gr.update(value=None, interactive=False)
            manual_entry_visibility = gr.update(visible=False)

        updates = refresh_ui_on_change(
            dept_for_ui,
            day_val,
            is_admin,
        )

        return [
            gr.update(visible=False),
            gr.update(visible=True),
            welcome_msg,
            gr.update(
                choices=["الكل"] + OFFICIAL_DEPTS,
                value=dept_for_ui,
                interactive=is_admin,
            ),
            gr.update(value=""),
            up_dept_update,
            manual_entry_visibility,
            is_admin,
            is_owner,
            session_display_name,
            role,
            login_account_id,
        ] + list(updates) + [
            gr.update(
                visible=(
                    dept_for_ui in ["العلوم", "المهارات الفردية"]
                    and not is_shared_teacher
                )
            ),
            gr.update(visible=ui_vis["can_clear_system"]),
            gr.update(visible=ui_vis["school_data_tab"]),
            gr.update(visible=ui_vis["controls_row"]),
            gr.update(visible=ui_vis["exemptions_tab"]),
            gr.update(visible=ui_vis["distribution_tab"]),
            gr.update(visible=ui_vis["balances_tab"]),
            gr.update(visible=ui_vis["swap_tab"]),
            gr.update(visible=ui_vis["day_tab"]),
            gr.update(visible=ui_vis["teacher_tab"]),
            gr.update(visible=ui_vis["swap_excel_btn"]),
        ]

    if auth_error == "disabled":
        error_text = "هذا الحساب معطل. راجع مالك النظام."
    else:
        error_text = "رمز الدخول غير صحيح! حاول مرة أخرى."

    gr.Warning(f"❌ {error_text}")
    error_updates = [gr.update()] * 27

    return [
        gr.update(),
        gr.update(),
        (
            "<div style='color:red;text-align:center;font-weight:bold;"
            "margin-top:10px;'>"
            f"❌ {html_lib.escape(error_text)}</div>"
        ),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        False,
        False,
        "",
        "",
        "",
    ] + error_updates + [
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
    ]


def do_logout(): 
    return (
        gr.update(visible=True),
        gr.update(visible=False),
        "",
        gr.update(choices=["الكل"] + OFFICIAL_DEPTS, value="الكل"),
        False,
        False,
        "",
        "",
        "",
        None,
        None,
        gr.update(visible=False, value=False),
        gr.update(visible=False),
        gr.update(visible=True),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        get_empty_generation_state(),
        {}
    )
    
css = """
/* فرض وضع النهار بالقوة على مستوى المتصفح */
:root, body, .dark, * { color-scheme: light !important; }
:root, body, .dark { --background-fill-primary: #ffffff !important; --background-fill-secondary: #ffffff !important; --block-background-fill: #ffffff !important; --body-background-fill: #ffffff !important; --color-text-primary: #000000 !important; --body-text-color: #000000 !important; --table-even-background-fill: #ffffff !important; --table-odd-background-fill: #ffffff !important; --table-row-focus: #f1f8e9 !important; --border-color-primary: #e5e7eb !important; --checkbox-background-color: #ffffff !important; --checkbox-background-color-selected: #004d40 !important; --checkbox-border-color: #e5e7eb !important; --input-background-fill: #ffffff !important; --input-background-fill-focus: #ffffff !important; --neutral-100: #ffffff !important; --neutral-200: #f4f6f8 !important; --neutral-800: #000000 !important; --neutral-900: #000000 !important; }
body, .gradio-container, .dark .gradio-container { background-color: #ffffff !important; color: #000000 !important; }
.gradio-container label span, .gradio-container fieldset legend, .gradio-container .gr-form-label span, .dark label span, .dark fieldset legend, .dark .gr-form-label span, .dark .wrap span, .dark .block span, span.svelte-1b6s6s { color: #004d40 !important; -webkit-text-fill-color: #004d40 !important; font-weight: 900 !important; opacity: 1 !important; font-size: 15px !important; text-shadow: none !important; }
.gr-form label, .dark .gr-form label, fieldset label, .dark fieldset label, .gr-checkbox-group label, .dark .gr-checkbox-group label, .gradio-container label.cursor-pointer, .dark .gradio-container label.cursor-pointer { background-color: #f1f8e9 !important; background: #f1f8e9 !important; background-image: none !important; color: #004d40 !important; -webkit-text-fill-color: #004d40 !important; border: 1px solid #c8e6c9 !important; border-radius: 8px !important; box-shadow: none !important; }
.gr-form label.selected, .dark .gr-form label.selected, fieldset label.selected, .dark fieldset label.selected, .gr-form label:has(input:checked), .dark .gr-form label:has(input:checked), .gradio-container label.cursor-pointer.selected, .dark .gradio-container label.cursor-pointer.selected { background-color: #ffca28 !important; background: #ffca28 !important; background-image: none !important; color: #004d40 !important; -webkit-text-fill-color: #004d40 !important; border-color: #004d40 !important; }
input[type="checkbox"], input[type="radio"], .dark input[type="checkbox"], .dark input[type="radio"], .gradio-container input[type="checkbox"], .dark .gradio-container input[type="checkbox"] { -webkit-appearance: none !important; appearance: none !important; background-color: #ffffff !important; border: 2px solid #004d40 !important; width: 18px !important; height: 18px !important; border-radius: 4px !important; display: inline-block !important; position: relative !important; outline: none !important; }
input[type="checkbox"]:checked::after, .dark input[type="checkbox"]:checked::after, .gradio-container input[type="checkbox"]:checked::after { content: '✔' !important; position: absolute !important; top: 50% !important; left: 50% !important; transform: translate(-50%, -50%) !important; color: #004d40 !important; font-size: 14px !important; font-weight: bold !important; }
.absent-box .token, .dark .absent-box .token { background: linear-gradient(135deg, #e53935, #c62828) !important; color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; border: 2px solid #b71c1c !important; font-weight: 900 !important; font-size: 15px !important; padding: 6px 12px !important; border-radius: 10px !important; box-shadow: 0 4px 8px rgba(198, 40, 40, 0.3) !important; transition: transform 0.2s ease !important; animation: pulse-red 2s infinite !important; }
.absent-box .token span { color: #ffffff !important; -webkit-text-fill-color: #ffffff !important; }
.absent-box .token::before { content: '🚨 ' !important; margin-left: 5px !important; }
.absent-box .token:hover { transform: scale(1.05) !important; }
@keyframes pulse-red { 0% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.5); } 70% { box-shadow: 0 0 0 10px rgba(198, 40, 40, 0); } 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0); } }
.gr-input, .gr-dropdown-item, input, select, option, textarea, .dark .gr-input, .dark .gr-dropdown-item, .dark input, .dark select, .dark option, .dark textarea { color: #000000 !important; -webkit-text-fill-color: #000000 !important; font-weight: bold !important; background-color: #ffffff !important; }
h1, h2, p { color: inherit; }
.main-header { background: #004d40 !important; padding: 20px 10px !important; border-radius: 0 0 20px 20px; border-bottom: 5px solid #ffca28; box-shadow: 0 4px 8px rgba(0,0,0,0.1); margin-bottom: 15px;}
.header-grid { display: grid; grid-template-columns: auto 1fr auto; grid-template-areas: "logo title ministry" "logo school ministry" "logo credits ministry"; align-items: center; gap: 5px 20px; max-width: 1200px; margin: 0 auto;}
.h-logo { grid-area: logo; text-align: left; }
.h-logo img { width: 85px; height: 85px; object-fit: contain; background: #ffffff; border-radius: 50%; border: 3px solid #ffca28; box-shadow: 0 4px 10px rgba(0,0,0,0.3); padding: 3px; }
.h-ministry { grid-area: ministry; text-align: right; color: white !important; font-weight: bold; font-size: 14px; line-height: 1.6; }
.h-title { grid-area: title; text-align: center; color: #ffffff !important; font-weight: 900; font-size: 24px; margin: 0;}
.h-school { grid-area: school; text-align: center; font-size: 18px !important; margin: 0;}
.h-credits { grid-area: credits; text-align: center; }
.credits-box { background: linear-gradient(135deg, #004d40, #00332a) !important; color: #ffca28 !important; padding: 8px 15px !important; border-radius: 8px !important; border: 1px dashed #ffca28 !important; font-weight: bold !important; font-size: 14px !important; display: inline-block !important; box-shadow: inset 0 0 10px rgba(0,0,0,0.2) !important;}
@media (max-width: 768px) { .header-grid { display: flex; flex-direction: column; align-items: center; text-align: center; gap: 10px; padding: 5px 0; } .h-logo { order: 1; margin-bottom: 0; } .h-logo img { width: 75px; height: 75px; margin: 0 auto; } .h-ministry { order: 2; text-align: center; font-size: 13px; border-bottom: 1px dashed rgba(255,255,255,0.3); padding-bottom: 8px; margin-bottom: 0; width: 95%; line-height: 1.5; } .h-school { order: 3; font-weight: bold; font-size: 16.5px !important; margin-bottom: 0;} .h-title { order: 4; font-size: 18px; line-height: 1.4; margin-bottom: 0; } .h-credits { order: 5; margin-top: 5px; } }
.tab-nav button, .dark .tab-nav button { color: #333333 !important; font-weight: bold !important; font-size: 15px !important; }
.tab-nav button.selected, .dark .tab-nav button.selected { background-color: #ffca28 !important; color: #004d40 !important; border-color: #ffca28 !important;}
.table-wrap { overflow-x: auto !important; }
table, .gr-table, .dark table, .dark .gr-table { background-color: #ffffff !important; color: #000000 !important; table-layout: auto !important; width: 100% !important; border-collapse: collapse !important;}
tbody, tr, td, .dark tbody, .dark tr, .dark td { background-color: #ffffff !important; color: #000000 !important; }
thead, thead tr, thead th, th, .dark thead, .dark thead tr, .dark thead th, .dark th { background-color: #f1f8e9 !important; color: #000000 !important; border-bottom: 2px solid #004d40 !important;}
td *, th *, .dark td *, .dark th *, .cell-wrap, .dark .cell-wrap { background-color: transparent !important; color: #000000 !important; white-space: nowrap !important; overflow: visible !important; text-overflow: clip !important;}
th, .dark th { font-weight: 900 !important; text-align: center !important; white-space: nowrap !important; min-width: 65px !important; font-size: 14px !important; padding: 10px 5px !important; border: 1px solid #e5e7eb !important;}
td, .dark td { font-weight: bold !important; text-align: center !important; white-space: nowrap !important; min-width: 65px !important; font-size: 13px !important; padding: 8px 5px !important; border: 1px solid #e5e7eb !important;}
.yellow-box { background-color: #fff9c4 !important; border-radius: 15px !important; padding: 15px !important; margin: 10px 0 !important; border: 2px solid #ffca28 !important;}
.whatsapp-box { background-color: #e8f5e9 !important; border-radius: 15px !important; padding: 20px !important; margin: 10px 0 !important; border: 2px solid #4caf50 !important;}
.shield-box { background-color: #ffebee !important; border-radius: 15px !important; padding: 20px !important; margin: 10px 0 !important; border: 2px solid #f44336 !important;}
.action-btn { background: #ffca28 !important; color: #004d40 !important; font-weight: 900 !important; height: 50px !important; border-radius: 10px !important;}
.export-btn { background: #1565c0 !important; color: white !important; font-weight: bold !important; height: 50px !important; border-radius: 10px !important;}
.admin-zone { background-color: #f4f6f8 !important; border: 2px solid #004d40 !important; border-radius: 12px !important; padding: 20px !important; margin-top: 15px !important; box-shadow: inset 0 0 10px rgba(0,0,0,0.03) !important; }
.admin-btn { background: linear-gradient(135deg, #004d40, #00695c) !important; color: #ffca28 !important; font-weight: bold !important; border-radius: 8px !important; height: 50px !important; border: none !important; box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important; transition: all 0.3s ease !important; }
.admin-btn:hover { transform: translateY(-2px) !important; box-shadow: 0 6px 12px rgba(0,0,0,0.15) !important; }
.refresh-btn { background: linear-gradient(135deg, #004d40, #00695c) !important; color: #ffca28 !important; font-weight: bold !important; border-radius: 8px !important; min-height: 50px !important; height: auto !important; font-size: 13.5px !important; white-space: normal !important; padding: 8px 5px !important; line-height: 1.4 !important; border: none !important; box-shadow: 0 4px 6px rgba(0,0,0,0.1) !important; transition: all 0.3s ease !important; }
.refresh-btn:hover { transform: translateY(-2px) !important; box-shadow: 0 6px 12px rgba(0,0,0,0.15) !important; }
@media (min-width: 768px) { .refresh-btn { margin-top: 24px !important; } }
.reset-btn { background: #e53935 !important; color: white !important; font-weight: bold !important; border-radius: 8px !important; height: 50px !important; }
.tabadul-btn { background: #00897b !important; color: white !important; font-weight: bold !important; border-radius: 8px !important; height: 50px !important; border: 2px solid #00695c !important; }
.login-box { max-width: 450px !important; margin: 65px auto 20px auto !important; padding: 25px 20px !important; background: #ffffff !important; border-radius: 20px !important; box-shadow: 0 10px 30px rgba(0,0,0,0.15) !important; border-top: 8px solid #004d40 !important; border-bottom: 8px solid #ffca28 !important;}
.login-box input::placeholder { font-size: 13.5px !important; }
@keyframes pulse {
  0% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.4); }
  70% { box-shadow: 0 0 0 10px rgba(198, 40, 40, 0); }
  100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0); }
}

/* راديو التبادل الودي فقط */
.swap-radio-square input[type="radio"],
.dark .swap-radio-square input[type="radio"] {
  -webkit-appearance: none !important;
  appearance: none !important;
  width: 18px !important;
  height: 18px !important;
  border: 2px solid #004d40 !important;
  border-radius: 4px !important;
  background-color: #ffffff !important;
  display: inline-block !important;
  position: relative !important;
  outline: none !important;
  cursor: pointer !important;
  box-shadow: none !important;
  background-image: none !important;
}

.swap-radio-square input[type="radio"]:checked,
.dark .swap-radio-square input[type="radio"]:checked {
  background-color: #ffffff !important;
  border-color: #004d40 !important;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><circle cx='8' cy='8' r='4.6' fill='%23004d40'/></svg>") !important;
  background-repeat: no-repeat !important;
  background-position: center center !important;
  background-size: 12px 12px !important;
  box-shadow: none !important;
}

.swap-radio-square label.selected,
.dark .swap-radio-square label.selected {
  background-image: none !important;
}
.regen-btn {
  background: #ef6c00 !important;
  color: white !important;
  font-weight: bold !important;
  border-radius: 8px !important;
  height: 50px !important;
  border: none !important;
}

/* v43 fix5: تحسينات تبويب الإعفاء والخزنة والتبويبات */
.exemption-rtl-group,
.exemption-rtl-group fieldset,
.exemption-rtl-group .wrap {
  direction: rtl !important;
}

.exemption-rtl-group fieldset > div,
.exemption-rtl-group .wrap,
.exemption-rtl-group .form {
  display: flex !important;
  flex-direction: row-reverse !important;
  flex-wrap: wrap !important;
  justify-content: flex-end !important;
  gap: 8px !important;
  direction: ltr !important;
}

.exemption-rtl-group label {
  direction: rtl !important;
  min-width: 64px !important;
  justify-content: center !important;
  text-align: center !important;
  font-size: 15px !important;
  font-weight: 900 !important;
}

.exemption-periods-order label {
  min-width: 48px !important;
}

.vault-accordion summary,
.leader-accordion summary,
.vault-accordion .label-wrap,
.leader-accordion .label-wrap {
  font-size: 20px !important;
  font-weight: 900 !important;
  color: #004d40 !important;
}

button[role="tab"],
.tab-nav button,
.tabs button,
.gradio-container button[role="tab"] {
  font-size: 17px !important;
  font-weight: 900 !important;
  padding: 10px 14px !important;
}

@media (max-width: 640px) {
  button[role="tab"],
  .tab-nav button,
  .tabs button,
  .gradio-container button[role="tab"] {
    font-size: 15px !important;
    padding: 8px 10px !important;
  }
}


/* v43 fix6: تحسينات تجربة الاستخدام ولوحة القيادة */
.reserve-guide-box {
  background: #f0fdf4 !important;
  border: 1px solid #bbf7d0 !important;
  border-right: 5px solid #0f766e !important;
  border-radius: 12px !important;
  padding: 14px !important;
  margin-bottom: 12px !important;
  color: #064e3b !important;
  font-weight: 800 !important;
  line-height: 1.9 !important;
  direction: rtl !important;
  text-align: right !important;
  font-size: 15px !important;
}

.big-section-title {
  text-align: center !important;
  color: #004d40 !important;
  background: #f0fdf4 !important;
  border: 1px solid #bbf7d0 !important;
  border-radius: 12px !important;
  padding: 12px 10px !important;
  margin: 4px 0 14px 0 !important;
  font-size: 24px !important;
  font-weight: 900 !important;
  direction: rtl !important;
}

.exemption-rtl-group label {
  gap: 9px !important;
  padding-inline: 10px !important;
}

.exemption-rtl-group input[type="checkbox"] {
  margin-inline-end: 7px !important;
  margin-inline-start: 3px !important;
  flex-shrink: 0 !important;
}

.exemption-periods-order label {
  gap: 10px !important;
}

button:disabled,
button[disabled],
.gradio-container button:disabled {
  opacity: 0.48 !important;
  filter: grayscale(0.25) !important;
  cursor: not-allowed !important;
}

@media (max-width: 640px) {
  .big-section-title {
    font-size: 21px !important;
  }
  .reserve-guide-box {
    font-size: 14px !important;
  }
}


/* v43 fix7: عناوين خارجية كبيرة للأكورديون */
.external-section-title {
  text-align: center !important;
  direction: rtl !important;
  color: #004d40 !important;
  background: linear-gradient(135deg, #f0fdf4, #ffffff) !important;
  border: 2px solid #0f766e !important;
  border-right: 7px solid #0f766e !important;
  border-radius: 14px !important;
  padding: 16px 12px !important;
  margin: 18px 0 8px 0 !important;
  font-size: 28px !important;
  font-weight: 1000 !important;
  line-height: 1.5 !important;
  box-shadow: 0 2px 8px rgba(15, 118, 110, 0.14) !important;
}

.vault-title {
  background: linear-gradient(135deg, #fffde7, #ffffff) !important;
  border-color: #facc15 !important;
  border-right-color: #ca8a04 !important;
  color: #4d3b00 !important;
}

.leader-accordion summary,
.vault-accordion summary,
.leader-accordion button,
.vault-accordion button,
.leader-accordion [role="button"],
.vault-accordion [role="button"] {
  font-size: 18px !important;
  font-weight: 900 !important;
  color: #004d40 !important;
}

@media (max-width: 640px) {
  .external-section-title {
    font-size: 23px !important;
    padding: 14px 10px !important;
  }
}


/* v43 fix8: استرجاع ألوان أزرار الخزنة كما كانت واضحة */
.vault-save-btn,
.vault-save-btn button,
button.vault-save-btn,
.vault-save-btn * {
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  font-weight: 900 !important;
  font-size: 20px !important;
}

.vault-save-btn,
.vault-save-btn button,
button.vault-save-btn {
  background: linear-gradient(135deg, #004d40, #00695c) !important;
  border: none !important;
  opacity: 1 !important;
  filter: none !important;
}

.vault-delete-btn,
.vault-delete-btn button,
button.vault-delete-btn,
.vault-delete-btn * {
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
  font-weight: 900 !important;
  font-size: 20px !important;
}

.vault-delete-btn,
.vault-delete-btn button,
button.vault-delete-btn {
  background: #d8433e !important;
  border: none !important;
  opacity: 1 !important;
  filter: none !important;
}

.vault-save-btn:disabled,
.vault-save-btn button:disabled,
.vault-delete-btn:disabled,
.vault-delete-btn button:disabled {
  opacity: 1 !important;
  filter: none !important;
}


/* v43 fix9: ألوان أزرار لوحة القائد في التفعيل والإطفاء */
.leader-official-btn,
.leader-official-btn button,
button.leader-official-btn {
  background: linear-gradient(135deg, #004d40, #00695c) !important;
  border: none !important;
}

.leader-official-btn *,
.leader-official-btn button,
button.leader-official-btn {
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  font-weight: 900 !important;
}

.leader-swap-btn,
.leader-swap-btn button,
button.leader-swap-btn {
  background: linear-gradient(135deg, #00897b, #009688) !important;
  border: 2px solid #00695c !important;
}

.leader-swap-btn *,
.leader-swap-btn button,
button.leader-swap-btn {
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
  font-weight: 900 !important;
}

.leader-penalty-btn,
.leader-penalty-btn button,
button.leader-penalty-btn,
.leader-cancel-btn,
.leader-cancel-btn button,
button.leader-cancel-btn {
  background: #ef3737 !important;
  border: none !important;
}

.leader-penalty-btn *,
.leader-penalty-btn button,
button.leader-penalty-btn,
.leader-cancel-btn *,
.leader-cancel-btn button,
button.leader-cancel-btn {
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
  font-weight: 900 !important;
}

.leader-official-btn:disabled,
.leader-official-btn button:disabled,
.leader-swap-btn:disabled,
.leader-swap-btn button:disabled,
.leader-penalty-btn:disabled,
.leader-penalty-btn button:disabled,
.leader-cancel-btn:disabled,
.leader-cancel-btn button:disabled {
  opacity: 0.52 !important;
  filter: grayscale(0.2) !important;
}

.leader-official-btn:disabled *,
.leader-official-btn button:disabled,
.leader-swap-btn:disabled *,
.leader-swap-btn button:disabled,
.leader-penalty-btn:disabled *,
.leader-penalty-btn button:disabled,
.leader-cancel-btn:disabled *,
.leader-cancel-btn button:disabled {
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
}


/* v43 fix10: ضبط زر إلغاء غياب اليوم بالكامل للجوال */
.leader-cancel-btn,
.leader-cancel-btn button,
button.leader-cancel-btn {
  min-height: 58px !important;
  white-space: normal !important;
  line-height: 1.35 !important;
  padding: 10px 14px !important;
  text-align: center !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
}

.leader-cancel-btn *,
.leader-cancel-btn button,
button.leader-cancel-btn {
  font-size: 20px !important;
  line-height: 1.35 !important;
}

@media (max-width: 640px) {
  .leader-cancel-btn,
  .leader-cancel-btn button,
  button.leader-cancel-btn {
    min-height: 64px !important;
    padding: 10px 12px !important;
  }

  .leader-cancel-btn *,
  .leader-cancel-btn button,
  button.leader-cancel-btn {
    font-size: 18px !important;
    line-height: 1.25 !important;
  }
}


/* v43 fix13: إرشادات الخزنة والتبادل الودي */
.vault-guide-box,
.swap-guide-box {
  background: #f0fdf4 !important;
  border: 1px solid #bbf7d0 !important;
  border-right: 5px solid #0f766e !important;
  border-radius: 12px !important;
  padding: 14px !important;
  margin: 10px 0 14px 0 !important;
  color: #064e3b !important;
  font-weight: 800 !important;
  line-height: 1.9 !important;
  direction: rtl !important;
  text-align: right !important;
  font-size: 15px !important;
}

.vault-guide-box {
  background: #fffde7 !important;
  border-color: #fde68a !important;
  border-right-color: #ca8a04 !important;
  color: #4d3b00 !important;
}

@media (max-width: 640px) {
  .vault-guide-box,
  .swap-guide-box {
    font-size: 14px !important;
    padding: 13px !important;
  }
}


/* v43 fix18: اسم منظومة مسار مع الحفاظ على هوية الهيدر */
.h-title {
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 4px !important;
  line-height: 1.25 !important;
}

.h-title-main {
  font-size: inherit !important;
  font-weight: inherit !important;
  color: inherit !important;
  -webkit-text-fill-color: inherit !important;
  white-space: nowrap !important;
}

.h-title-sub {
  font-size: 0.52em !important;
  font-weight: 900 !important;
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  white-space: nowrap !important;
  letter-spacing: 0 !important;
}

@media (max-width: 640px) {
  .h-title-sub {
    font-size: 0.48em !important;
  }
}


/* v43 fix19: إبراز اسم منظومة مسار في الهيدر */
.main-header {
  padding-top: 18px !important;
  padding-bottom: 18px !important;
}

.h-title {
  position: relative !important;
  display: flex !important;
  flex-direction: column !important;
  align-items: center !important;
  justify-content: center !important;
  gap: 7px !important;
  line-height: 1.12 !important;
  color: #ffffff !important;
  text-align: center !important;
  margin: 2px 0 4px 0 !important;
}

.h-title-main {
  font-size: 42px !important;
  font-weight: 1000 !important;
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
  text-shadow: 0 3px 8px rgba(0, 0, 0, 0.35), 0 0 14px rgba(255, 202, 40, 0.18) !important;
  letter-spacing: 0 !important;
  white-space: nowrap !important;
  padding: 2px 18px 4px 18px !important;
  border-bottom: 3px solid rgba(255, 202, 40, 0.95) !important;
  display: inline-block !important;
}

.h-title-sub {
  font-size: 18px !important;
  font-weight: 900 !important;
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  text-shadow: 0 2px 5px rgba(0,0,0,0.22) !important;
  white-space: nowrap !important;
  padding: 0 10px !important;
}

.h-school {
  margin-top: 3px !important;
  color: #ffdf6d !important;
  -webkit-text-fill-color: #ffdf6d !important;
  font-weight: 900 !important;
}

.credits-box {
  margin-top: 5px !important;
}

@media (max-width: 768px) {
  .main-header {
    padding-top: 15px !important;
    padding-bottom: 15px !important;
  }

  .h-title {
    order: 3 !important;
    gap: 6px !important;
    margin-top: 2px !important;
    margin-bottom: 2px !important;
  }

  .h-title-main {
    font-size: clamp(30px, 8.5vw, 40px) !important;
    padding: 2px 12px 4px 12px !important;
    border-bottom-width: 2px !important;
  }

  .h-title-sub {
    font-size: clamp(14px, 4.2vw, 18px) !important;
  }

  .h-school {
    order: 4 !important;
    font-size: 15.5px !important;
  }

  .h-ministry {
    order: 2 !important;
  }

  .h-credits {
    order: 5 !important;
  }
}


/* v43 fix20: بطاقة داخلية أنيقة لعنوان منظومة مسار داخل الهيدر */
.h-title {
  background: linear-gradient(135deg, rgba(0, 77, 64, 0.72), rgba(0, 55, 46, 0.86)) !important;
  border: 1.8px solid rgba(255, 202, 40, 0.82) !important;
  border-radius: 18px !important;
  padding: 14px 30px 13px 30px !important;
  min-width: 420px !important;
  max-width: 720px !important;
  box-shadow:
    0 8px 20px rgba(0, 0, 0, 0.18),
    inset 0 0 18px rgba(255, 255, 255, 0.045) !important;
  backdrop-filter: blur(2px) !important;
  -webkit-backdrop-filter: blur(2px) !important;
}

.h-title-main {
  border-bottom: none !important;
  padding: 0 !important;
  font-size: 44px !important;
  line-height: 1.08 !important;
}

.h-title-main::after {
  content: "" !important;
  display: block !important;
  width: 58% !important;
  height: 3px !important;
  margin: 8px auto 0 auto !important;
  border-radius: 999px !important;
  background: linear-gradient(90deg, transparent, #ffca28, transparent) !important;
  opacity: 0.9 !important;
}

.h-title-sub {
  margin-top: 2px !important;
  font-size: 18px !important;
  color: #ffdc64 !important;
  -webkit-text-fill-color: #ffdc64 !important;
}

@media (max-width: 768px) {
  .h-title {
    min-width: unset !important;
    width: min(92vw, 560px) !important;
    padding: 13px 16px 12px 16px !important;
    border-radius: 16px !important;
  }

  .h-title-main {
    font-size: clamp(31px, 9vw, 42px) !important;
  }

  .h-title-main::after {
    width: 66% !important;
    height: 2px !important;
    margin-top: 7px !important;
  }

  .h-title-sub {
    font-size: clamp(14px, 4.4vw, 18px) !important;
  }
}


/* v43 fix21: ضبط محاذاة الهيدر على الحاسوب فقط */
@media (min-width: 769px) {
  .header-grid {
    grid-template-columns: 120px minmax(580px, 720px) 230px !important;
    grid-template-areas:
      "logo title ministry"
      "logo school ministry"
      "logo credits ministry" !important;
    justify-content: center !important;
    align-items: center !important;
    column-gap: 28px !important;
  }

  .h-title,
  .h-school,
  .h-credits {
    width: 100% !important;
    max-width: 720px !important;
    justify-self: center !important;
    margin-left: auto !important;
    margin-right: auto !important;
    box-sizing: border-box !important;
    text-align: center !important;
  }

  .h-title {
    min-width: 0 !important;
    margin-top: 0 !important;
    margin-bottom: 8px !important;
  }

  .h-school {
    margin-top: 2px !important;
    margin-bottom: 6px !important;
    line-height: 1.35 !important;
  }

  .h-credits {
    display: flex !important;
    justify-content: center !important;
  }

  .credits-box {
    width: 100% !important;
    max-width: 620px !important;
    box-sizing: border-box !important;
    text-align: center !important;
    margin-left: auto !important;
    margin-right: auto !important;
  }

  .h-title-main,
  .h-title-sub {
    text-align: center !important;
    margin-left: auto !important;
    margin-right: auto !important;
  }
}


/* v44-A: لوحة الدخول الرئيسية لمنظومة مسار */
.masar-home-dashboard {
  direction: rtl !important;
  width: 100% !important;
  box-sizing: border-box !important;
}

.masar-home-hero {
  background: linear-gradient(135deg, #004d40 0%, #00695c 58%, #003c32 100%) !important;
  border: 2px solid rgba(255, 202, 40, 0.82) !important;
  border-radius: 20px !important;
  padding: 22px 18px !important;
  margin: 16px 0 18px 0 !important;
  text-align: center !important;
  box-shadow: 0 10px 24px rgba(0, 77, 64, 0.18) !important;
}

.masar-home-title {
  color: #ffffff !important;
  -webkit-text-fill-color: #ffffff !important;
  font-size: 28px !important;
  font-weight: 1000 !important;
  line-height: 1.4 !important;
  text-shadow: 0 3px 8px rgba(0,0,0,0.25) !important;
}

.masar-home-subtitle {
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  font-size: 18px !important;
  font-weight: 900 !important;
  margin-top: 8px !important;
}

.masar-home-note {
  color: #e8f5e9 !important;
  -webkit-text-fill-color: #e8f5e9 !important;
  font-size: 15px !important;
  font-weight: 800 !important;
  margin-top: 5px !important;
}

.masar-card-grid {
  display: grid !important;
  grid-template-columns: repeat(3, minmax(0, 1fr)) !important;
  gap: 14px !important;
  align-items: stretch !important;
}

.masar-card {
  background: linear-gradient(180deg, #ffffff 0%, #f8fffb 100%) !important;
  border: 1.5px solid #b7dfd5 !important;
  border-right: 7px solid #0f766e !important;
  border-radius: 18px !important;
  padding: 16px 14px !important;
  box-shadow: 0 6px 16px rgba(15, 118, 110, 0.10) !important;
  min-height: 180px !important;
  box-sizing: border-box !important;
  transition: transform 0.18s ease, box-shadow 0.18s ease !important;
}

.masar-card:hover {
  transform: translateY(-2px) !important;
  box-shadow: 0 10px 24px rgba(15, 118, 110, 0.16) !important;
}

.masar-card-icon {
  font-size: 34px !important;
  line-height: 1.2 !important;
  text-align: center !important;
  margin-bottom: 6px !important;
}

.masar-card-title {
  color: #004d40 !important;
  -webkit-text-fill-color: #004d40 !important;
  font-size: 19px !important;
  font-weight: 1000 !important;
  text-align: center !important;
  line-height: 1.4 !important;
}

.masar-card-desc {
  color: #475569 !important;
  -webkit-text-fill-color: #475569 !important;
  font-size: 14px !important;
  font-weight: 800 !important;
  text-align: center !important;
  line-height: 1.65 !important;
  margin: 6px 0 10px 0 !important;
}

.masar-card-btn,
.masar-card-btn button,
button.masar-card-btn {
  background: linear-gradient(135deg, #004d40, #00695c) !important;
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  border: none !important;
  border-radius: 12px !important;
  font-weight: 1000 !important;
  min-height: 42px !important;
}

.home-back-btn,
.home-back-btn button,
button.home-back-btn {
  background: linear-gradient(135deg, #004d40, #00695c) !important;
  color: #ffca28 !important;
  -webkit-text-fill-color: #ffca28 !important;
  border: 1.5px solid #ffca28 !important;
  border-radius: 12px !important;
  font-weight: 1000 !important;
  font-size: 17px !important;
  margin: 10px 0 14px 0 !important;
  min-height: 46px !important;
}

.masar-tabs-container {
  direction: rtl !important;
}

@media (max-width: 900px) {
  .masar-card-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}

@media (max-width: 640px) {
  .masar-home-hero {
    padding: 18px 12px !important;
    margin-top: 12px !important;
  }

  .masar-home-title {
    font-size: 23px !important;
  }

  .masar-home-subtitle {
    font-size: 16px !important;
  }

  .masar-card-grid {
    display: flex !important;
    flex-direction: column !important;
    gap: 12px !important;
  }

  .masar-card {
    width: 100% !important;
    min-height: 154px !important;
    padding: 15px 12px !important;
  }

  .masar-card-title {
    font-size: 18px !important;
  }

  .masar-card-desc {
    font-size: 14px !important;
  }
}


/* v44-E: ملاحظة فتح الأقسام */
.section-open-note {
  background: #f0fdf4 !important;
  color: #064e3b !important;
  -webkit-text-fill-color: #064e3b !important;
  border: 1px solid #bbf7d0 !important;
  border-right: 5px solid #0f766e !important;
  border-radius: 10px !important;
  padding: 10px 12px !important;
  margin: 0 0 12px 0 !important;
  font-weight: 900 !important;
  text-align: center !important;
  direction: rtl !important;
}




/* v44-O: إخفاء شريط التبويبات القديم بصريًا لأن التنقل صار من البطاقات */
.masar-tabs-container .tab-nav,
.masar-tabs-container [role="tablist"] {
  display: none !important;
}

.masar-tabs-container .tabs,
.masar-tabs-container .tabitem {
  direction: rtl !important;
}






/* v44-Q: تحميل التبويبات دائمًا وإخفاء الحاوية بـ CSS فقط */
#masar_tabs_container {
  display: none !important;
}

#masar_home_dashboard {
  display: block;
}

/* إخفاء شريط التبويبات القديم بصريًا لأن التنقل من البطاقات */
.masar-tabs-container .tab-nav,
.masar-tabs-container [role="tablist"] {
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}


/* v44-S: تهذيب زر العودة وزر الخروج على شاشات اللابتوب فقط */
@media (min-width: 769px) {
  /* زر العودة للوحة الرئيسية */
  .home-back-btn,
  .home-back-btn button,
  button.home-back-btn {
    width: 420px !important;
    max-width: 42vw !important;
    min-height: 46px !important;
    margin: 14px auto 18px auto !important;
    display: block !important;
    border-radius: 14px !important;
    font-size: 18px !important;
    font-weight: 1000 !important;
    padding: 8px 22px !important;
    box-shadow: 0 6px 16px rgba(0, 77, 64, 0.16) !important;
  }

  /* زر الخروج والإقفال */
  .logout-btn,
  .logout-btn button,
  button.logout-btn,
  .reset-btn.logout-btn,
  button.reset-btn.logout-btn {
    width: 230px !important;
    max-width: 24vw !important;
    min-height: 44px !important;
    margin: 0 0 0 auto !important;
    border-radius: 12px !important;
    font-size: 16px !important;
    font-weight: 1000 !important;
    padding: 8px 18px !important;
    box-shadow: 0 5px 14px rgba(185, 28, 28, 0.16) !important;
  }

  /* لا يغيّر الهاتف: فقط تحسين تموضع الصف العلوي إن كانت الكلاسات موجودة */
  .top-action-row,
  .welcome-action-row {
    align-items: center !important;
    gap: 14px !important;
  }
}


/* v44-T: ضبط محاذاة زر الخروج مع رسالة الترحيب على اللابتوب فقط */
@media (min-width: 769px) {
  /* صف رسالة الترحيب وزر الخروج */
  .main-app-header-row,
  .welcome-row,
  .top-row,
  .top-action-row,
  .welcome-action-row {
    display: flex !important;
    align-items: center !important;
    gap: 14px !important;
  }

  /* رسالة الترحيب */
  .welcome-box,
  .welcome-html,
  .welcome-message {
    display: flex !important;
    align-items: center !important;
    min-height: 46px !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
  }

  /* زر خروج وإقفال: محاذاة رأسية مع رسالة الترحيب */
  .logout-btn,
  .logout-btn button,
  button.logout-btn,
  .reset-btn.logout-btn,
  button.reset-btn.logout-btn {
    align-self: center !important;
    transform: translateY(0) !important;
    margin-top: 0 !important;
    margin-bottom: 0 !important;
    min-height: 46px !important;
    height: 46px !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    line-height: 1.2 !important;
  }
}


/* v44-U: محاذاة دقيقة لزر الخروج مع رسالة الترحيب على اللابتوب فقط */
@media (min-width: 769px) {
  .top-user-row {
    display: flex !important;
    align-items: center !important;
    gap: 16px !important;
    width: 100% !important;
    margin: 12px 0 14px 0 !important;
  }

  .top-user-row .welcome-col,
  .top-user-row .logout-col {
    display: flex !important;
    align-items: center !important;
  }

  .top-user-row .welcome-col {
    flex: 1 1 auto !important;
  }

  .top-user-row .logout-col {
    flex: 0 0 230px !important;
    max-width: 230px !important;
    min-width: 210px !important;
    justify-content: center !important;
  }

  .top-user-row .welcome-html-box,
  .top-user-row .welcome-html-box > div,
  .top-user-row .welcome-html-box .prose,
  .top-user-row .welcome-html-box .markdown {
    width: 100% !important;
    min-height: 46px !important;
    margin: 0 !important;
    display: flex !important;
    align-items: center !important;
  }

  .top-user-row .logout-col button,
  .top-user-row .logout-btn button,
  .top-user-row button.logout-btn,
  .top-user-row .reset-btn button {
    width: 230px !important;
    max-width: 230px !important;
    height: 46px !important;
    min-height: 46px !important;
    margin: 0 !important;
    padding: 8px 18px !important;
    border-radius: 12px !important;
    font-size: 16px !important;
    font-weight: 1000 !important;
    line-height: 1.2 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    align-self: center !important;
    transform: none !important;
  }

  .top-user-row .logout-col > div {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    margin: 0 !important;
  }
}


/* v44-V: إعادة توسيط رسالة الترحيب على اللابتوب فقط دون المساس ببقية التنسيقات */
@media (min-width: 769px) {
  .top-user-row .welcome-col {
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
  }

  .top-user-row .welcome-html-box,
  .top-user-row .welcome-html-box > div,
  .top-user-row .welcome-html-box .prose,
  .top-user-row .welcome-html-box .markdown {
    width: 100% !important;
    text-align: center !important;
    justify-content: center !important;
    align-items: center !important;
  }

  .top-user-row .welcome-html-box * {
    text-align: center !important;
  }

  .top-user-row .logout-col {
    align-items: center !important;
    justify-content: center !important;
  }
}


/* v44-W: إصلاح ربط البطاقات بالتبويبات دون إظهار شريط التبويبات للمستخدم */
#masar_tabs_container .tab-nav,
#masar_tabs_container [role="tablist"],
.masar-tabs-container .tab-nav,
.masar-tabs-container [role="tablist"] {
  display: flex !important;
  position: absolute !important;
  width: 1px !important;
  height: 1px !important;
  overflow: hidden !important;
  opacity: 0 !important;
  pointer-events: none !important;
}

#masar_tabs_container {
  display: none !important;
}


/* v44-AV — حركة 4D تفاعلية لشعار بوابة الدخول فقط */
@keyframes logo4d {
    0%   { transform: perspective(400px) rotateY(-12deg) rotateX(5deg) scale(1.08); box-shadow: 0 15px 40px rgba(0,0,0,0.6), 0 0 0 5px rgba(255,202,40,0.3), 4px -4px 15px rgba(255,255,255,0.2); }
    25%  { transform: perspective(400px) rotateY(8deg) rotateX(-3deg) scale(1.10); box-shadow: 0 20px 50px rgba(0,0,0,0.7), 0 0 0 7px rgba(255,202,40,0.5), -4px -4px 20px rgba(255,255,255,0.3); }
    50%  { transform: perspective(400px) rotateY(12deg) rotateX(5deg) scale(1.08); box-shadow: 0 15px 40px rgba(0,0,0,0.6), 0 0 0 5px rgba(255,202,40,0.3), -4px 4px 15px rgba(255,255,255,0.2); }
    75%  { transform: perspective(400px) rotateY(-8deg) rotateX(-3deg) scale(1.10); box-shadow: 0 20px 50px rgba(0,0,0,0.7), 0 0 0 7px rgba(255,202,40,0.5), 4px -4px 20px rgba(255,255,255,0.3); }
    100% { transform: perspective(400px) rotateY(-12deg) rotateX(5deg) scale(1.08); box-shadow: 0 15px 40px rgba(0,0,0,0.6), 0 0 0 5px rgba(255,202,40,0.3), 4px -4px 15px rgba(255,255,255,0.2); }
}


/* waveMove — تحريك الموجة الذهبية في واجهة الدخول فقط */
@keyframes waveMove {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
}


/* v44 final: إزالة الإطار البرتقالي الخارجي الذي يظهر حول محتوى التبويبات فقط */
#masar_tabs_container,
#masar_tabs_container > .block,
#masar_tabs_container .tabs,
#masar_tabs_container .tabitem,
#masar_tabs_container [role="tabpanel"],
#masar_tabs_container [data-testid="tabs"],
#masar_tabs_container [data-testid="tabitem"] {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
}

#masar_tabs_container:focus,
#masar_tabs_container:focus-visible,
#masar_tabs_container .tabitem:focus,
#masar_tabs_container .tabitem:focus-visible,
#masar_tabs_container [role="tabpanel"]:focus,
#masar_tabs_container [role="tabpanel"]:focus-visible {
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
}


/* v44 final v2: إزالة الإطار البرتقالي من طبقات TabPanel الداخلية فقط */
#masar_tabs_container,
#masar_tabs_container > div,
#masar_tabs_container > div > div,
#masar_tabs_container > div > div > div,
#masar_tabs_container .tabs,
#masar_tabs_container [class*="tabs"],
#masar_tabs_container [class*="tabitem"],
#masar_tabs_container [class*="tabpanel"],
#masar_tabs_container [data-testid*="tabs"],
#masar_tabs_container [data-testid*="tabitem"],
#masar_tabs_container [data-testid*="tabpanel"],
#masar_tabs_container [role="tabpanel"] {
    border-color: transparent !important;
    border-left-color: transparent !important;
    border-right-color: transparent !important;
    border-top-color: transparent !important;
    border-bottom-color: transparent !important;
    outline: none !important;
    outline-color: transparent !important;
    box-shadow: none !important;
}

#masar_tabs_container .tabs::before,
#masar_tabs_container .tabs::after,
#masar_tabs_container [class*="tabs"]::before,
#masar_tabs_container [class*="tabs"]::after,
#masar_tabs_container [class*="tabitem"]::before,
#masar_tabs_container [class*="tabitem"]::after,
#masar_tabs_container [role="tabpanel"]::before,
#masar_tabs_container [role="tabpanel"]::after {
    display: none !important;
    border: none !important;
    box-shadow: none !important;
}


/* v44 final: إزالة الإطار البرتقالي من عناصر رفع الملفات فقط */
.gradio-container .upload-container,
.gradio-container [data-testid="file"],
.gradio-container .file-preview,
.gradio-container input[type="file"],
.gradio-container .gr-file,
.gradio-container .block:has(input[type="file"]) {
    border-color: #004d40 !important;
    outline-color: #004d40 !important;
    box-shadow: none !important;
}

.gradio-container .upload-container:focus-within,
.gradio-container .block:has(input[type="file"]):focus-within {
    border-color: #004d40 !important;
    box-shadow: 0 0 0 2px rgba(0,77,64,0.2) !important;
}


/* v44 final safe: إزالة الإطار البرتقالي من عناصر Gradio داخل حاوية الأقسام فقط */
#masar_tabs_container .block,
#masar_tabs_container .form,
#masar_tabs_container .gap,
#masar_tabs_container details,
#masar_tabs_container .tabitem,
#masar_tabs_container [data-testid],
#masar_tabs_container .wrap,
#masar_tabs_container .container {
    border-color: #e5e7eb !important;
    outline: none !important;
    box-shadow: none !important;
}

#masar_tabs_container .block:focus,
#masar_tabs_container .block:focus-within,
#masar_tabs_container *:focus {
    outline: none !important;
    border-color: #004d40 !important;
    box-shadow: none !important;
}

/* تحديداً لعناصر الرفع داخل الأقسام */
#masar_tabs_container .upload-container,
#masar_tabs_container .file-preview {
    border-color: #e5e7eb !important;
    box-shadow: none !important;
}



/* v1.8.3 Fix 4 — RTL polish and school data center icon organization */
.gradio-container,
.gradio-container label,
.gradio-container textarea,
.gradio-container input,
.gradio-container table,
.gradio-container th,
.gradio-container td,
.gradio-container .prose,
.gradio-container .markdown,
.gradio-container [data-testid="block-info"],
.gradio-container [role="combobox"],
.gradio-container [role="listbox"],
.gradio-container [role="option"] {
    direction: rtl !important;
    text-align: right !important;
}

.gradio-container th,
.gradio-container td {
    text-align: center !important;
}

/* Dropdown popups can be rendered outside the local component tree, because apparently menus need independence. */
div[data-testid="dropdown-options"],
div[data-testid="dropdown-options"] *,
.svelte-select-list,
.svelte-select-list *,
[role="listbox"],
[role="option"] {
    direction: rtl !important;
    text-align: right !important;
}

.school-data-center-note {
    background: #eef6f3;
    color: #004d40;
    border-right: 5px solid #0f766e;
    border-radius: 12px;
    padding: 12px;
    margin: 10px 0 14px;
    font-weight: 800;
    line-height: 1.8;
    text-align: right !important;
    direction: rtl !important;
}

.school-data-icon-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 12px;
    margin: 10px 0 18px;
    direction: rtl !important;
}

.school-data-icon-card {
    direction: rtl !important;
    text-align: center !important;
    background: #ffffff;
    border: 1px solid #dbe3e8;
    border-radius: 16px;
    padding: 15px 12px;
    box-shadow: 0 8px 20px rgba(15, 118, 110, 0.08);
    min-height: 120px;
}

.school-data-icon-card * {
    text-align: center !important;
}

.school-data-icon {
    display: block;
    font-size: 31px;
    line-height: 1;
    margin-bottom: 9px;
}

.school-data-card-title {
    font-size: 17px;
    font-weight: 900;
    color: #004d40;
    margin-bottom: 6px;
}

.school-data-card-desc {
    font-size: 13px;
    font-weight: 700;
    color: #475569;
    line-height: 1.7;
}



/* v1.8.3 Fix 5 — clickable school data center panels */
.school-data-nav-row {
    direction: rtl !important;
    gap: 10px !important;
    margin: 10px 0 14px !important;
}
.school-data-nav-btn {
    min-height: 82px !important;
    border-radius: 16px !important;
    background: #ffffff !important;
    border: 1px solid #dbe3e8 !important;
    color: #004d40 !important;
    font-weight: 900 !important;
    box-shadow: 0 8px 20px rgba(15, 118, 110, 0.08) !important;
    white-space: pre-line !important;
    line-height: 1.55 !important;
}
.school-data-nav-btn:hover {
    transform: translateY(-1px);
    box-shadow: 0 10px 24px rgba(15, 118, 110, 0.13) !important;
}
.school-data-panel-box {
    direction: rtl !important;
    text-align: right !important;
    background: #ffffff;
    border: 1px solid #dbe3e8;
    border-radius: 16px;
    padding: 12px;
    margin-top: 10px;
    box-shadow: 0 8px 20px rgba(15, 118, 110, 0.06);
}
.school-data-panel-title {
    background: #eef6f3;
    color: #004d40;
    border-right: 5px solid #0f766e;
    border-radius: 12px;
    padding: 11px 12px;
    margin: 4px 0 14px;
    font-weight: 900;
    line-height: 1.8;
    text-align: right;
}



/* v1.8.3 Fix 6 — restore official header alignment and continuous login wave */
.main-header,
.main-header * {
    box-sizing: border-box !important;
}

.main-header {
    direction: ltr !important;
}

.main-header .header-grid {
    direction: ltr !important;
    display: grid !important;
    grid-template-columns: 115px minmax(320px, 1fr) 300px !important;
    grid-template-areas:
        "logo title ministry"
        "logo school ministry"
        "logo credits ministry" !important;
    align-items: center !important;
    gap: 5px 20px !important;
    max-width: 1200px !important;
    margin: 0 auto !important;
}

.main-header .h-logo {
    grid-area: logo !important;
    justify-self: start !important;
    text-align: left !important;
    direction: ltr !important;
}

.main-header .h-logo img {
    display: inline-block !important;
}

.main-header .h-ministry {
    grid-area: ministry !important;
    justify-self: end !important;
    text-align: right !important;
    direction: rtl !important;
    min-width: 240px !important;
}

.main-header .h-title,
.main-header .h-school,
.main-header .h-credits {
    direction: rtl !important;
    text-align: center !important;
}

.main-header .h-title {
    grid-area: title !important;
}

.main-header .h-school {
    grid-area: school !important;
}

.main-header .h-credits {
    grid-area: credits !important;
}

.masar-login-wave-wrap {
    margin-bottom: -4px !important;
    line-height: 0 !important;
    overflow: hidden !important;
    margin-left: -30px !important;
    margin-right: -30px !important;
    height: 18px !important;
    direction: ltr !important;
}

.masar-login-wave-track {
    direction: ltr !important;
    display: flex !important;
    width: 200% !important;
    height: 18px !important;
    animation: waveMoveContinuous 4.5s linear infinite !important;
    will-change: transform !important;
}

.masar-login-wave-track svg {
    display: block !important;
    width: 50% !important;
    min-width: 50% !important;
    flex: 0 0 50% !important;
    height: 18px !important;
}

@keyframes waveMoveContinuous {
    0% { transform: translateX(0); }
    100% { transform: translateX(-50%); }
}

.login-box,
.login-box * {
    text-align: center;
}

.login-box input {
    text-align: center !important;
    direction: rtl !important;
}

@media (max-width: 768px) {
    .main-header .header-grid {
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        text-align: center !important;
        gap: 10px !important;
    }

    .main-header .h-logo,
    .main-header .h-ministry,
    .main-header .h-title,
    .main-header .h-school,
    .main-header .h-credits {
        justify-self: center !important;
        text-align: center !important;
    }
}



/* v1.8.3 Fix 7 — remove double-click feeling in school data center panels */
.direct-panel-accordion {
    border: 0 !important;
    box-shadow: none !important;
    background: transparent !important;
    margin-top: 0 !important;
}

.direct-panel-accordion > details,
.direct-panel-accordion details {
    border: 0 !important;
    box-shadow: none !important;
    background: transparent !important;
}

.direct-panel-accordion summary,
.direct-panel-accordion > details > summary {
    display: none !important;
}

.direct-panel-accordion .wrap,
.direct-panel-accordion .form,
.direct-panel-accordion .block {
    border-top: 0 !important;
}

.school-data-panel-box {
    scroll-margin-top: 20px;
}

.school-data-panel-box .school-data-panel-title {
    margin-bottom: 14px !important;
}



/* v1.8.3 Fix 8 — restore old login wave feel, official header centering, and panel focus */
.main-header {
    direction: ltr !important;
    position: relative !important;
}

.main-header .header-grid,
.header-grid {
    direction: ltr !important;
    display: grid !important;
    grid-template-columns: 300px minmax(0, 1fr) 300px !important;
    grid-template-areas:
        "logo title ministry"
        "logo school ministry"
        "logo credits ministry" !important;
    align-items: center !important;
    justify-items: center !important;
    gap: 5px 16px !important;
    width: 100% !important;
    max-width: 1220px !important;
    margin: 0 auto !important;
}

.main-header .h-logo,
.h-logo {
    grid-area: logo !important;
    justify-self: start !important;
    text-align: left !important;
    direction: ltr !important;
}

.main-header .h-ministry,
.h-ministry {
    grid-area: ministry !important;
    justify-self: end !important;
    text-align: right !important;
    direction: rtl !important;
    width: 300px !important;
}

.main-header .h-title,
.h-title {
    grid-area: title !important;
    justify-self: center !important;
    text-align: center !important;
    direction: rtl !important;
    width: 100% !important;
}

.main-header .h-school,
.h-school {
    grid-area: school !important;
    justify-self: center !important;
    text-align: center !important;
    direction: rtl !important;
    width: 100% !important;
}

.main-header .h-credits,
.h-credits {
    grid-area: credits !important;
    justify-self: center !important;
    text-align: center !important;
    direction: rtl !important;
    width: 100% !important;
}

.main-header .credits-box,
.h-credits .credits-box {
    margin-left: auto !important;
    margin-right: auto !important;
    text-align: center !important;
}

@media (max-width: 900px) {
    .main-header .header-grid,
    .header-grid {
        grid-template-columns: 150px minmax(0, 1fr) 150px !important;
        gap: 4px 10px !important;
    }
    .main-header .h-ministry,
    .h-ministry {
        width: 150px !important;
        font-size: 11px !important;
    }
}

@media (max-width: 768px) {
    .main-header .header-grid,
    .header-grid {
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        text-align: center !important;
    }
    .main-header .h-logo,
    .main-header .h-ministry,
    .main-header .h-title,
    .main-header .h-school,
    .main-header .h-credits,
    .h-logo,
    .h-ministry,
    .h-title,
    .h-school,
    .h-credits {
        justify-self: center !important;
        text-align: center !important;
        width: 100% !important;
    }
}

"""


def apply_school_theme_to_css(css_text):
    """تطبيق ألوان الهوية المحفوظة عند تشغيل التطبيق."""
    themed = str(css_text)
    replacements = {
        "#004d40": THEME_COLOR,
        "#00695c": THEME_COLOR_2,
        "#ffca28": ACCENT_COLOR,
    }
    for old_color, new_color in replacements.items():
        themed = themed.replace(old_color, new_color)
        themed = themed.replace(old_color.upper(), new_color)
    return themed

css = apply_school_theme_to_css(css)

js_code = """
function() {
    let isRunning = false;

    const removeDark = () => {
        if (isRunning) return;

        const html = document.documentElement;
        const body = document.body;
        if (!html || !body) return;

        const needsFix =
            html.classList.contains('dark') ||
            body.classList.contains('dark') ||
            html.getAttribute('data-theme') === 'dark';

        if (!needsFix) return;

        isRunning = true;
        html.classList.remove('dark');
        body.classList.remove('dark');
        html.setAttribute('data-theme', 'light');
        body.style.backgroundColor = '#ffffff';
        setTimeout(() => { isRunning = false; }, 100);
    };

    removeDark();

    const observer = new MutationObserver(removeDark);
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['class', 'data-theme']
    });

    if (document.body) {
        observer.observe(document.body, {
            attributes: true,
        });
    }
}


/* v44-AV — تفاعل لمس شعار الدخول */
function setupMainLogoTouchInteraction() {
    const logo = document.getElementById('main-logo');
    if (logo && !logo.dataset.touchReady) {
        logo.dataset.touchReady = '1';
        logo.addEventListener('touchmove', function(e) {
            const touch = e.touches[0];
            const rect = logo.getBoundingClientRect();
            const x = touch.clientX - rect.left - rect.width/2;
            const y = touch.clientY - rect.top - rect.height/2;
            const rotX = -(y / rect.height) * 30;
            const rotY = (x / rect.width) * 30;
            logo.style.transform = `perspective(400px) rotateX(${rotX}deg) rotateY(${rotY}deg) scale(1.12)`;
            logo.style.animation = 'none';
        }, { passive: true });
        logo.addEventListener('touchend', function() {
            logo.style.transform = '';
            logo.style.animation = 'logo4d 4s ease-in-out infinite';
        });
    }
}

setTimeout(setupMainLogoTouchInteraction, 300);
setTimeout(setupMainLogoTouchInteraction, 1000);
setTimeout(setupMainLogoTouchInteraction, 2500);
document.addEventListener('DOMContentLoaded', setupMainLogoTouchInteraction);

"""

header_html = build_header_html()

def filter_swap_teachers_safe(dept):
    try:
        choices = get_teacher_choices(dept if dept != "الكل" else "الكل")
        if not choices:
            return gr.update(choices=["لا يوجد معلمون"], value=None)
        return gr.update(choices=choices, value=None)
    except Exception:
        return gr.update(choices=[], value=None)

def get_teacher_periods_safe(t, d):
    try:
        if t and t in teachers_db and t != "لا يوجد معلمون":
            periods_elegant = []
            for k, v in teachers_db[t].get(d, {}).items():
                if str(k).isdigit() and str(v).strip() != "" and str(v).lower() != "nan":
                    elegant_c = format_elegant_class(v)
                    display_text = f"الحصة {k} - ({elegant_c})"
                    periods_elegant.append(display_text)
            periods_elegant.sort(key=lambda x: int(x.split("-")[0].replace("الحصة", "").strip()))
            if not periods_elegant: return gr.update(choices=["لا توجد حصص"], value=None)
            return gr.update(choices=periods_elegant, value=None)
        return gr.update(choices=["اختر معلماً أولاً"], value=None)
    except Exception as e:
        return gr.update(choices=["خطأ داخلي"], value=None)
def extract_clean_period_number(period_value):
    raw = str(period_value).split("-")[0]
    raw = raw.replace("✅", "").replace("الحصة", "").strip()
    return raw if raw.isdigit() else ""

def get_teacher_periods_marked(t, d, confirmed_state, current_value=None):
    t = str(t or "").split(" (")[0].strip()
    try:
        if not t or t not in teachers_db or t == "لا يوجد معلمون":
            return gr.update(choices=["اختر معلماً أولاً"], value=None)

        confirmed_keys = set()
        if isinstance(confirmed_state, dict):
            confirmed_keys = {str(k) for k in confirmed_state.keys()}

        choices = []
        selected_value = None
        current_clean = extract_clean_period_number(current_value)

        for k, v in teachers_db[t].get(d, {}).items():
            if str(k).isdigit() and str(v).strip() != "" and str(v).lower() != "nan":
                elegant_c = format_elegant_class(v)
                prefix = "✅ " if str(k) in confirmed_keys else ""
                display_text = f"{prefix}الحصة {k} - ({elegant_c})"
                choices.append((int(k), display_text))

        choices.sort(key=lambda x: x[0])
        final_choices = [text for _, text in choices]

        if current_clean:
            for k, text in choices:
                if str(k) == current_clean:
                    selected_value = text
                    break

        if not final_choices:
            return gr.update(choices=["لا توجد حصص"], value=None)

        return gr.update(choices=final_choices, value=selected_value)

    except Exception:
        return gr.update(choices=["خطأ داخلي"], value=None)
        
def run_radar_safe(t, p, d):
    t = str(t or "").split(" (")[0].strip()
    default_msg = "💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا..."
    try:
        if not t or not p or "لا يوجد" in t or "اختر" in p: return gr.update(choices=[], value=None), gr.update(value=default_msg), gr.update(value="")
        
        p_str_clean = extract_clean_period_number(p)
        if not p_str_clean.isdigit(): return gr.update(choices=[], value=None), gr.update(value=default_msg), gr.update(value="")
        p_int = int(p_str_clean)
        
        t_cls = teachers_db.get(t, {}).get(d, {}).get(str(p_int), teachers_db.get(t, {}).get(d, {}).get(p_int, ""))
        if not t_cls: return gr.update(choices=["❌ لا توجد حصة مسجلة لك"], value=None), gr.update(value=default_msg), gr.update(value="")
        
        dna = get_class_dna(t_cls)
        perf, flex = [], []
        
        day_weights = {"الأحد": 1, "الإثنين": 2, "الثلاثاء": 3, "الأربعاء": 4, "الخميس": 5}
        current_day_str = get_current_day_oman()
        current_weight = day_weights.get(current_day_str, 1)
        
        for tb, info in teachers_db.items():
            if tb == t or info.get("dept") == "الهيئة الإدارية" or info.get("role") == "إداري": continue
            if str(p_int) in info.get(d, {}) or p_int in info.get(d, {}): continue
            
            for db in SCHOOL_WEEK_DAYS:
                db_weight = day_weights.get(db, 1)
                db_display = f"{db} القادم" if db_weight < current_weight else db
                
                for pb, cb in info.get(db, {}).items():
                    if dna == get_class_dna(cb) and dna != "":
                        w_b = check_teacher_load(tb, d, p_int)
                        is_t_free = True
                        if str(pb) in teachers_db.get(t, {}).get(db, {}): is_t_free = False
                        elif str(pb).isdigit() and int(str(pb)) in teachers_db.get(t, {}).get(db, {}): is_t_free = False
                        
                        if is_t_free:
                            w_a = check_teacher_load(t, db, pb)
                            warns = []
                            if w_b: warns.append(f"إجهاد لـ {tb}: {w_b}")
                            if w_a: warns.append(f"إجهاد لك: {w_a}")
                            w_str = f" ⚠️ ({' | '.join(warns)})" if warns else ""
                            perf.append(f"🟢 تبادل مثالي | البديل: {tb} | يغطيك ({d} ح{p_int}) وتغطيه ({db_display} ح{pb}){w_str}")
                        else:
                            w_str = f" ⚠️ (إجهاد لـ {tb}: {w_b})" if w_b else ""
                            flex.append(f"🟠 إنقاذ مرن | البديل: {tb} | يغطيك ({d} ح{p_int}) لكنك مشغول وقت حصته ({db_display} ح{pb}){w_str}")
                            
        res = sorted(list(set(perf))) + sorted(list(set(flex)))
        if not res: return gr.update(choices=[f"❌ لا يوجد بديل متفرغ (بصمة: {dna})"], value=None), gr.update(value=default_msg), gr.update(value="")
        return gr.update(choices=res, value=None), gr.update(value=default_msg), gr.update(value="")
    except Exception as e:
        return gr.update(choices=["خطأ داخلي"], value=None), gr.update(value=default_msg), gr.update(value="")

def generate_wa_msg(choice, t_req, p_req, d_req):
    default_msg = "💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا..."
    if not choice or "❌" in choice or "خطأ" in choice: return gr.update(value=default_msg), gr.update(value="")
    try:
        parts = choice.split("|")
        t_target = parts[1].split(":")[1].strip()
        details = parts[2].strip()
        
        p_req_clean = extract_clean_period_number(p_req)
        t_req_clean = str(t_req or "").split(" (")[0].strip()
        req_class_raw = teachers_db.get(t_req_clean, {}).get(d_req, {}).get(p_req_clean, teachers_db.get(t_req_clean, {}).get(d_req, {}).get(int(p_req_clean) if p_req_clean.isdigit() else p_req_clean, ""))
        req_class_elegant = format_elegant_class(req_class_raw)
        
        msg = f"السلام عليكم ورحمة الله وبركاته أستاذي العزيز ({t_target}) 🌹\n\n"
        msg += f"يرغب الأستاذ ({t_req}) بالتبادل الودي معك (بعد إذنك وموافقتك طبعاً لظرف طارئ).\n"
        msg += f"ستقوم أنت مشكوراً بتغطية الصف ({req_class_elegant}) في الحصة ({p_req_clean}) يوم ({d_req}).\n"
        
        if "مثالي" in choice:
            rep_part = details.split("وتغطيه ")[1].split(")")[0].replace("(", "")
            rep_day, rep_period = rep_part.split(" ح")
            
            clean_rep_day = rep_day.replace(" القادم", "").strip()
            target_class_raw = teachers_db.get(t_target, {}).get(clean_rep_day, {}).get(str(rep_period), teachers_db.get(t_target, {}).get(clean_rep_day, {}).get(int(rep_period) if str(rep_period).isdigit() else rep_period, ""))
            target_class_elegant = format_elegant_class(target_class_raw)
            
            msg += f"وسيقوم الأستاذ ({t_req}) بتغطية الصف ({target_class_elegant}) في الحصة ({rep_period}) يوم ({rep_day}) بدلاً عنك.\n\n"
        else:
            msg += f"ونظراً لانشغال الأستاذ ({t_req}) وقت حصتك، سيتم التنسيق لرد الحصة لاحقاً.\n\n"
            
        msg += "هل يناسبك هذا التبادل ليتم اعتماده؟ شاكرين ومقدرين تعاونك 🤝"
        
        phone = teachers_db.get(t_target, {}).get("phone", "")
        btn_color = "#25D366" 
        
        if phone:
            phone = "".join(filter(str.isdigit, str(phone)))
            if len(phone) == 8: phone = "968" + phone
            btn_text = f"✅ إرسال للأستاذ {t_target}"
        else:
            phone = ""
            btn_text = f"⚠️ إرسال (لا يوجد رقم)"
            
        encoded_msg = urllib.parse.quote(msg)
        wa_link = f"https://api.whatsapp.com/send?phone={phone}&text={encoded_msg}"
        
        btn_html = f'<div style="margin-top: 10px; border: 2px solid {btn_color}; border-radius: 8px; padding: 2px;"><a href="{wa_link}" target="_blank" style="display: block; width: 100%; text-align: center; background-color: {btn_color}; color: white; padding: 12px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 16px;">{btn_text}</a></div>'
        
        return gr.update(value=msg), gr.update(value=btn_html)

    except Exception as e:
        return gr.update(value=default_msg), gr.update(value="")

def _get_update_value(obj, fallback=""):
    try:
        if isinstance(obj, dict):
            return obj.get("value", fallback)
        return getattr(obj, "value", fallback)
    except Exception:
        return fallback

def _get_update_choices(obj):
    try:
        if isinstance(obj, dict):
            return obj.get("choices", [])
        return getattr(obj, "choices", [])
    except Exception:
        try:
            return obj["choices"]
        except Exception:
            return []

def get_swap_candidates_for_period(t, period_value, d, confirmed_state):
    if not t or not period_value:
        return (
            gr.update(choices=[], value=None, visible=True),
            gr.update(value=SWAP_EMPTY_MSG, visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True, interactive=False)
        )

    opts_update, _, _ = run_radar_safe(t, period_value, d)
    candidates = _get_update_choices(opts_update)

    if not candidates:
        candidates = ["❌ لا يوجد بديل متفرغ"]

    p_clean = extract_clean_period_number(period_value)

    saved_choice = None
    saved_message = SWAP_EMPTY_MSG
    btn_update = gr.update(value="", visible=True)
    confirm_update = gr.update(visible=True, interactive=False)

    if isinstance(confirmed_state, dict) and p_clean in confirmed_state:
        saved_choice = confirmed_state[p_clean].get("choice")
        if saved_choice not in candidates:
            saved_choice = None

        if saved_choice:
            saved_message = confirmed_state[p_clean].get("message", SWAP_EMPTY_MSG) or SWAP_EMPTY_MSG
            _, btn_raw = generate_wa_msg(saved_choice, t, period_value, d)
            btn_value = _get_update_value(btn_raw, "")
            btn_update = gr.update(value=btn_value, visible=True)
            confirm_update = gr.update(visible=True, interactive=True)

    return (
        gr.update(choices=candidates, value=saved_choice, visible=True),
        gr.update(value=saved_message, visible=True),
        btn_update,
        confirm_update
    )

def on_swap_option_selected(choice, t, period_value, d):
    if not choice:
        return (
            gr.update(value=SWAP_EMPTY_MSG, visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True, interactive=False)
        )

    if "❌" in str(choice):
        return (
            gr.update(value=SWAP_EMPTY_MSG, visible=True),
            gr.update(value="", visible=True),
            gr.update(visible=True, interactive=False)
        )

    msg_upd, btn_upd = generate_wa_msg(choice, t, period_value, d)

    msg_value = _get_update_value(msg_upd, SWAP_EMPTY_MSG)
    btn_value = _get_update_value(btn_upd, "")

    return (
        gr.update(value=msg_value, visible=True),
        gr.update(value=btn_value, visible=True),
        gr.update(visible=True, interactive=True)
    )

def on_swap_option_selected_from_event(choice_current, t, period_value, d, evt: gr.SelectData):
    choice = None

    if evt is not None:
        try:
            choice = evt.value
        except Exception:
            choice = None

    if not choice:
        choice = choice_current

    if not choice:
        try:
            choice = str(evt) if evt else None
        except Exception:
            choice = None

    return on_swap_option_selected(choice, t, period_value, d)

def get_leader_action_button_updates(abs_teacher, period_value=None, substitute_teacher=None):
    has_absent = bool(clean_teacher_name_from_ui(abs_teacher))
    clean_period = extract_clean_period_number(period_value) if period_value else ""
    has_period = bool(str(clean_period or "").strip())
    sub_clean = str(substitute_teacher or "").strip()
    valid_substitute = bool(
        sub_clean
        and sub_clean not in ["ℹ️ اختر الحصة أولاً", "❌ لا يوجد بديل مناسب", "اختر الحصة أولاً"]
        and not sub_clean.startswith("❌")
    )
    has_substitute = bool(has_absent and has_period and valid_substitute)

    return (
        gr.update(interactive=has_substitute),              # تكليف احتياط رسمي
        gr.update(interactive=has_substitute),              # اعتماد كتبادل
        gr.update(interactive=has_absent and has_period),   # رصد تقصير في التكليف
        gr.update(interactive=has_absent),                  # التراجع عن غياب اليوم بالكامل
    )




def show_home_dashboard_js():
    return """() => {
        const home = document.getElementById('masar_home_dashboard');
        const tabsBox = document.getElementById('masar_tabs_container');
        if (home) home.style.setProperty('display', 'block', 'important');
        if (tabsBox) tabsBox.style.setProperty('display', 'none', 'important');

        const centerVisibleOrphanCards = () => {
            const grid = document.querySelector('#masar_home_dashboard .masar-card-grid');
            if (!grid) return;

            const cards = Array.from(grid.querySelectorAll('.masar-card'));
            cards.forEach(card => card.style.removeProperty('grid-column'));

            if (window.innerWidth < 769) return;

            const visibleCards = cards.filter(card => {
                const st = window.getComputedStyle(card);
                return st.display !== 'none' && st.visibility !== 'hidden' && card.offsetParent !== null;
            });

            if (visibleCards.length > 1 && visibleCards.length % 3 === 1) {
                visibleCards[visibleCards.length - 1].style.setProperty('grid-column', '2 / span 1', 'important');
            }
        };

        setTimeout(centerVisibleOrphanCards, 80);
        setTimeout(centerVisibleOrphanCards, 300);
        setTimeout(centerVisibleOrphanCards, 700);

        if (!window.__masarCenterCardsResizeReady) {
            window.__masarCenterCardsResizeReady = true;
            window.addEventListener('resize', centerVisibleOrphanCards, { passive: true });
        }

        window.scrollTo({ top: 0, behavior: 'smooth' });
    }"""

def return_home_dashboard_js():
    return """() => {
        const home = document.getElementById('masar_home_dashboard');
        const tabsBox = document.getElementById('masar_tabs_container');
        if (tabsBox) tabsBox.style.setProperty('display', 'none', 'important');
        if (home) home.style.setProperty('display', 'block', 'important');

        const centerVisibleOrphanCards = () => {
            const grid = document.querySelector('#masar_home_dashboard .masar-card-grid');
            if (!grid) return;

            const cards = Array.from(grid.querySelectorAll('.masar-card'));
            cards.forEach(card => card.style.removeProperty('grid-column'));

            if (window.innerWidth < 769) return;

            const visibleCards = cards.filter(card => {
                const st = window.getComputedStyle(card);
                return st.display !== 'none' && st.visibility !== 'hidden' && card.offsetParent !== null;
            });

            if (visibleCards.length > 1 && visibleCards.length % 3 === 1) {
                visibleCards[visibleCards.length - 1].style.setProperty('grid-column', '2 / span 1', 'important');
            }
        };

        setTimeout(centerVisibleOrphanCards, 80);
        setTimeout(centerVisibleOrphanCards, 300);
        setTimeout(centerVisibleOrphanCards, 700);

        if (!window.__masarCenterCardsResizeReady) {
            window.__masarCenterCardsResizeReady = true;
            window.addEventListener('resize', centerVisibleOrphanCards, { passive: true });
        }

        window.scrollTo({ top: 0, behavior: 'smooth' });
    }"""

def select_tab_js(label_text, tab_index):
    safe_label = str(label_text).replace("\\", "\\\\").replace("'", "\\'")
    try:
        safe_index = int(tab_index)
    except Exception:
        safe_index = 0

    return f"""() => {{
        const home = document.getElementById('masar_home_dashboard');
        const tabsBox = document.getElementById('masar_tabs_container');

        // أخفِ اللوحة الرئيسية فقط.
        if (home) home.style.setProperty('display', 'none', 'important');

        // أظهر حاوية التبويبات أولًا حتى تكون عناصرها موجودة للبحث والنقر.
        if (tabsBox) {{
            tabsBox.style.setProperty('display', 'block', 'important');
            tabsBox.style.setProperty('visibility', 'hidden', 'important');

            // مهم: لا تترك شريط التبويبات display:none، لأن JS لن يضغطه بثبات.
            const tabLists = tabsBox.querySelectorAll('.tab-nav, [role="tablist"]');
            tabLists.forEach(el => {{
                el.style.setProperty('display', 'flex', 'important');
                el.style.setProperty('position', 'absolute', 'important');
                el.style.setProperty('width', '1px', 'important');
                el.style.setProperty('height', '1px', 'important');
                el.style.setProperty('overflow', 'hidden', 'important');
                el.style.setProperty('opacity', '0', 'important');
                el.style.setProperty('pointer-events', 'none', 'important');
            }});
        }}

        setTimeout(() => {{
            const selectors = [
                '#masar_tabs_container [role="tab"]',
                '#masar_tabs_container .tab-nav button',
                '#masar_tabs_container button[aria-controls]',
                '#masar_tabs_container button',
                '.masar-tabs-container [role="tab"]',
                '.masar-tabs-container .tab-nav button',
                '.masar-tabs-container button[aria-controls]'
            ];

            let tabs = [];
            selectors.forEach(sel => {{
                document.querySelectorAll(sel).forEach(el => {{
                    const txt = (el.textContent || '').trim();
                    if (txt && !tabs.includes(el)) tabs.push(el);
                }});
            }});

            const wanted = '{safe_label}';
            let target = tabs.find(btn => (btn.textContent || '').includes(wanted));

            if (!target && tabs.length > {safe_index}) {{
                target = tabs[{safe_index}];
            }}

            if (target) {{
                target.click();
                target.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
            }}

            setTimeout(() => {{
                if (tabsBox) {{
                    tabsBox.style.setProperty('display', 'block', 'important');
                    tabsBox.style.setProperty('visibility', 'visible', 'important');
                }}
                window.scrollTo({{ top: 0, behavior: 'smooth' }});
            }}, 160);
        }}, 100);
    }}"""


def show_home_dashboard_after_login(dept_value, is_admin, is_owner, role=""):
    permissions = get_permissions(
        role=role,
        is_owner=is_owner,
        dept_value=dept_value,
        is_admin_flag=is_admin
    )

    return (
        gr.update(),               # home_dashboard controlled by JS/CSS
        gr.update(),               # tabs_container controlled by JS/CSS
        gr.update(visible=permissions["can_view_distribution"]),
        gr.update(visible=permissions["can_view_balances"]),
        gr.update(visible=permissions["can_manage_exemptions"]),
        gr.update(visible=permissions["can_view_swap"]),
        gr.update(visible=permissions["can_view_day_table"]),
        gr.update(visible=permissions["can_view_teacher_table"]),
        gr.update(visible=permissions["can_access_school_data"]),
    )

def open_home_section(tab_id=None):
    return (
        gr.update(),  # home_dashboard controlled by JS
        gr.update(),  # tabs_container controlled by JS
        gr.update(selected=tab_id or "distribution"),  # main_tabs selected by Gradio
    )

def return_to_home_dashboard():
    return (
        gr.update(),  # home_dashboard controlled by JS
        gr.update(),  # tabs_container controlled by JS
    )

def show_selected_tab_container_js():
    return """() => {
        const home = document.getElementById('masar_home_dashboard');
        const tabsBox = document.getElementById('masar_tabs_container');

        if (home) home.style.setProperty('display', 'none', 'important');

        if (tabsBox) {
            tabsBox.style.setProperty('display', 'block', 'important');
            tabsBox.style.setProperty('visibility', 'visible', 'important');

            const tabLists = tabsBox.querySelectorAll('.tab-nav, [role="tablist"]');
            tabLists.forEach(el => {
                el.style.setProperty('display', 'flex', 'important');
                el.style.setProperty('position', 'absolute', 'important');
                el.style.setProperty('width', '1px', 'important');
                el.style.setProperty('height', '1px', 'important');
                el.style.setProperty('overflow', 'hidden', 'important');
                el.style.setProperty('opacity', '0', 'important');
                el.style.setProperty('pointer-events', 'none', 'important');
            });
        }

        window.scrollTo({ top: 0, behavior: 'smooth' });
    }"""

# ================================================================
# واجهة Gradio الرئيسية — كل شيء داخل كتلة واحدة
# ================================================================
with gr.Blocks() as app:
    current_user_is_admin = gr.State(value=False)
    current_user_is_owner = gr.State(value=False)
    current_user_name = gr.State(value="")
    current_user_role = gr.State(value="")
    current_user_account_id = gr.State(value="")
    current_schedule_state = gr.State()
    reserve_generation_state = gr.State(value=get_empty_generation_state())

    with gr.Column(visible=True, elem_classes="login-box") as login_container:
        login_branding_html = gr.HTML(value=build_login_branding_html())

        pin_input = gr.Textbox(type="password", show_label=False, placeholder="Enter ثم اضغط (PIN) 🔑 أدخل رمز الدخول", text_align="center")
        login_btn = gr.Button("تسجيل الدخول", elem_classes="admin-btn")
        login_msg = gr.HTML()
        login_credits_html = gr.HTML(value=build_login_credits_html())

    with gr.Column(visible=False) as main_app_container:
        header_branding_html = gr.HTML(value=build_header_html())
        
        with gr.Row(elem_classes="top-user-row"):
            with gr.Column(scale=5, elem_classes="welcome-col"):
                welcome_html = gr.HTML(elem_classes="welcome-html-box")
            with gr.Column(scale=1, min_width=120, elem_classes="logout-col"):
                logout_btn = gr.Button("🚪 خروج و إقفال", elem_classes=["reset-btn", "logout-btn"])
        
        with gr.Accordion("🔑 تغيير رمز دخولي", open=False):
            gr.HTML(
                "<div style='background:#eef6f3;color:#004d40;padding:10px;"
                "border-radius:8px;border-right:4px solid #0f766e;"
                "font-weight:800;line-height:1.7;'>"
                "اكتب رمزك الحالي ثم الرمز الجديد. رمز مالك النظام يُدار من Secret الاستضافة."
                "</div>"
            )
            with gr.Row():
                self_current_pin = gr.Textbox(
                    type="password",
                    label="الرمز الحالي",
                )
                self_new_pin = gr.Textbox(
                    type="password",
                    label="الرمز الجديد",
                )
                self_confirm_pin = gr.Textbox(
                    type="password",
                    label="تأكيد الرمز الجديد",
                )
            self_change_pin_btn = gr.Button(
                "حفظ رمز الدخول الجديد",
                elem_classes="admin-btn",
            )
            self_change_pin_status = gr.HTML()

        with gr.Column(visible=True, elem_id="masar_home_dashboard", elem_classes="masar-home-dashboard") as home_dashboard:
            home_hero_html = gr.HTML(value=build_home_hero_html())

            with gr.Row(elem_classes="masar-card-grid"):
                with gr.Column(visible=True, elem_classes="masar-card") as card_distribution:
                    gr.HTML("<div class='masar-card-icon'>📋</div><div class='masar-card-title'>التوزيع والاحتياط</div><div class='masar-card-desc'>إدارة الغياب وتوزيع حصص الاحتياط.</div>")
                    btn_open_distribution = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_balances:
                    gr.HTML("<div class='masar-card-icon'>⚖️</div><div class='masar-card-title'>الأرصدة والتقارير</div><div class='masar-card-desc'>متابعة الأرصدة، الغياب، والتقارير.</div>")
                    btn_open_balances = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_exemptions:
                    gr.HTML("<div class='masar-card-icon'>🛡️</div><div class='masar-card-title'>حالات الإعفاء</div><div class='masar-card-desc'>تثبيت ومراجعة إعفاءات المعلمين.</div>")
                    btn_open_exemptions = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_swap:
                    gr.HTML("<div class='masar-card-icon'>🤝</div><div class='masar-card-title'>التبادل الودي الأسبوعي</div><div class='masar-card-desc'>تنظيم التبادلات بين المعلمين الحاضرين.</div>")
                    btn_open_swap = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_day:
                    gr.HTML("<div class='masar-card-icon'>📅</div><div class='masar-card-title'>جدول اليوم</div><div class='masar-card-desc'>عرض جدول اليوم الدراسي حسب القسم.</div>")
                    btn_open_day = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_teacher:
                    gr.HTML("<div class='masar-card-icon'>🔍</div><div class='masar-card-title'>جدول المعلم</div><div class='masar-card-desc'>عرض الجدول الأسبوعي للمعلم.</div>")
                    btn_open_teacher = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

                with gr.Column(visible=True, elem_classes="masar-card") as card_school_data:
                    gr.HTML("<div class='masar-card-icon'>🏫</div><div class='masar-card-title'>مركز البيانات المدرسية</div><div class='masar-card-desc'>إدارة الملفات المرجعية للمنظومة.</div>")
                    btn_open_school_data = gr.Button("دخول القسم ←", elem_classes="masar-card-btn")

        with gr.Column(visible=True, elem_id="masar_tabs_container", elem_classes="masar-tabs-container") as tabs_container:
            btn_back_home = gr.Button("🏠 العودة للوحة الرئيسية", elem_classes="home-back-btn")
            with gr.Row(elem_classes="yellow-box") as controls_row:
                dept_in = gr.Dropdown(["الكل"] + OFFICIAL_DEPTS, label="📂 مركز التحكم", value="الكل", scale=2)
                day_in = gr.Dropdown(SCHOOL_WEEK_DAYS, label="📅 اختر اليوم الدراسي", value=get_current_day_oman(), scale=2)
                refresh_btn = gr.Button("🔄 تحديث الشاشة والبيانات", elem_classes="refresh-btn", scale=1)

            with gr.Tabs(selected="distribution") as main_tabs:
                with gr.Tab("📋 التوزيع والاحتياط", id="distribution") as distribution_tab:
                    with gr.Column():
                        with gr.Accordion("⚙️ ضوابط التوزيع اليومية", open=True, elem_classes="yellow-box"):
                            max_reserves_input = gr.Number(value=1, label="🛑 الحد الأقصى للاحتياط لكل معلم في اليوم", precision=0)
                    
                        radar_warning_html = gr.HTML()
                        gr.HTML("""
                        <div class='reserve-guide-box'>
                            💡 <b>طريقة الاستخدام:</b><br>
                            ⚫️ حدد المعلمين الغائبين، ثم اضغط <b>توليد وتوزيع الاحتياط</b>.<br>
                            ⚫️ عند إضافة معلم غائب جديد، اضغط الزر نفسه لإضافة حصصه فوق التوزيع السابق دون تغييره، أو استخدم <b>إعادة توليد من جديد</b> لمسح محتوى اليوم وإعادة بنائه من الصفر.<br>
                            ⚫️ استخدم <b>مقترح آخر</b> لعرض توزيع آلي بديل للغيابات الحالية.
                        </div>
                        """)
                        abs_in = gr.Dropdown([], label="👨‍🏫 حدد المعلمين الغائبين", multiselect=True, elem_classes="absent-box")

                        with gr.Row():
                            btn = gr.Button("🚀 توليد وتوزيع الاحتياط", variant="primary", interactive=False, elem_classes="action-btn")
                            btn_regenerate = gr.Button("🔁 إعادة توليد من جديد", visible=False, interactive=False, elem_classes="regen-btn")
                            btn_alt = gr.Button("🪄 مقترح آخر", interactive=False, elem_classes="action-btn")
                            btn_img = gr.Button("🖼️ تحميل الجدول كصورة", interactive=False, elem_classes="export-btn")

                        date_display = gr.HTML(get_initial_header)
                        img_out = gr.Image(label="الصورة الجاهزة للنسخ", interactive=False)
                        tbl_out = gr.HTML(value="")
                    
                        with gr.Column(elem_classes="whatsapp-box"):
                            gr.Markdown("## 📱 مركز التواصل الذكي ومهام الواتساب")
                            with gr.Row(): msg_summary = gr.Textbox(label="📊 تقرير الجروب الإداري", lines=4, interactive=True)
                            with gr.Row(): msg_individual_html = gr.HTML(label="💌 بطاقات التكليف الفردية")

                        gr.HTML("<div class='external-section-title leader-title'>⚙️ لوحة القائد: التعديل اليدوي والتبادل</div>")
                        with gr.Accordion("فتح / إغلاق لوحة القائد", open=False, elem_classes="leader-accordion"):
                            with gr.Column(elem_classes="admin-zone"):
                                admin_zone_title = gr.HTML("<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة العمليات والقيادة</h4>")
                                admin_zone_help = gr.HTML("<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b;'>💡 <b>توضيح:</b> اختر المعلم الغائب ثم الحصة، وبعدها نفّذ الإجراء المناسب من نفس اللوحة حسب دورك وصلاحيتك.</div>")
                            
                                with gr.Row():
                                    edit_abs_t = gr.Dropdown([], label="1️⃣ المعلم الغائب", allow_custom_value=True)
                                    edit_period = gr.Dropdown([], label="2️⃣ اختر الحصة", allow_custom_value=False)
                                    edit_intervention_type = gr.Dropdown([], label="3️⃣ نطاق البحث عن بديل (تلقائي ذكي)", allow_custom_value=True)
                            
                                with gr.Row():
                                    cb_cross_dept = gr.Checkbox(label="🔓 تفعيل التعاون مع قسم آخر 🤝", visible=False)
                            
                                with gr.Row():
                                    edit_new_sub = gr.Dropdown([], label="4️⃣ البديل المنقذ", allow_custom_value=True)
                                with gr.Row():
                                    btn_apply_override = gr.Button("✍🏻 تكليف احتياط رسمي", elem_classes=["admin-btn", "leader-official-btn"], interactive=False)
                                    btn_apply_tabadul = gr.Button("🤝 اعتماد كـ تبادل", elem_classes=["tabadul-btn", "leader-swap-btn"], interactive=False)
                                    btn_apply_penalty = gr.Button("🚨 رصد تقصير في التكليف", elem_classes=["reset-btn", "leader-penalty-btn"], interactive=False)
                            
                                with gr.Row():
                                    btn_cancel_absence = gr.Button("⏪ إلغاء غياب اليوم بالكامل", elem_classes=["reset-btn", "leader-cancel-btn"], interactive=False)

                with gr.Tab("⚖️ الأرصدة والتقارير", id="balances") as balances_tab:
                    monthly_status = gr.HTML()

                    with gr.Row(elem_classes="yellow-box"):
                        export_btn = gr.Button("📥 تصدير تقرير المدرسة (Excel)", elem_classes="export-btn")
                        reset_month_btn = gr.Button("🔄 إقفال الشهر (تصفير الأرصدة فقط)", elem_classes="reset-btn", visible=False)
                
                    report_file = gr.File(label="📥 التقرير الجاهز للتحميل")

                    with gr.Row():
                        with gr.Column():
                            gr.HTML("<h3 style='text-align:center; color:#004d40; font-size: 1.3em; font-weight: 900; margin-bottom: 10px;'>🟢 رصيد الاحتياط</h3>")
                            tbl_bal = gr.HTML()
                        with gr.Column():
                            gr.HTML("<h3 style='text-align:center; color:#c62828; font-size: 1.3em; font-weight: 900; margin-bottom: 10px;'>🔴 حصر الغياب</h3>")
                            tbl_abs = gr.HTML()

                    with gr.Row():
                        with gr.Column():
                            gr.HTML("<h3 style='text-align:center; color:#b45309; font-size: 1.3em; font-weight: 900; margin-bottom: 10px;'>🟠 حالات التقصير</h3>")
                            tbl_short = gr.HTML()
                
                    gr.HTML("<div class='external-section-title vault-title'>🔒 الخزنة: تعديل يدوي للأرصدة والهواتف</div>")
                    with gr.Accordion("فتح / إغلاق الخزنة", open=False, elem_classes=["yellow-box", "vault-accordion"]):
                        gr.HTML("""
                        <div class='vault-guide-box'>
                            💡 <b>توضيح:</b><br>
                            ⚫️ اختر المعلم من القائمة لعرض بياناته الحالية.<br>
                            ⚫️ يمكن تعديل رصيد الاحتياط، مرات الغياب، وحالات التقصير عند الحاجة، ثم اضغط <b>حفظ التعديلات</b>.
                        </div>
                        """)
                        with gr.Row():
                            t_name = gr.Dropdown(list(teachers_db.keys()), label="المعلم")
                            t_dept_edit = gr.Textbox(label="القسم / المادة (للعرض فقط)", interactive=False)
                            t_role_edit = gr.Dropdown(ALL_ROLES, label="المنصب الإشرافي", interactive=False)
                        with gr.Row():
                            t_phone_edit = gr.Textbox(label="رقم الهاتف (الواتساب)", interactive=False)
                            t_specialty_edit = gr.Dropdown(
                                choices=["فيزياء", "كيمياء", "أحياء", "تقنية المعلومات",
                                         "الفنون التشكيلية", "الرياضة المدرسية",
                                         "المهارات الحياتية", "المهارات الموسيقية"],
                                label="التخصص الدقيق",
                                visible=False,
                                interactive=False,
                                allow_custom_value=True
                            )
                        with gr.Row():
                            t_val = gr.Number(label="رصيد الاحتياط")
                            t_abs_val = gr.Number(label="مرات الغياب")
                            t_short_val = gr.Number(label="حالات التقصير")
                            t_btn = gr.Button("✅ حفظ التعديلات", elem_classes=["admin-btn", "vault-save-btn"])
                            t_del_btn = gr.Button("🗑️ حذف السجل", elem_classes=["reset-btn", "vault-delete-btn"], visible=False)
                        vault_status = gr.HTML()
                            
                with gr.Tab("🛡️ حالات الإعفاء", id="exemptions") as exemptions_tab:
                    gr.Markdown("### 🚫 تثبيت الإعفاءات الدائمة")
                    with gr.Column(elem_classes="shield-box"):
                        rule_teacher = gr.Dropdown(get_teacher_choices("الكل"), label="👨‍🏫 اختر المعلم المراد إعفاؤه")
                        with gr.Row():
                            rule_days = gr.CheckboxGroup(SCHOOL_WEEK_DAYS, label="📅 أيام معفى منها", elem_classes="exemption-rtl-group")
                            rule_periods = gr.CheckboxGroup(list(range(1, MAX_PERIODS + 1)), label="⏱️ حصص معفى منها", elem_classes="exemption-rtl-group exemption-periods-order")
                        rule_save_btn = gr.Button("✅ حفظ قوانين هذا المعلم", elem_classes="admin-btn")
                        rule_status = gr.HTML()
                        exemptions_log_html = gr.HTML(value=render_exemptions_log_html())

                with gr.Tab("🤝 التبادل الودي الأسبوعي", id="swap") as swap_tab:
                    swap_confirmed_state = gr.State({})

                    gr.HTML("""
                    <div class='swap-guide-box'>
                        💡 <b>توضيح:</b><br>
                        ⚫️ التبادل الودي مخصص للاتفاق بين <b>معلمين حاضرين</b>، وليس لمعالجة غياب معلم.<br>
                        ⚫️ اختر <b>اليوم</b>، ثم <b>القسم</b>، ثم اختر <b>المعلم الراغب في التبادل</b>.<br>
                        ⚫️ اختر <b>الحصة المطلوب تبديلها</b>، وسيعرض النظام البدائل المناسبة حسب جدول اليوم.<br>
                        ⚫️ عند اختيار بديل مناسب، ستظهر <b>رسالة واتساب جاهزة</b> للتنسيق.<br>
                        ⚫️ بعد الاتفاق، اضغط <b>اعتماد التبادل</b> ليتم حفظه في جدول التبادلات المعتمدة.<br>
                        ⚫️ بعد اعتماد التبادل، سيظهر في جدول التبادلات المعتمدة، وتظهر خيارات المشاركة أو التصدير حسب صلاحية المستخدم.
                    </div>
                    """)

                    with gr.Row(elem_classes="yellow-box"):
                        swap_day = gr.Dropdown(
                            SCHOOL_WEEK_DAYS,
                            label="1️⃣ اليوم",
                            value=get_current_day_oman()
                        )
                        swap_dept = gr.Dropdown(
                            ["الكل"] + [d for d in OFFICIAL_DEPTS if d != "الهيئة الإدارية"],
                            label="2️⃣ القسم",
                            value="الكل"
                        )
                        swap_t1 = gr.Dropdown(
                            get_teacher_choices("الكل"),
                            label="3️⃣ المعلم الطالب للتبادل",
                            value=None,
                            allow_custom_value=False
                        )
                        swap_p1 = gr.Dropdown([], label="4️⃣ الحصة المراد مبادلتها", allow_custom_value=False)

                    btn_run_radar = gr.Button("🚀 تشغيل الرادار والبحث عن بدائل الآن", variant="primary", visible=False)

                    swap_options = gr.Radio(
                        label="5️⃣ الخيارات المتاحة (اختر المعلم الذي يناسبك لتوليد الرسالة 💬)",
                        choices=[],
                        visible=True,
                        elem_classes="swap-radio-square"
                    )

                    whatsapp_msg = gr.Textbox(
                        label="💬 معاينة رسالة الواتساب التلقائية (يمكنك التعديل عليها)",
                        lines=6,
                        interactive=True,
                        value=SWAP_EMPTY_MSG,
                        visible=True
                    )

                    wa_html_btn = gr.HTML(value="", visible=True)
                    btn_swap_confirm = gr.Button("✅ اعتماد", visible=True, interactive=False, elem_classes="tabadul-btn")

                    confirmed_tbl_html = gr.HTML(value=render_swap_table_html({}))
                    with gr.Row():
                        btn_swap_img = gr.Button("🖼️ إنشاء صورة جدول التبادلات", elem_classes="export-btn")
                    swap_img_out = gr.Image(label="صورة جدول التبادلات المعتمدة", interactive=False)
                    with gr.Column(visible=True) as swap_export_row:
                        btn_swap_excel = gr.Button("📥 تصدير التبادلات المعتمدة Excel", elem_classes="export-btn")
                        swap_excel_out = gr.File(label="ملف Excel للتبادلات المعتمدة", interactive=False)

                with gr.Tab("📅 جدول اليوم", id="day_table") as day_tab:
                    day_page_state = gr.State(value=0)
                    tbl_day = gr.Dataframe(headers=["المعلم"] + [f"ح {p}" for p in range(1, MAX_PERIODS + 1)], interactive=False, visible=True)
                    day_table_html = gr.HTML(visible=False)
                    with gr.Row(visible=False) as day_pagination_row:
                        btn_prev_page = gr.Button("◀ السابق", elem_classes="admin-btn", scale=1, min_width=110)
                        page_info_html = gr.HTML(elem_classes="day-page-info")
                        btn_next_page = gr.Button("التالي ▶", elem_classes="admin-btn", scale=1, min_width=110)
                with gr.Tab("🔍 جدول المعلم", id="teacher_table") as teacher_tab:
                    gr.Markdown("### 🧐 شاشة التدقيق")
                    check_teacher_in = gr.Dropdown(get_teacher_schedule_choices("الكل"), label="👨‍🏫 اختر المعلم")
                    check_tbl = gr.HTML("<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>اختر المعلم لعرض جدوله الأسبوعي.</div>")
                    check_teacher_in.change(get_teacher_weekly_schedule_html, check_teacher_in, check_tbl)
                with gr.Tab("🗄️ مركز البيانات المدرسية", id="school_data") as school_data_tab:
                    gr.HTML("""
<div class="school-data-center-note">
    <b>🗄️ مركز البيانات المدرسية</b><br>
    تظهر حالة التخزين وملف إعدادات المدرسة دائمًا. اضغط بطاقة من البطاقات التالية لفتح بياناتها وأدواتها.
</div>
""")
                    with gr.Row(elem_classes="school-data-nav-row"):
                        btn_school_data_references_panel = gr.Button("🗂️\nالملفات المرجعية", elem_classes=["school-data-nav-btn"])
                        btn_school_data_identity_panel = gr.Button("🎨\nهوية المدرسة", elem_classes=["school-data-nav-btn"])
                        btn_school_data_accounts_panel = gr.Button("🔐\nحسابات الدخول", elem_classes=["school-data-nav-btn"])
                        btn_school_data_audit_panel = gr.Button("🛡️\nالسجل والنسخ", elem_classes=["school-data-nav-btn"])

                    persistent_storage_status_html = gr.HTML(value=render_persistent_storage_status_html())
                    school_config_summary_html = gr.HTML(value=render_school_config_summary_html())


                    school_data_section_status = gr.HTML(
                        value="<div class='school-data-panel-title'>يعرض مركز البيانات الآن حالة التخزين الدائم وملف إعدادات المدرسة فقط. اختر بطاقة من الأعلى لفتح القسم المطلوب.</div>"
                    )

                    with gr.Column(visible=False, elem_classes="school-data-panel-box") as school_data_references_panel:
                        gr.HTML("<div class='school-data-panel-title'>🗂️ الملفات المرجعية والأدوات الإدارية الإضافية</div>")
                        with gr.Row(visible=False):
                            clear_noop = gr.Textbox(label="noop", value="", visible=False)
                            up_dept = gr.Dropdown([], label="noop", visible=False)

                        school_data_admin_html = gr.HTML(value=render_admin_reference_card())
                        with gr.Row():
                            admin_reference_upload = gr.File(label="رفع ملف الإداريين المرجعي", file_types=[".xlsx", ".xls", ".csv"])
                        with gr.Row():
                            save_admin_reference_btn = gr.Button("💾 اعتماد ملف الإداريين المرجعي", elem_classes="admin-btn")
                            refresh_admin_reference_btn = gr.Button("🔄 تحديث الإداريين من الملف المرجعي", elem_classes="admin-btn")
                        admin_reference_status_html = gr.HTML()

                        school_data_phones_html = gr.HTML(value=render_phones_reference_card())
                        with gr.Row():
                            phones_reference_upload = gr.File(label="رفع ملف أرقام المعلمين المرجعي", file_types=[".xlsx", ".xls", ".csv"])
                        with gr.Row():
                            save_phones_reference_btn = gr.Button("💾 اعتماد ملف أرقام المعلمين المرجعي", elem_classes="admin-btn")
                            refresh_phones_reference_btn = gr.Button("🔄 تحديث أرقام المعلمين من الملف المرجعي", elem_classes="admin-btn")
                        phones_reference_status_html = gr.HTML()

                        school_data_schedules_html = gr.HTML(value=render_schedule_reference_cards())
                        with gr.Row():
                            schedule_reference_dept = gr.Dropdown(
                                choices=list(SCHEDULE_FILES.keys()),
                                label="اختر القسم لملفه المرجعي",
                                value="التربية الإسلامية"
                            )
                        with gr.Row():
                            schedule_reference_upload = gr.File(
                                label="رفع ملف الجدول المرجعي للقسم المختار",
                                file_types=[".xlsx", ".xls", ".csv"]
                            )
                        with gr.Row():
                            save_schedule_reference_btn = gr.Button("💾 اعتماد الملف المرجعي للقسم", elem_classes="admin-btn")
                            refresh_schedule_reference_btn = gr.Button("🔄 تحديث القسم من الملف المرجعي", elem_classes="admin-btn")
                        schedule_reference_status_html = gr.HTML()


                        with gr.Accordion("🧩 أدوات إدارية إضافية", open=True, visible=False, elem_classes=["direct-panel-accordion", "manual-tools-direct-panel"]) as manual_entry_container:
                            gr.Markdown("### 👨‍💼 الإدخال اليدوي للطاقم الإداري")
                            with gr.Row(elem_classes="yellow-box"):
                                manual_name = gr.Textbox(label="الاسم الثلاثي")
                                manual_dept = gr.Dropdown(["الهيئة الإدارية"], label="القسم", value="الهيئة الإدارية", interactive=False, elem_classes="fixed-dd")
                                manual_role = gr.Dropdown(ADMIN_ROLES, label="المنصب", value="أخصائي اجتماعي", elem_classes="fixed-dd")
                                manual_phone = gr.Textbox(label="رقم الواتساب")
                            with gr.Row():
                                manual_add_btn = gr.Button("➕ حفظ وإضافة", elem_classes="admin-btn")
                        manual_status_html = gr.HTML()
                        clear_status_html = gr.HTML()
                        clear_btn = gr.Button("🧨 مسح وتصفير المنظومة", elem_classes="reset-btn", visible=False)


                    with gr.Column(visible=False, elem_classes="school-data-panel-box") as school_data_identity_panel:
                        gr.HTML("<div class='school-data-panel-title'>🎨 هوية المدرسة — تظهر مباشرة بعد الضغط على البطاقة</div>")
                        with gr.Accordion("🎨 إعدادات هوية المدرسة", open=True, elem_classes=["direct-panel-accordion", "identity-direct-panel"]):
                            gr.HTML("<div style='background:#eef6f3;color:#004d40;padding:12px;border-radius:10px;border-right:5px solid #0f766e;margin-bottom:12px;font-weight:800;line-height:1.8;'>هذه اللوحة مخصصة لمالك النظام. يمكن تعديل الهوية البصرية دون تعديل app.py. العناوين والشعار تتحدث فورًا، أما الألوان العامة فتُطبق بالكامل بعد إعادة تشغيل التطبيق.</div>")

                            gr.HTML(
                                "<div style='background:#f8fafc;color:#334155;padding:12px;"
                                "border-radius:10px;border-right:5px solid #64748b;"
                                "margin-bottom:12px;font-weight:800;line-height:1.9;text-align:right;'>"
                                "العناصر الثابتة في الهوية: <b>وزارة التعليم</b>، "
                                "<b>منظومة مسار</b>، <b>للاحتياط والتبادل الودي</b>، "
                                "وعبارة <b>فكرة وتطوير</b>. يمكن تعديل اسم المدرسة، المحافظة، الشعار، والألوان فقط."
                                "</div>"
                            )
                            identity_system_name = gr.Textbox(value=SYSTEM_NAME, label="اسم المنظومة", visible=False)
                            identity_system_subtitle = gr.Textbox(value=SYSTEM_SUBTITLE, label="العنوان الفرعي", visible=False)
                            identity_developer_credit = gr.Textbox(value=DEVELOPER_CREDIT, label="عبارة الحقوق والتطوير", visible=False)
                            with gr.Row():
                                identity_school_name = gr.Textbox(value=SCHOOL_NAME, label="اسم المدرسة")
                                identity_directorate_region = gr.Textbox(value=DIRECTORATE_REGION, label="المحافظة في سطر المديرية", placeholder="مثال: جنوب الباطنة")
                            with gr.Row():
                                identity_logo_url = gr.Textbox(value=SCHOOL_LOGO_URL, label="رابط الشعار أو مساره المحلي")
                                identity_logo_upload = gr.File(
                                    label="رفع شعار بديل اختياري",
                                    file_types=[".png", ".jpg", ".jpeg", ".webp"],
                                    type="filepath",
                                )
                            with gr.Row():
                                identity_theme_color = gr.Textbox(value=THEME_COLOR, label="اللون الأساسي HEX")
                                identity_theme_color_2 = gr.Textbox(value=THEME_COLOR_2, label="اللون الثانوي HEX")
                                identity_accent_color = gr.Textbox(value=ACCENT_COLOR, label="اللون البارز HEX")

                            with gr.Row():
                                identity_preview_btn = gr.Button("معاينة هوية المدرسة", elem_classes="admin-btn")
                                identity_save_btn = gr.Button("حفظ هوية المدرسة", elem_classes="admin-btn")
                                identity_reset_btn = gr.Button("استعادة الهوية الافتراضية", elem_classes="reset-btn")

                            identity_status_html = gr.HTML()
                            gr.HTML(
                                "<div style='background:#fff7ed;color:#9a3412;padding:10px;"
                                "border-radius:10px;border-right:5px solid #f59e0b;"
                                "margin:12px 0;font-weight:900;line-height:1.8;text-align:right;'>"
                                "تنبيه: هذه معاينة هوية المدرسة داخل مركز البيانات، وليست الهيدر الفعلي بعد تسجيل الدخول."
                                "</div>"
                            )
                            gr.HTML(
                                "<div style='font-weight:900;color:#004d40;margin:8px 0 6px;text-align:right;'>"
                                "🖼️ معاينة هوية المدرسة"
                                "</div>"
                            )
                            identity_preview_html = gr.HTML(
                                value=render_school_identity_preview_html(
                                    SYSTEM_NAME,
                                    SYSTEM_SUBTITLE,
                                    SCHOOL_NAME,
                                    DEVELOPER_CREDIT,
                                    SCHOOL_LOGO_URL,
                                    THEME_COLOR,
                                    THEME_COLOR_2,
                                    ACCENT_COLOR,
                                    DIRECTORATE_REGION,
                                )
                            )



                    with gr.Column(visible=False, elem_classes="school-data-panel-box") as school_data_accounts_panel:
                        gr.HTML("<div class='school-data-panel-title'>🔐 حسابات الدخول والترحيب — أدوات الرمز والتخصيص في صفحة واحدة</div>")
                        with gr.Accordion("🔐 إدارة حسابات الدخول", open=True, elem_classes=["direct-panel-accordion", "accounts-direct-panel"]):
                            gr.HTML(
                                "<div style='background:#eef6f3;color:#004d40;padding:12px;"
                                "border-radius:10px;border-right:5px solid #0f766e;"
                                "margin-bottom:12px;font-weight:800;line-height:1.8;'>"
                                "لوحة المالك لإعادة تعيين الرموز وتعطيل الحسابات. "
                                "لا تعرض المنظومة أي رمز قديم. الرمز الجديد يظهر مرة واحدة فقط بعد إعادة التعيين."
                                "</div>"
                            )
                            owner_accounts_html = gr.HTML(
                                value=render_auth_accounts_html(False)
                            )
                            with gr.Row():
                                owner_account_selector = gr.Dropdown(
                                    choices=[],
                                    value=None,
                                    label="اختر الحساب",
                                )
                                owner_requested_pin = gr.Textbox(
                                    type="password",
                                    label="رمز جديد اختياري",
                                    placeholder="اتركه فارغًا لتوليد رمز من 6 أرقام",
                                )
                            with gr.Row():
                                owner_reset_pin_btn = gr.Button(
                                    "إعادة تعيين رمز الحساب",
                                    elem_classes="admin-btn",
                                )
                                owner_toggle_account_btn = gr.Button(
                                    "تفعيل / تعطيل الحساب",
                                    elem_classes="reset-btn",
                                )
                                owner_refresh_accounts_btn = gr.Button(
                                    "تحديث قائمة الحسابات",
                                    elem_classes="admin-btn",
                                )
                            owner_one_time_pin = gr.Textbox(
                                label="الرمز الجديد لمرة واحدة",
                                interactive=False,
                                value="",
                            )

                            with gr.Accordion("✨ تخصيص الترحيب والمسميات", open=True, elem_classes=["direct-panel-accordion", "account-profile-direct-panel"]):
                                gr.HTML(
                                    "<div style='background:#fff7ed;color:#7c2d12;padding:10px;"
                                    "border-radius:8px;border-right:4px solid #f59e0b;"
                                    "font-weight:800;line-height:1.8;'>"
                                    "اختر حسابًا من الأعلى، ثم اضبط اسم العرض واللقب الجمالي وعبارة الهيدر. "
                                    "يمكن استخدام المتغيرات: {display_name}، {welcome_title}، {department_label}، {official_title}، {whatsapp_title}، {school_name}."
                                    "</div>"
                                )
                                with gr.Row():
                                    owner_profile_display_name = gr.Textbox(
                                        label="اسم العرض",
                                        placeholder="مثال: أ. سعود المعولي",
                                    )
                                    owner_profile_official_title = gr.Textbox(
                                        label="المسمى الرسمي",
                                        placeholder="مثال: منسق مادة اللغة العربية",
                                    )
                                    owner_profile_whatsapp_title = gr.Textbox(
                                        label="مسمى واتساب",
                                        placeholder="مثال: منسق اللغة العربية",
                                    )
                                with gr.Row():
                                    owner_profile_welcome_title = gr.Textbox(
                                        label="اللقب الجمالي",
                                        placeholder="مثال: مايسترو البيان",
                                    )
                                    owner_profile_department_label = gr.Textbox(
                                        label="القسم الظاهر",
                                        placeholder="مثال: قسم اللغة العربية",
                                    )
                                owner_profile_welcome_phrase = gr.Textbox(
                                    label="عبارة الترحيب",
                                    placeholder="مثال: نورتنا، وقسم اللغة العربية جاهز لك",
                                )
                                owner_profile_welcome_template = gr.Textbox(
                                    label="قالب الهيدر",
                                    value=ACCOUNT_WELCOME_DEFAULT_TEMPLATE,
                                    placeholder="مثال: {welcome_title} ({display_name}) {welcome_phrase}",
                                )
                                with gr.Row():
                                    owner_profile_preview_btn = gr.Button(
                                        "معاينة الترحيب",
                                        elem_classes="admin-btn",
                                    )
                                    owner_profile_save_btn = gr.Button(
                                        "حفظ تخصيص الحساب",
                                        elem_classes="admin-btn",
                                    )
                                owner_profile_preview_html = gr.HTML()

                            owner_accounts_status = gr.HTML()


                    with gr.Column(visible=False, elem_classes="school-data-panel-box") as school_data_audit_panel:
                        gr.HTML("<div class='school-data-panel-title'>🛡️ السجل والنسخ — عرض مباشر دون ضغط إضافي</div>")
                        with gr.Accordion("🛡️ سجل العمليات والنسخ الاحتياطية", open=True, elem_classes=["direct-panel-accordion", "audit-direct-panel"]):
                            gr.HTML("<div style='background:#eef6f3;color:#004d40;padding:12px;border-radius:10px;border-right:5px solid #0f766e;margin-bottom:12px;font-weight:800;line-height:1.8;'>هذه أدوات رقابية لمالك النظام فقط. سجل العمليات لا ينفذ أي تعديل؛ بل يوضح من غيّر ماذا، وعلى أي معلم، وما القيمة القديمة والجديدة، ومتى حدث ذلك. اختر الفلاتر للعرض، ثم صدّر النتائج المطابقة إلى Excel عند الحاجة.</div>")

                            with gr.Accordion("📑 سجل العمليات الحساسة", open=True):
                                with gr.Row():
                                    audit_action_filter = gr.Dropdown(["الكل"], value="الكل", label="نوع العملية")
                                    audit_actor_filter = gr.Dropdown(["الكل"], value="الكل", label="اسم المنفذ")
                                    audit_teacher_filter = gr.Dropdown(["الكل"], value="الكل", label="المعلم المتأثر")
                                with gr.Row():
                                    audit_date_from = gr.DateTime(
                                        label="من تاريخ",
                                        include_time=False,
                                        type="string",
                                        timezone="Asia/Muscat",
                                        info="اختر تاريخ البداية من التقويم",
                                    )
                                    audit_date_to = gr.DateTime(
                                        label="إلى تاريخ",
                                        include_time=False,
                                        type="string",
                                        timezone="Asia/Muscat",
                                        info="اختر تاريخ النهاية من التقويم",
                                    )
                                with gr.Row():
                                    audit_today_btn = gr.Button("اليوم", elem_classes="admin-btn")
                                    audit_last_7_days_btn = gr.Button("آخر 7 أيام", elem_classes="admin-btn")
                                    audit_this_month_btn = gr.Button("هذا الشهر", elem_classes="admin-btn")
                                    audit_clear_dates_btn = gr.Button("مسح التاريخ", elem_classes="reset-btn")
                                with gr.Row():
                                    audit_refresh_btn = gr.Button("عرض النتائج حسب الفلاتر", elem_classes="admin-btn")
                                    audit_export_btn = gr.Button("تصدير السجل إلى Excel", elem_classes="export-btn")
                                audit_action_status_html = gr.HTML()
                                audit_table_html = gr.HTML("<div style='text-align:center;color:#64748b;padding:16px;'>افتح القسم أو اضغط تحديث لعرض سجل العمليات.</div>")
                                audit_export_file = gr.File(label="ملف سجل العمليات", interactive=False)

                            with gr.Accordion("💾 حالة النسخ الاحتياطية", open=True):
                                backup_status_html = gr.HTML("<div style='text-align:center;color:#64748b;padding:16px;'>اضغط تحديث لعرض حالة النسخ الاحتياطية.</div>")
                                with gr.Row():
                                    backup_refresh_btn = gr.Button("تحديث حالة النسخ الاحتياطية", elem_classes="admin-btn")
                                    backup_zip_btn = gr.Button("تحميل نسخة احتياطية كاملة ZIP", elem_classes="export-btn")
                                backup_action_status_html = gr.HTML()
                                backup_zip_file = gr.File(label="الحزمة الاحتياطية", interactive=False)

    # ── ربط الأحداث ──────────────────────────────────────────────
    update_outputs = [
        abs_in, tbl_bal, tbl_abs, tbl_short, tbl_day, day_table_html, day_pagination_row, btn_prev_page, btn_next_page, page_info_html, day_page_state, t_name, check_teacher_in, rule_teacher, 
        radar_warning_html, tbl_out, edit_abs_t, current_schedule_state, 
        msg_summary, msg_individual_html, date_display, admin_zone_title,
        admin_zone_help, edit_period, cb_cross_dept, btn_alt, btn_img
    ]
    app.load(sync_current_school_days, None, [day_in, swap_day])

    btn_school_data_references_panel.click(
        lambda: show_school_data_panel("references"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    )
    btn_school_data_identity_panel.click(
        lambda: show_school_data_panel("identity"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    )
    btn_school_data_accounts_panel.click(
        lambda: show_school_data_panel("accounts"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    )
    btn_school_data_audit_panel.click(
        lambda: show_school_data_panel("audit"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    )

    btn_open_distribution.click(lambda: open_home_section("distribution"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_balances.click(lambda: open_home_section("balances"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_exemptions.click(lambda: open_home_section("exemptions"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_swap.click(lambda: open_home_section("swap"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_day.click(lambda: open_home_section("day_table"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_teacher.click(lambda: open_home_section("teacher_table"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_school_data.click(
        lambda: open_home_section("school_data"),
        [],
        [home_dashboard, tabs_container, main_tabs],
        queue=False,
    ).then(
        lambda: show_school_data_panel("overview"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    ).then(
        refresh_school_data_center_cards,
        [current_user_is_owner],
        [
            persistent_storage_status_html,
            school_config_summary_html,
            school_data_admin_html,
            school_data_phones_html,
            school_data_schedules_html,
        ],
        queue=False,
    ).then(
        refresh_owner_accounts_panel,
        [current_user_is_owner],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_one_time_pin,
            owner_accounts_status,
        ],
        queue=False,
    ).then(
        None, None, None,
        js=show_selected_tab_container_js(),
    )
    btn_back_home.click(return_to_home_dashboard, [], [home_dashboard, tabs_container], queue=False).then(None, None, None, js=return_home_dashboard_js())
    login_btn.click(
        attempt_login,
        inputs=[pin_input, day_in],
        outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role, current_user_account_id] + update_outputs + [t_specialty_edit, clear_btn, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row]
    ).then(
        show_home_dashboard_after_login,
        [dept_in, current_user_is_admin, current_user_is_owner, current_user_role],
        [home_dashboard, tabs_container, card_distribution, card_balances, card_exemptions, card_swap, card_day, card_teacher, card_school_data],
        queue=False
    ).then(
        update_home_hero_after_login,
        [
            current_user_account_id,
            current_user_name,
            current_user_role,
            current_user_is_owner,
        ],
        [home_hero_html],
        queue=False,
    ).then(
        None, None, None,
        js=show_home_dashboard_js()
    ).then(
        refresh_ui_on_change,
        [dept_in, day_in, current_user_is_admin],
        update_outputs
    ).then(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    ).then(
        lambda: gr.update(value=render_exemptions_log_html()),
        [],
        [exemptions_log_html],
        queue=False
    ).then(
        lambda adm, own: gr.update(visible=bool(adm or own)),
        [current_user_is_admin, current_user_is_owner],
        [reset_month_btn],
        queue=False
    )
    pin_input.submit(
        attempt_login,
        inputs=[pin_input, day_in],
        outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role, current_user_account_id] + update_outputs + [t_specialty_edit, clear_btn, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row]
    ).then(
        show_home_dashboard_after_login,
        [dept_in, current_user_is_admin, current_user_is_owner, current_user_role],
        [home_dashboard, tabs_container, card_distribution, card_balances, card_exemptions, card_swap, card_day, card_teacher, card_school_data],
        queue=False
    ).then(
        update_home_hero_after_login,
        [
            current_user_account_id,
            current_user_name,
            current_user_role,
            current_user_is_owner,
        ],
        [home_hero_html],
        queue=False,
    ).then(
        None, None, None,
        js=show_home_dashboard_js()
    ).then(
        refresh_ui_on_change,
        [dept_in, day_in, current_user_is_admin],
        update_outputs
    ).then(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    ).then(
        lambda: gr.update(value=render_exemptions_log_html()),
        [],
        [exemptions_log_html],
        queue=False
    ).then(
        lambda adm, own: gr.update(visible=bool(adm or own)),
        [current_user_is_admin, current_user_is_owner],
        [reset_month_btn],
        queue=False
    )
    logout_btn.click(do_logout, inputs=[], outputs=[login_container, main_app_container, welcome_html, dept_in, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role, current_user_account_id, current_schedule_state, img_out, cb_cross_dept, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row, reserve_generation_state, swap_confirmed_state]).then(
        None, None, None,
        js="""() => {
            const style = document.createElement('style');
            style.textContent = '.toast-wrap, .toast-body { display: none !important; }';
            document.head.appendChild(style);
            setTimeout(() => { window.location.reload(); }, 200);
        }"""
    )
    
    update_trigger = [dept_in, day_in, current_user_is_admin]
    dept_in.change(
    lambda d, dy, adm: refresh_ui_on_change(d, dy, adm) + (gr.update(visible=d in ["العلوم", "المهارات الفردية"]),),
    update_trigger,
    update_outputs + [t_specialty_edit]
    )
    day_in.change(lambda d, dy, adm: refresh_ui_on_change(d, dy, adm), update_trigger, update_outputs)
    btn_prev_page.click(
        lambda dy, dp, pg: change_day_page(-1, dy, dp, pg),
        [day_in, dept_in, day_page_state],
        [tbl_day, day_table_html, day_pagination_row, btn_prev_page, btn_next_page, page_info_html, day_page_state],
        queue=False
    )
    btn_next_page.click(
        lambda dy, dp, pg: change_day_page(1, dy, dp, pg),
        [day_in, dept_in, day_page_state],
        [tbl_day, day_table_html, day_pagination_row, btn_prev_page, btn_next_page, page_info_html, day_page_state],
        queue=False
    )
    dept_in.change(clear_generated_image, None, [img_out], queue=False)
    day_in.change(clear_generated_image, None, [img_out], queue=False)
    refresh_btn.click(
        force_refresh_data,
        [dept_in, day_in, current_user_is_admin, abs_in],
        update_outputs
    ).then(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    )
    refresh_btn.click(sync_current_school_days, None, [day_in, swap_day])
    btn_img.click(
        generate_image_only,
        [dept_in, day_in],
        [img_out],
        queue=False
    )
    refresh_btn.click(clear_generated_image, None, [img_out], queue=False)
    btn.click(clear_generated_image, None, [img_out], queue=False)
    btn_regenerate.click(clear_generated_image, None, [img_out], queue=False)
    btn_alt.click(clear_generated_image, None, [img_out], queue=False)
    btn_apply_override.click(clear_generated_image, None, [img_out], queue=False)
    btn_apply_tabadul.click(clear_generated_image, None, [img_out], queue=False)
    btn_apply_penalty.click(clear_generated_image, None, [img_out], queue=False)
    btn_cancel_absence.click(clear_generated_image, None, [img_out], queue=False)
    
    manual_add_btn.click(add_manual_staff, [manual_name, manual_dept, manual_phone, manual_role, dept_in, current_user_is_owner], [manual_status_html, abs_in, check_teacher_in, rule_teacher, t_name, manual_name, manual_phone])
    save_admin_reference_btn.click(
        save_admin_reference_file,
        [admin_reference_upload, current_user_is_owner],
        [admin_reference_status_html, school_data_admin_html]
    )
    refresh_admin_reference_btn.click(
        refresh_admins_from_reference,
        [dept_in, current_user_is_owner],
        [admin_reference_status_html, abs_in, check_teacher_in, rule_teacher, t_name, tbl_bal, school_data_admin_html, admin_reference_upload]
    )
    save_phones_reference_btn.click(
        save_phones_reference_file,
        [phones_reference_upload, current_user_is_owner],
        [phones_reference_status_html, school_data_phones_html]
    )
    refresh_phones_reference_btn.click(
        refresh_phones_from_reference,
        [dept_in, current_user_is_owner],
        [phones_reference_status_html, tbl_bal, school_data_phones_html, phones_reference_upload]
    )
    save_schedule_reference_btn.click(
        save_schedule_reference_file,
        [schedule_reference_upload, schedule_reference_dept, current_user_is_owner],
        [schedule_reference_status_html, school_data_schedules_html]
    )
    refresh_schedule_reference_btn.click(
        refresh_schedule_from_reference,
        [schedule_reference_dept, day_in, current_user_is_owner],
        [schedule_reference_status_html, abs_in, check_teacher_in, rule_teacher, tbl_bal, tbl_abs, tbl_day, school_data_schedules_html, schedule_reference_upload]
    )

    self_change_pin_btn.click(
        change_own_account_pin,
        [
            current_user_account_id,
            self_current_pin,
            self_new_pin,
            self_confirm_pin,
            current_user_name,
            current_user_role,
            current_user_is_owner,
        ],
        [
            self_current_pin,
            self_new_pin,
            self_confirm_pin,
            self_change_pin_status,
        ],
        queue=False,
    )

    owner_refresh_accounts_btn.click(
        refresh_owner_accounts_panel,
        [current_user_is_owner],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_one_time_pin,
            owner_accounts_status,
        ],
        queue=False,
    )

    owner_reset_pin_btn.click(
        owner_reset_account_pin,
        [
            owner_account_selector,
            owner_requested_pin,
            current_user_is_owner,
            current_user_name,
            current_user_role,
        ],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_requested_pin,
            owner_one_time_pin,
            owner_accounts_status,
        ],
        queue=False,
    )

    owner_toggle_account_btn.click(
        owner_toggle_account_status,
        [
            owner_account_selector,
            current_user_is_owner,
            current_user_name,
            current_user_role,
        ],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_one_time_pin,
            owner_accounts_status,
        ],
        queue=False,
    )

    owner_account_selector.change(
        load_auth_account_profile_for_editor,
        [owner_account_selector, current_user_is_owner],
        [
            owner_profile_display_name,
            owner_profile_official_title,
            owner_profile_welcome_title,
            owner_profile_department_label,
            owner_profile_welcome_phrase,
            owner_profile_welcome_template,
            owner_profile_whatsapp_title,
            owner_profile_preview_html,
            owner_accounts_status,
        ],
        queue=False,
    )

    owner_profile_preview_btn.click(
        preview_account_profile_settings,
        [
            owner_profile_display_name,
            owner_profile_official_title,
            owner_profile_welcome_title,
            owner_profile_department_label,
            owner_profile_welcome_phrase,
            owner_profile_welcome_template,
            owner_profile_whatsapp_title,
            current_user_is_owner,
        ],
        [owner_profile_preview_html, owner_accounts_status],
        queue=False,
    )

    owner_profile_save_btn.click(
        save_auth_account_profile,
        [
            owner_account_selector,
            owner_profile_display_name,
            owner_profile_official_title,
            owner_profile_welcome_title,
            owner_profile_department_label,
            owner_profile_welcome_phrase,
            owner_profile_welcome_template,
            owner_profile_whatsapp_title,
            current_user_is_owner,
            current_user_name,
            current_user_role,
        ],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_profile_preview_html,
            owner_accounts_status,
        ],
        queue=False,
    )

    identity_preview_btn.click(
        preview_school_identity_settings,
        [
            identity_system_name,
            identity_system_subtitle,
            identity_school_name,
            identity_developer_credit,
            identity_directorate_region,
            identity_logo_url,
            identity_theme_color,
            identity_theme_color_2,
            identity_accent_color,
            current_user_is_owner,
        ],
        [identity_preview_html, identity_status_html],
        queue=False,
    )

    identity_save_btn.click(
        save_school_identity_settings,
        [
            identity_system_name,
            identity_system_subtitle,
            identity_school_name,
            identity_developer_credit,
            identity_directorate_region,
            identity_logo_url,
            identity_logo_upload,
            identity_theme_color,
            identity_theme_color_2,
            identity_accent_color,
            current_user_is_owner,
        ],
        [
            identity_system_name,
            identity_system_subtitle,
            identity_school_name,
            identity_directorate_region,
            identity_developer_credit,
            identity_logo_url,
            identity_logo_upload,
            identity_theme_color,
            identity_theme_color_2,
            identity_accent_color,
            identity_status_html,
            identity_preview_html,
            login_branding_html,
            login_credits_html,
            header_branding_html,
            home_hero_html,
            school_config_summary_html,
        ],
        queue=False,
    )

    identity_reset_btn.click(
        reset_school_identity_settings,
        [current_user_is_owner],
        [
            identity_system_name,
            identity_system_subtitle,
            identity_school_name,
            identity_directorate_region,
            identity_developer_credit,
            identity_logo_url,
            identity_logo_upload,
            identity_theme_color,
            identity_theme_color_2,
            identity_accent_color,
            identity_status_html,
            identity_preview_html,
            login_branding_html,
            login_credits_html,
            header_branding_html,
            home_hero_html,
            school_config_summary_html,
        ],
        queue=False,
    )

    school_data_tab.select(
        lambda: show_school_data_panel("overview"),
        [],
        [school_data_section_status, persistent_storage_status_html, school_config_summary_html, school_data_references_panel, school_data_identity_panel, school_data_accounts_panel, school_data_audit_panel],
        queue=False,
    )
    school_data_tab.select(
        refresh_owner_tools_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html, backup_status_html],
        queue=False
    )
    school_data_tab.select(
        refresh_school_data_center_cards,
        [current_user_is_owner],
        [
            persistent_storage_status_html,
            school_config_summary_html,
            school_data_admin_html,
            school_data_phones_html,
            school_data_schedules_html,
        ],
        queue=False,
    )
    school_data_tab.select(
        refresh_owner_accounts_panel,
        [current_user_is_owner],
        [
            owner_accounts_html,
            owner_account_selector,
            owner_one_time_pin,
            owner_accounts_status,
        ],
        queue=False,
    )
    audit_today_btn.click(
        lambda: get_audit_date_range("today"),
        [],
        [audit_date_from, audit_date_to],
        queue=False,
    ).then(
        refresh_audit_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html],
        queue=False,
    ).then(
        lambda: (gr.update(value=None), gr.update(value="")),
        [],
        [audit_export_file, audit_action_status_html],
        queue=False,
    )

    audit_last_7_days_btn.click(
        lambda: get_audit_date_range("last_7_days"),
        [],
        [audit_date_from, audit_date_to],
        queue=False,
    ).then(
        refresh_audit_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html],
        queue=False,
    ).then(
        lambda: (gr.update(value=None), gr.update(value="")),
        [],
        [audit_export_file, audit_action_status_html],
        queue=False,
    )

    audit_this_month_btn.click(
        lambda: get_audit_date_range("this_month"),
        [],
        [audit_date_from, audit_date_to],
        queue=False,
    ).then(
        refresh_audit_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html],
        queue=False,
    ).then(
        lambda: (gr.update(value=None), gr.update(value="")),
        [],
        [audit_export_file, audit_action_status_html],
        queue=False,
    )

    audit_clear_dates_btn.click(
        lambda: get_audit_date_range("clear"),
        [],
        [audit_date_from, audit_date_to],
        queue=False,
    ).then(
        refresh_audit_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html],
        queue=False,
    ).then(
        lambda: (gr.update(value=None), gr.update(value="")),
        [],
        [audit_export_file, audit_action_status_html],
        queue=False,
    )

    audit_refresh_btn.click(
        refresh_audit_dashboard,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_table_html],
        queue=False
    ).then(
        lambda: (gr.update(value=None), gr.update(value="")),
        [],
        [audit_export_file, audit_action_status_html],
        queue=False,
    )
    audit_export_btn.click(
        export_audit_log_excel,
        [audit_action_filter, audit_actor_filter, audit_teacher_filter, audit_date_from, audit_date_to, current_user_is_owner],
        [audit_export_file, audit_action_status_html],
        queue=False
    )
    backup_refresh_btn.click(
        refresh_backup_status,
        [current_user_is_owner],
        [backup_status_html],
        queue=False
    )
    backup_zip_btn.click(
        create_backup_bundle,
        [current_user_is_owner],
        [backup_zip_file, backup_action_status_html],
        queue=False
    )
    clear_btn.click(
        clear_all_data,
        [current_user_is_owner],
        [
            dept_in,
            abs_in,
            check_teacher_in,
            rule_teacher,
            tbl_bal,
            tbl_abs,
            tbl_short,
            tbl_day,
            day_table_html,
            day_pagination_row,
            btn_prev_page,
            btn_next_page,
            page_info_html,
            day_page_state,
            clear_status_html,
            t_name,
            clear_noop,
            tbl_out,
            img_out,
            current_schedule_state,
            reserve_generation_state,
            msg_summary,
            msg_individual_html,
            date_display,
            radar_warning_html,
            edit_abs_t,
            edit_period,
            btn,
            btn_regenerate,
            btn_alt,
            btn_img,
        ]
    )
    
    rule_teacher.change(
        lambda selected_teacher: load_teacher_rules(selected_teacher) + (gr.update(value=""), gr.update(value=render_exemptions_log_html())),
        rule_teacher,
        [rule_days, rule_periods, rule_status, exemptions_log_html]
    )
    rule_save_btn.click(save_teacher_rules, [rule_teacher, rule_days, rule_periods, current_user_name, current_user_role, current_user_is_admin, current_user_is_owner], [rule_status, exemptions_log_html])
    exemptions_tab.select(
        lambda: gr.update(value=render_exemptions_log_html()),
        [],
        [exemptions_log_html],
        queue=False
    )
    
    btn.click(
        run_main_generation,
        [abs_in, day_in, dept_in, max_reserves_input, current_user_is_admin, reserve_generation_state, current_user_name, current_user_role],
        update_outputs + [btn, btn_regenerate, reserve_generation_state]
    )
    btn_regenerate.click(
        run_full_regeneration,
        [abs_in, day_in, dept_in, max_reserves_input, current_user_is_admin, reserve_generation_state, current_user_name, current_user_role],
        update_outputs + [btn, btn_regenerate, reserve_generation_state]
    )
    btn_alt.click(lambda a, d, dp, mr, adm, an, ar: assign_logic(a, d, dp, mr, True, adm, an, ar), [abs_in, day_in, dept_in, max_reserves_input, current_user_is_admin, current_user_name, current_user_role], update_outputs)
    abs_in.change(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    )

    day_in.change(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    )

    dept_in.change(
        get_generation_button_updates,
        [abs_in, day_in, dept_in, reserve_generation_state],
        [btn, btn_regenerate]
    )
    edit_abs_t.change(on_abs_t_change, [current_schedule_state, edit_abs_t, current_user_is_admin], [edit_period, edit_intervention_type, cb_cross_dept]).then(
        lambda: gr.update(choices=[], value=None),
        None,
        [edit_new_sub],
        queue=False
    ).then(
        lambda at: get_leader_action_button_updates(at, None, None),
        [edit_abs_t],
        [btn_apply_override, btn_apply_tabadul, btn_apply_penalty, btn_cancel_absence],
        queue=False
    )
    cb_cross_dept.change(toggle_cross_dept, [cb_cross_dept, edit_abs_t], [edit_intervention_type])
    edit_period.change(update_available_subs_smart, [edit_abs_t, edit_period, edit_intervention_type, day_in, current_schedule_state, current_user_is_admin], [edit_new_sub]).then(
        get_leader_action_button_updates,
        [edit_abs_t, edit_period, edit_new_sub],
        [btn_apply_override, btn_apply_tabadul, btn_apply_penalty, btn_cancel_absence],
        queue=False
    )
    edit_intervention_type.change(update_available_subs_smart, [edit_abs_t, edit_period, edit_intervention_type, day_in, current_schedule_state, current_user_is_admin], [edit_new_sub]).then(
        get_leader_action_button_updates,
        [edit_abs_t, edit_period, edit_new_sub],
        [btn_apply_override, btn_apply_tabadul, btn_apply_penalty, btn_cancel_absence],
        queue=False
    )
    edit_new_sub.change(
        get_leader_action_button_updates,
        [edit_abs_t, edit_period, edit_new_sub],
        [btn_apply_override, btn_apply_tabadul, btn_apply_penalty, btn_cancel_absence],
        queue=False
    )
    btn_apply_override.click(lambda dfs, at, p, ns, dn, dpt, adm, ca, an, ar: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "normal", an, ar), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in, current_user_name, current_user_role], update_outputs)
    btn_apply_tabadul.click(lambda dfs, at, p, ns, dn, dpt, adm, ca, an, ar: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "tabadul", an, ar), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in, current_user_name, current_user_role], update_outputs)
    btn_apply_penalty.click(lambda dfs, at, p, ns, dn, dpt, adm, ca, an, ar: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "penalty", an, ar), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in, current_user_name, current_user_role], update_outputs)
    btn_cancel_absence.click(
        cancel_teacher_absence_with_generation_state,
        [edit_abs_t, day_in, dept_in, current_user_is_admin, abs_in, current_user_name, current_user_role],
        update_outputs + [btn, btn_regenerate, reserve_generation_state]
    )
    # ── أحداث الخزنة والتقارير والتبادل ─────────────────────────
    t_name.change(
        lambda selected_teacher, is_admin, is_owner: load_teacher_data_for_edit(selected_teacher, is_admin, is_owner) + (gr.update(value=""),),
        [t_name, current_user_is_admin, current_user_is_owner],
        [t_dept_edit, t_val, t_abs_val, t_short_val, t_phone_edit, t_specialty_edit, t_role_edit, vault_status]
    )
    t_dept_edit.change(
    lambda td, d, own: gr.update(
        visible=td in ["العلوم", "المهارات الفردية"] or d in ["العلوم", "المهارات الفردية"],
        interactive=bool(own)
    ),
    [t_dept_edit, dept_in, current_user_is_owner],
    t_specialty_edit
    )
    t_btn.click(update_manual_count, [t_name, t_val, t_abs_val, t_short_val, t_phone_edit, t_specialty_edit, t_role_edit, dept_in, day_in, current_schedule_state, abs_in, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role], [tbl_bal, tbl_abs, tbl_short, tbl_day, vault_status, abs_in, check_teacher_in, rule_teacher])
    t_del_btn.click(delete_single_teacher, [t_name, dept_in, day_in, current_user_is_owner], [tbl_bal, tbl_abs, tbl_short, tbl_day, vault_status, abs_in, check_teacher_in, rule_teacher, t_name])
    export_btn.click(export_excel_report, [dept_in], [report_file])
    reset_month_btn.click(reset_monthly_balances, [dept_in, day_in, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role], [tbl_bal, tbl_abs, tbl_short, tbl_day, monthly_status])
    
    swap_dept.change(
        filter_swap_teachers_safe,
        [swap_dept],
        [swap_t1],
        queue=False
    ).then(
        lambda: gr.update(choices=[], value=None),
        None,
        [swap_p1],
        queue=False
    ).then(
        lambda: ({}, gr.update(value=render_swap_table_html({}))),
        None,
        [swap_confirmed_state, confirmed_tbl_html],
        queue=False
    ).then(
        clear_swap_detail_ui,
        None,
        [swap_options, whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )

    swap_day.change(
        load_confirmed_swaps_for_context,
        [swap_t1, swap_day],
        [swap_confirmed_state, confirmed_tbl_html],
        queue=False
    ).then(
        get_teacher_periods_marked,
        [swap_t1, swap_day, swap_confirmed_state, swap_p1],
        [swap_p1],
        queue=False
    ).then(
        clear_swap_detail_ui,
        None,
        [swap_options, whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )

    swap_t1.change(
        load_confirmed_swaps_for_context,
        [swap_t1, swap_day],
        [swap_confirmed_state, confirmed_tbl_html],
        queue=False
    ).then(
        get_teacher_periods_marked,
        [swap_t1, swap_day, swap_confirmed_state, swap_p1],
        [swap_p1],
        queue=False
    ).then(
        clear_swap_detail_ui,
        None,
        [swap_options, whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )
    swap_p1.change(
        get_swap_candidates_for_period,
        [swap_t1, swap_p1, swap_day, swap_confirmed_state],
        [swap_options, whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )

    swap_options.change(
        on_swap_option_selected,
        [swap_options, swap_t1, swap_p1, swap_day],
        [whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )

    swap_options.input(
        on_swap_option_selected,
        [swap_options, swap_t1, swap_p1, swap_day],
        [whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=False
    )

    swap_options.select(
        on_swap_option_selected_from_event,
        [swap_options, swap_t1, swap_p1, swap_day],
        [whatsapp_msg, wa_html_btn, btn_swap_confirm],
        queue=True
    )

    btn_swap_confirm.click(
        confirm_swap,
        [swap_t1, swap_p1, swap_options, swap_day, whatsapp_msg, swap_confirmed_state, current_user_name, current_user_role],
        [swap_confirmed_state, confirmed_tbl_html],
        queue=False
    ).then(
        get_teacher_periods_marked,
        [swap_t1, swap_day, swap_confirmed_state, swap_p1],
        [swap_p1],
        queue=False
    )
    btn_swap_img.click(
        generate_swap_table_image,
        [swap_confirmed_state, swap_t1, swap_day],
        [swap_img_out],
        queue=False
    )
    btn_swap_excel.click(
        export_confirmed_swaps_excel,
        outputs=[swap_excel_out],
        queue=False
    )

app.launch(
    css=css,
    js=js_code,
    server_name="0.0.0.0",
    server_port=7860,
    ssr_mode=False,
    allowed_paths=[
        EXPORTS_DIR,
        IMG_DIR,
        SWAP_IMG_DIR,
    ],
)
