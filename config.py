# -*- coding: utf-8 -*-
"""
config.py
ثوابت خام وآمنة لمنظومة مسار.

ملاحظة معمارية مهمة:
- هذا الملف لا يحمّل ملفات JSON ولا يكتبها.
- لا يحتوي SCHOOL_CONFIG ولا MAX_PERIODS ولا OFFICIAL_DEPTS.
- القيم الديناميكية المشتقة من school_config.json تبقى مؤقتًا في app.py حتى مرحلة storage.py.
"""

import os

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_SIZE = 12

# مسار التخزين المطلوب قبل فحص القابلية للكتابة. DATA_DIR النهائي يُحسب في app.py.
LOCAL_DATA_DIR = os.path.join(APP_DIR, "data")
REQUESTED_PERSISTENT_DATA_DIR = os.getenv("MASAR_DATA_DIR", "/data/masar").strip() or "/data/masar"

MAX_BACKUPS_PER_FILE = 10

DB_FILENAME = "school_balances.json"
DAILY_DB_FILENAME = "daily_assignments.json"
SWAP_DB_FILENAME = "friendly_swaps.json"
AUTH_DB_FILENAME = "auth_db.json"

REFERENCE_STATUS_FILENAME = "reference_files_status.json"
AUTH_ACCOUNTS_FILENAME = "auth_accounts.json"
MIGRATION_STATUS_FILENAME = ".v1_8_1_migration.json"
ADMIN_FILENAME = "admin_staff.xlsx"
PHONES_FILENAME = "teacher_phones.xlsx"
EXEMPTIONS_LOG_FILENAME = "exemptions_log.json"
AUDIT_LOG_FILENAME = "audit_log.json"
SCHOOL_CONFIG_FILENAME = "school_config.json"

SCHEDULE_FILE_NAMES = {
    "التربية الإسلامية": "التربية_الإسلامية.xlsx",
    "اللغة العربية": "اللغة_العربية.xlsx",
    "الرياضيات": "الرياضيات.xlsx",
    "العلوم": "العلوم.xlsx",
    "اللغة الإنجليزية": "اللغة_الإنجليزية.xlsx",
    "الدراسات الإجتماعية": "الدراسات_الاجتماعية.xlsx",
    "المهارات الفردية": "المهارات_الفردية.xlsx",
}

# أدوار إدارية ثابتة تستخدمها الواجهة ومركز البيانات.
ADMIN_ROLES = ["مدير المدرسة", "المدير المساعد", "منسق شؤون مدرسية", "أخصائي توجيه مهني", "أخصائي اجتماعي", "أخصائي شؤون ادارية ومالية", "أخصائي مصادر التعلم", "أخصائي أنظمة مدرسية", "فني مختبر علوم", "فني دعم أجهزة مدرسية ثالث"]
ALL_ROLES = ["معلم", "معلم أول", "منسق مادة"] + ADMIN_ROLES

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
    "official_departments": ["الهيئة الإدارية", "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية"],
}

# اسم المنظومة الافتراضي للاستخدامات العامة مثل سجل العمليات.
SYSTEM_NAME = str(DEFAULT_SCHOOL_CONFIG["system_name"])
