# -*- coding: utf-8 -*-
"""
storage.py
طبقة التخزين الأساسية لمنظومة مسار.

هذه المرحلة تنقل المسارات ودوال JSON الآمنة والأقفال من app.py،
وتضيف تحميل إعدادات المدرسة والقيم التشغيلية المشتقة منها:
SCHOOL_CONFIG / MAX_PERIODS / OFFICIAL_DEPTS.
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import tempfile
import threading

from config import (
    APP_DIR,
    LOCAL_DATA_DIR,
    REQUESTED_PERSISTENT_DATA_DIR,
    DEFAULT_SCHOOL_CONFIG,
    MAX_BACKUPS_PER_FILE,
    DB_FILENAME,
    DAILY_DB_FILENAME,
    SWAP_DB_FILENAME,
    AUTH_DB_FILENAME,
    REFERENCE_STATUS_FILENAME,
    AUTH_ACCOUNTS_FILENAME,
    MIGRATION_STATUS_FILENAME,
    ADMIN_FILENAME,
    PHONES_FILENAME,
    EXEMPTIONS_LOG_FILENAME,
    AUDIT_LOG_FILENAME,
    SCHOOL_CONFIG_FILENAME,
    SCHEDULE_FILE_NAMES,
)


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

DB_FILE = os.path.join(DATA_DIR, DB_FILENAME)
DAILY_DB_FILE = os.path.join(DATA_DIR, DAILY_DB_FILENAME)
SWAP_DB_FILE = os.path.join(DATA_DIR, SWAP_DB_FILENAME)
AUTH_DB_FILE = os.getenv("AUTH_DB_FILE", os.path.join(DATA_DIR, AUTH_DB_FILENAME))

IMG_DIR = os.path.join(DATA_DIR, "generated_images")
SWAP_IMG_DIR = os.path.join(DATA_DIR, "generated_swap_tables")
SCHEDULES_DIR = os.path.join(DATA_DIR, "schedules")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
BRANDING_DIR = os.path.join(DATA_DIR, "branding")
REFERENCE_STATUS_FILE = os.path.join(DATA_DIR, REFERENCE_STATUS_FILENAME)
AUTH_ACCOUNTS_FILE = os.path.join(DATA_DIR, AUTH_ACCOUNTS_FILENAME)
MIGRATION_STATUS_FILE = os.path.join(DATA_DIR, MIGRATION_STATUS_FILENAME)

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


ADMIN_FILE = os.path.join(DATA_DIR, ADMIN_FILENAME)
PHONES_FILE = os.path.join(DATA_DIR, PHONES_FILENAME)
EXEMPTIONS_LOG_FILE = os.path.join(DATA_DIR, EXEMPTIONS_LOG_FILENAME)
AUDIT_LOG_FILE = os.path.join(DATA_DIR, AUDIT_LOG_FILENAME)
SCHOOL_CONFIG_FILE = os.path.join(DATA_DIR, SCHOOL_CONFIG_FILENAME)

SCHEDULE_FILES = {dept: os.path.join(SCHEDULES_DIR, filename) for dept, filename in SCHEDULE_FILE_NAMES.items()}


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
    # لا نعتمد على get_now_oman هنا لتجنب ربط storage.py بمنطق app.py،
    # لكن نحافظ على توقيت عُمان في أسماء النسخ الاحتياطية كما كان في app.py.
    import datetime
    tz_oman = datetime.timezone(datetime.timedelta(hours=4))
    timestamp = datetime.datetime.now(tz_oman).strftime("%Y%m%d_%H%M%S_%f")
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
# v1.8.5d / Phase 3C — إعدادات المدرسة التشغيلية
# ─────────────────────────────────────────────────────────────────────────────

def load_school_config():
    """
    تحميل إعدادات المدرسة من school_config.json.
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


def _coerce_runtime_periods_per_day(config):
    try:
        value = int(config.get("periods_per_day", DEFAULT_SCHOOL_CONFIG["periods_per_day"]))
    except Exception:
        value = int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])
    return value if value in (7, 8) else int(DEFAULT_SCHOOL_CONFIG["periods_per_day"])


SCHOOL_CONFIG = load_school_config()
MAX_PERIODS = _coerce_runtime_periods_per_day(SCHOOL_CONFIG)
SCHOOL_WEEK_DAYS = list(
    SCHOOL_CONFIG.get("week_days", DEFAULT_SCHOOL_CONFIG["week_days"])
    or DEFAULT_SCHOOL_CONFIG["week_days"]
)
SCHOOL_WEEKEND_DAYS = list(
    SCHOOL_CONFIG.get("weekend_days", DEFAULT_SCHOOL_CONFIG["weekend_days"])
    or DEFAULT_SCHOOL_CONFIG["weekend_days"]
)
OFFICIAL_DEPTS = list(
    SCHOOL_CONFIG.get("official_departments", DEFAULT_SCHOOL_CONFIG["official_departments"])
    or DEFAULT_SCHOOL_CONFIG["official_departments"]
)
