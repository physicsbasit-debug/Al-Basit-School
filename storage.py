# -*- coding: utf-8 -*-
"""
storage.py
طبقة التخزين الأساسية لمنظومة مسار.

هذه المرحلة تنقل المسارات ودوال JSON الآمنة والأقفال من app.py،
وتضيف تحميل إعدادات المدرسة والقيم التشغيلية المشتقة منها:
SCHOOL_CONFIG / MAX_PERIODS / OFFICIAL_DEPTS.
"""

from __future__ import annotations

import datetime
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
    SYSTEM_NAME,
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


def get_now_oman():
    """إرجاع الوقت الحالي بتوقيت سلطنة عُمان كدالة وقت عامة نظيفة."""
    tz_oman = datetime.timezone(datetime.timedelta(hours=4))
    return datetime.datetime.now(tz_oman)


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
# v1.8.5l / Phase 3H-a-1 — سجل العمليات الحساسة العام
# ─────────────────────────────────────────────────────────────────────────────

def _audit_json_safe(value):
    """تحويل القيم غير القابلة للتسلسل إلى نص قبل حفظها في سجل العمليات."""
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
            source_name = str(SCHOOL_CONFIG.get("system_name", SYSTEM_NAME) or SYSTEM_NAME)

            record = {
                "timestamp": get_now_oman().strftime("%Y-%m-%d %H:%M:%S"),
                "actor_name": actor_name,
                "actor_role": actor_role,
                "action": str(action or "").strip(),
                "target_teacher": str(target_teacher or "").strip(),
                "old_value": _audit_json_safe(old_value),
                "new_value": _audit_json_safe(new_value),
                "details": str(details or "").strip(),
                "source": source_name,
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

# ─────────────────────────────────────────────────────────────────────────────
# v1.8.5f / Phase 3E-pre — الحالة العامة المشتركة
# ─────────────────────────────────────────────────────────────────────────────
# ملاحظة معمارية حرجة:
# لا تعيد تعيين هذه الكائنات داخل دوال التحميل أو التصفير.
# استخدم clear/update/extend دائمًا حتى تبقى مراجع app.py وstorage.py مشتركة.
teachers_db = {}
daily_db = []
processed_absences = set()
exemptions_log = []
swap_db = {}


def _normalize_exempt_slots_for_storage(raw_slots):
    """تنظيف exempt_slots أثناء تحميل قاعدة المعلمين دون ربط storage.py بـ app.py."""
    clean_slots = []
    seen = set()
    for slot in raw_slots or []:
        day = None
        period = None
        if isinstance(slot, dict):
            day = slot.get("day") or slot.get("اليوم")
            period = slot.get("period") or slot.get("الحصة")
        elif isinstance(slot, (list, tuple)) and len(slot) >= 2:
            day, period = slot[0], slot[1]
        else:
            import re
            s = str(slot or "").strip()
            day = next((d for d in SCHOOL_WEEK_DAYS if d in s), None)
            m = re.search(r"(?:ح|الحصة)?\s*(\d+)", s)
            period = m.group(1) if m else None
        day = str(day or "").strip()
        if day not in SCHOOL_WEEK_DAYS:
            continue
        try:
            period_int = int(period)
        except Exception:
            continue
        if period_int < 1 or period_int > MAX_PERIODS:
            continue
        key = (day, period_int)
        if key in seen:
            continue
        seen.add(key)
        clean_slots.append({"day": day, "period": period_int})
    return clean_slots


def save_db():
    if not safe_write_json(DB_FILE, teachers_db):
        print("save_db error: safe_write_json failed")


def load_db():
    """تحميل قاعدة المعلمين مع الحفاظ على هوية teachers_db في الذاكرة."""
    loaded_db = {}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                loaded_db = loaded
        except Exception as e:
            print("Error loading DB:", e)
            loaded_db = {}

    teachers_db.clear()
    teachers_db.update(loaded_db)

    for teacher_name in list(teachers_db.keys()):
        info = teachers_db.get(teacher_name, {})
        if not isinstance(info, dict):
            teachers_db[teacher_name] = {}
            info = teachers_db[teacher_name]
        info["phone"] = info.get("phone", "")
        info["specialty"] = info.get("specialty", "")
        info["role"] = info.get("role", "معلم")
        info["exempt_days"] = info.get("exempt_days", [])
        try:
            info["exempt_periods"] = [int(p) for p in info.get("exempt_periods", [])]
        except Exception:
            info["exempt_periods"] = []
        info["exempt_slots"] = _normalize_exempt_slots_for_storage(info.get("exempt_slots", []))
        info["absence_dates"] = info.get("absence_dates", [])
        info["shortcoming_count"] = info.get("shortcoming_count", 0)
        info["exemption_updated_at"] = info.get("exemption_updated_at", "")

        for day in SCHOOL_WEEK_DAYS:
            if day in info and isinstance(info[day], dict):
                info[day] = {int(k): str(v) for k, v in info[day].items()}


def save_exemptions_log():
    if not safe_write_json(EXEMPTIONS_LOG_FILE, exemptions_log):
        print("save_exemptions_log error: safe_write_json failed")


def load_exemptions_log():
    """تحميل سجل الإعفاءات مع الحفاظ على هوية القائمة."""
    loaded_log = []
    if os.path.exists(EXEMPTIONS_LOG_FILE):
        try:
            with open(EXEMPTIONS_LOG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_log = data if isinstance(data, list) else []
        except Exception as e:
            print(f"load_exemptions_log error: {e}")
            loaded_log = []

    exemptions_log.clear()
    exemptions_log.extend(loaded_log)


def save_daily_db():
    payload = {
        "daily": daily_db,
        "processed": [list(x) if isinstance(x, tuple) else x for x in processed_absences],
    }
    if not safe_write_json(DAILY_DB_FILE, payload):
        print("save_daily_db error: safe_write_json failed")


def load_daily_db():
    """تحميل تكليفات اليوم مع الحفاظ على هوية daily_db وprocessed_absences."""
    loaded_daily = []
    loaded_processed = set()

    if os.path.exists(DAILY_DB_FILE):
        try:
            with open(DAILY_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                loaded_daily = data
                loaded_processed = set()
            elif isinstance(data, dict):
                loaded_daily = data.get("daily", []) if isinstance(data.get("daily", []), list) else []
                processed_raw = data.get("processed", [])
                loaded_processed = set(
                    tuple(x) for x in processed_raw if isinstance(x, (list, tuple))
                )
        except Exception as e:
            print(f"load_daily_db error: {e}")
            loaded_daily = []
            loaded_processed = set()

    daily_db.clear()
    daily_db.extend(loaded_daily)
    processed_absences.clear()
    processed_absences.update(loaded_processed)


def load_swap_db():
    """تحميل تبادلات الأسبوع مع الحفاظ على هوية swap_db."""
    loaded_swap = {}
    if os.path.exists(SWAP_DB_FILE):
        try:
            with open(SWAP_DB_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                loaded_swap = loaded
        except Exception:
            loaded_swap = {}

    swap_db.clear()
    swap_db.update(loaded_swap)


def save_swap_db():
    if not safe_write_json(SWAP_DB_FILE, swap_db):
        print("save_swap_db error: safe_write_json failed")


ensure_data_directories()
load_db()
load_exemptions_log()
load_daily_db()
load_swap_db()
