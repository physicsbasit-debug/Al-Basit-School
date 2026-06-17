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
matplotlib.use('Agg')  
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import arabic_reshaper
from bidi.algorithm import get_display
from PIL import Image, ImageDraw, ImageFont


# --- 1. الإعدادات والوقت ---
tz_oman = datetime.timezone(datetime.timedelta(hours=4))
DB_FILE = "school_balances.json"
DAILY_DB_FILE = "daily_assignments.json" 
SWAP_IMG_DIR = "generated_swap_tables"
PAGE_SIZE = 12

DATA_DIR = "data"
IMG_DIR = os.path.join(DATA_DIR, "generated_images")
SCHEDULES_DIR = os.path.join(DATA_DIR, "schedules")
BACKUPS_DIR = os.path.join(DATA_DIR, "backups")
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
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    os.makedirs(BACKUPS_DIR, exist_ok=True)


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



def get_reference_file_status(file_path):
    if os.path.exists(file_path):
        modified_time = datetime.datetime.fromtimestamp(
            os.path.getmtime(file_path),
            tz=tz_oman
        ).strftime("%Y-%m-%d %H:%M")
        return {
            "exists": True,
            "status_text": "✅ موجود",
            "file_name": os.path.basename(file_path),
            "modified_at": modified_time,
        }

    return {
        "exists": False,
        "status_text": "❌ غير موجود",
        "file_name": "—",
        "modified_at": "—",
    }

DEFAULT_SCHOOL_CONFIG = {
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

SYSTEM_NAME = str(SCHOOL_CONFIG.get("system_name", DEFAULT_SCHOOL_CONFIG["system_name"]))
SYSTEM_SUBTITLE = str(SCHOOL_CONFIG.get("system_subtitle", DEFAULT_SCHOOL_CONFIG["system_subtitle"]))
SCHOOL_NAME = str(SCHOOL_CONFIG.get("school_name", DEFAULT_SCHOOL_CONFIG["school_name"]))
DEVELOPER_CREDIT = str(SCHOOL_CONFIG.get("developer_credit", DEFAULT_SCHOOL_CONFIG["developer_credit"]))
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


AUTH_DB_FILE = os.getenv("AUTH_DB_FILE", "auth_db.json")


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
        file_info = get_reference_file_status(file_path)
        if not file_info["exists"] and dept_has_loaded_schedule_data(dept_name):
            db_modified = datetime.datetime.fromtimestamp(
                os.path.getmtime(DB_FILE),
                tz=tz_oman
            ).strftime("%Y-%m-%d %H:%M") if os.path.exists(DB_FILE) else "—"
            file_info = {
                "exists": True,
                "status_text": "✅ محمّل داخل المنظومة",
                "file_name": "—",
                "modified_at": db_modified,
            }
        schedules_status[dept_name] = file_info

    return {
        "admin_file": get_reference_file_status(ADMIN_FILE),
        "phones_file": get_reference_file_status(PHONES_FILE),
        "schedules": schedules_status,
    }
def render_reference_file_card(title, file_info):
    status_color = "#2e7d32" if file_info["exists"] else "#c62828"
    bg_color = "#e8f5e9" if file_info["exists"] else "#ffebee"

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
        shutil.copy(file.name, ADMIN_FILE)
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
        shutil.copy(file_path, PHONES_FILE)

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
        shutil.copy(file_path, SCHEDULE_FILES[dept_name])

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

APP_DIR = os.path.dirname(os.path.abspath(__file__))
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
                "source": "منظومة مسار"
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
SWAP_DB_FILE = "friendly_swaps.json"
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
        header_bg = "#004d40"
        draw.rectangle((0, 0, image_width, header_h), fill=header_bg)

        title = "جدول التبادلات الودية المعتمدة"
        subtitle = f"{teacher_name or 'الكل'} | {day_name} | {target_date}"

        title_w, title_h = text_size(title, font_title)
        subtitle_w, subtitle_h = text_size(subtitle, font_subtitle)
        draw.text(((image_width - title_w) / 2, 24), title, font=font_title, fill="#ffca28")
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

        footer_text = "منظومة مسار للاحتياط والتبادل الودي"
        footer_w, footer_h = text_size(footer_text, font_footer)
        draw.text(((image_width - footer_w) / 2, image_height - 39), footer_text, font=font_footer, fill="#004d40")

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
    if pin in AUTH_DB:
        user_info = AUTH_DB[pin]
        role = user_info.get("role", "")
        dept = user_info.get("dept", "الكل")
        if role == "مستخدم عام":
            dept = "المعلمون"
        name = user_info.get("name", "")
        is_owner = bool(user_info.get("is_owner", False) or role == "صاحب النظام")

        ui_vis = get_ui_visibility_updates(pin, role, is_owner)
        is_shared_teacher = ui_vis["is_shared_teacher"]

        effective_dept = resolve_effective_dept(dept)
        dept_for_ui = effective_dept
        is_admin = bool(ui_vis["is_admin"])

        raw_msg = WELCOME_MESSAGES.get(role, "مرحباً بك ({name}) في النظام.")
        welcome_msg = f"<div style='background:#004d40; color:#ffca28; padding:15px; border-radius:10px; text-align:center; font-size:18px; font-weight:bold; margin-bottom:15px;'>{raw_msg.format(name=name)}</div>"

        if is_admin:
            up_dept_update = gr.update(interactive=True)
            manual_entry_visibility = gr.update(visible=is_owner)
        else:
            up_dept_update = gr.update(value=None, interactive=False)
            manual_entry_visibility = gr.update(visible=False)

        updates = refresh_ui_on_change(dept_for_ui, day_val, is_admin)

        return [
            gr.update(visible=False),
            gr.update(visible=True),
            welcome_msg,
            gr.update(choices=["الكل"] + OFFICIAL_DEPTS, value=dept_for_ui, interactive=is_admin),
            gr.update(value=""),
            up_dept_update,
            manual_entry_visibility,
            is_admin,
            is_owner,
            name,
            role,
        ] + list(updates) + [
            gr.update(visible=dept_for_ui in ["العلوم", "المهارات الفردية"] and not is_shared_teacher),
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

    gr.Warning("❌ رمز الدخول غير صحيح! الرجاء المحاولة مرة أخرى.")
    error_updates = [gr.update()] * 27
    return [
        gr.update(),
        gr.update(),
        "<div style='color:red; text-align:center; font-weight:bold; margin-top:10px;'>❌ رمز الدخول غير صحيح! حاول مرة أخرى.</div>",
        gr.update(),
        gr.update(),
        gr.update(),
        gr.update(),
        False,
        False,
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
        gr.update()
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

"""

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

header_html = """
<div class='main-header'>
    <div class='header-grid'>
        <div class='h-logo'><img src='https://i.imgur.com/1cxFlX7.png' alt='Logo'></div>
        <div class='h-ministry'>وزارة التعليم<br>المديرية العامة للتعليم بمحافظة<br>جنوب الباطنة</div>
        <div class='h-title'>
            <div class='h-title-main'>منظومة مسار</div>
            <div class='h-title-sub'>للاحتياط والتبادل الودي</div>
        </div>
        <div class='h-school' style='color: #ffca28 !important; -webkit-text-fill-color: #ffca28 !important; white-space: nowrap;'>مدرسة الباسط للتعليم الأساسي (8-10)</div>
        <div class='h-credits'><div class='credits-box'>👑 فكرة وتطوير: أ. محمود اليحيائي - أ. وليد الهنائي</div></div>
    </div>
</div>
"""

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
    current_schedule_state = gr.State()
    reserve_generation_state = gr.State(value=get_empty_generation_state())

    with gr.Column(visible=True, elem_classes="login-box") as login_container:
        gr.HTML("""
<div style="
    background:linear-gradient(145deg,#003d33 0%,#004d40 40%,#00695c 80%,#004d40 100%);
    margin:0px 0px 20px 0px;
    padding:30px 20px 25px;
    padding-bottom: 0 !important;
    overflow: hidden;
    border-radius:16px 16px 0 0;
    text-align:center;
    border-bottom: none;
">
    <img id="main-logo" src='https://i.imgur.com/1cxFlX7.png' style='
        width:115px; height:115px;
        border-radius:50%;
        border:3px solid #ffca28;
        background:white;
        padding:3px;
        display:inline-block;
        margin-bottom:14px;
        box-shadow:
            0 15px 40px rgba(0,0,0,0.6),
            0 6px 15px rgba(0,0,0,0.4),
            0 0 0 5px rgba(255,202,40,0.3),
            0 0 0 10px rgba(0,77,64,0.15),
            4px -4px 15px rgba(255,255,255,0.2),
            -4px 4px 15px rgba(0,0,0,0.3);
        animation: logo4d 4s ease-in-out infinite;
        cursor: pointer;
    '>
    <div style='font-size:26px;font-weight:900;color:#ffca28;text-shadow:0 2px 8px rgba(0,0,0,0.4);margin-bottom:6px;'>
         بوابة الدخول
    </div>
    <div style='font-size:13px;color:rgba(255,255,255,0.9);font-weight:600;'>
        مدرسة الباسط للتعليم الأساسي (8-10)
    </div>

    <div style="margin-bottom: -4px; line-height: 0; overflow: hidden; margin-left: -28px; margin-right: -28px;">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 2880 60" preserveAspectRatio="none" style="display:block; width:200%; height:15px; animation: waveMove 4s linear infinite;">
            <path fill="#ffca28" fill-opacity="1" d="M-10,35 C180,5 360,55 540,25 C720,0 900,50 1080,20 C1260,-5 1400,45 1450,30 C1620,5 1800,55 1980,25 C2160,0 2340,50 2520,20 C2700,-5 2840,45 2890,30 L2890,65 L-10,65 Z"/>
        </svg>
    </div>
</div>
""")

        pin_input = gr.Textbox(type="password", show_label=False, placeholder="Enter ثم اضغط (PIN) 🔑 أدخل رمز الدخول", text_align="center")
        login_btn = gr.Button("تسجيل الدخول", elem_classes="admin-btn")
        login_msg = gr.HTML()
        gr.HTML("<div style='text-align:center;'><div class='credits-box' style='font-size: 10px; padding: 5px 10px;'>👑 فكرة وتطوير: أ. محمود اليحيائي - أ. وليد الهنائي © 2026</div></div>")

    with gr.Column(visible=False) as main_app_container:
        gr.HTML(header_html)
        
        with gr.Row(elem_classes="top-user-row"):
            with gr.Column(scale=5, elem_classes="welcome-col"):
                welcome_html = gr.HTML(elem_classes="welcome-html-box")
            with gr.Column(scale=1, min_width=120, elem_classes="logout-col"):
                logout_btn = gr.Button("🚪 خروج و إقفال", elem_classes=["reset-btn", "logout-btn"])
        
        with gr.Column(visible=True, elem_id="masar_home_dashboard", elem_classes="masar-home-dashboard") as home_dashboard:
            gr.HTML("""
            <div class='masar-home-hero'>
                <div class='masar-home-title'>👋 مرحبًا بك في منظومة مسار</div>
                <div class='masar-home-subtitle'>تم تجهيز لوحة العمل حسب صلاحيتك.</div>
                <div class='masar-home-note'>اختر القسم المناسب للبدء.</div>
            </div>
            """)

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
                    gr.Markdown("### 🗄️ مركز البيانات المدرسية")
                    gr.HTML("<div style='background:#e8f5e9; color:#004d40; padding:14px; border-radius:10px; border-right:5px solid #2e7d32; margin-bottom:12px;'>هذه البوابة هي المرجع الرسمي لاعتماد ملفات الإداريين وأرقام المعلمين والجداول المرجعية للأقسام، بدلاً من الرفع التشغيلي المباشر.</div>")
                    gr.HTML(f"<div style='background:#fffde7; color:#4d3b00; padding:12px; border-radius:10px; border-right:5px solid #ca8a04; margin-bottom:12px; font-weight:800; line-height:1.8;'>ملف إعدادات المدرسة: <b>data/school_config.json</b><br>المدرسة الحالية: <b>{SCHOOL_NAME}</b><br>اسم النظام: <b>{SYSTEM_NAME} - {SYSTEM_SUBTITLE}</b><br>عدد الحصص اليومية: <b>{MAX_PERIODS}</b></div>")

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

                    with gr.Accordion("🧩 أدوات إدارية إضافية", open=False, visible=False) as manual_entry_container:
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

    # ── ربط الأحداث ──────────────────────────────────────────────
    update_outputs = [
        abs_in, tbl_bal, tbl_abs, tbl_short, tbl_day, day_table_html, day_pagination_row, btn_prev_page, btn_next_page, page_info_html, day_page_state, t_name, check_teacher_in, rule_teacher, 
        radar_warning_html, tbl_out, edit_abs_t, current_schedule_state, 
        msg_summary, msg_individual_html, date_display, admin_zone_title,
        admin_zone_help, edit_period, cb_cross_dept, btn_alt, btn_img
    ]
    app.load(sync_current_school_days, None, [day_in, swap_day])

    btn_open_distribution.click(lambda: open_home_section("distribution"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_balances.click(lambda: open_home_section("balances"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_exemptions.click(lambda: open_home_section("exemptions"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_swap.click(lambda: open_home_section("swap"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_day.click(lambda: open_home_section("day_table"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_teacher.click(lambda: open_home_section("teacher_table"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_open_school_data.click(lambda: open_home_section("school_data"), [], [home_dashboard, tabs_container, main_tabs], queue=False).then(None, None, None, js=show_selected_tab_container_js())
    btn_back_home.click(return_to_home_dashboard, [], [home_dashboard, tabs_container], queue=False).then(None, None, None, js=return_home_dashboard_js())
    login_btn.click(
        attempt_login,
        inputs=[pin_input, day_in],
        outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role] + update_outputs + [t_specialty_edit, clear_btn, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row]
    ).then(
        show_home_dashboard_after_login,
        [dept_in, current_user_is_admin, current_user_is_owner, current_user_role],
        [home_dashboard, tabs_container, card_distribution, card_balances, card_exemptions, card_swap, card_day, card_teacher, card_school_data],
        queue=False
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
        outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role] + update_outputs + [t_specialty_edit, clear_btn, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row]
    ).then(
        show_home_dashboard_after_login,
        [dept_in, current_user_is_admin, current_user_is_owner, current_user_role],
        [home_dashboard, tabs_container, card_distribution, card_balances, card_exemptions, card_swap, card_day, card_teacher, card_school_data],
        queue=False
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
    logout_btn.click(do_logout, inputs=[], outputs=[login_container, main_app_container, welcome_html, dept_in, current_user_is_admin, current_user_is_owner, current_user_name, current_user_role, current_schedule_state, img_out, cb_cross_dept, school_data_tab, controls_row, exemptions_tab, distribution_tab, balances_tab, swap_tab, day_tab, teacher_tab, swap_export_row, reserve_generation_state, swap_confirmed_state]).then(
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
    ssr_mode=False
)
