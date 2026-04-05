import os
import gradio as gr
import pandas as pd
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import datetime
import matplotlib
import random
import json
import re
import urllib.parse
matplotlib.use('Agg')  
import matplotlib.pyplot as plt
import urllib.request
import matplotlib.font_manager as fm
import arabic_reshaper
from bidi.algorithm import get_display

# --- 1. الإعدادات والوقت ---
tz_oman = datetime.timezone(datetime.timedelta(hours=4))
DB_FILE = "school_balances.json"
DAILY_DB_FILE = "daily_assignments.json" 

OFFICIAL_DEPTS = ["الهيئة الإدارية", "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية"]
ADMIN_ROLES = ["مدير المدرسة", "المدير المساعد", "منسق شؤون مدرسية", "أخصائي توجيه مهني", "أخصائي اجتماعي", "أخصائي شؤون ادارية ومالية", "أخصائي مصادر التعلم", "أخصائي أنظمة مدرسية", "فني مختبر علوم", "فني دعم أجهزة مدرسية ثالث"]
ALL_ROLES = ["معلم", "معلم أول", "منسق مادة"] + ADMIN_ROLES

AUTH_DB = {
    "0000": {"role": "مدير المدرسة", "dept": "الكل", "name": "أ. عبدالله حمود الزدجالي"},
    "1111": {"role": "المدير المساعد", "dept": "الكل", "name": "أ. علي العجمي"},
    "2222": {"role": "العلوم", "dept": "العلوم", "name": "أ. وليد الهنائي"},
    "3333": {"role": "الرياضيات", "dept": "الرياضيات", "name": "أ. سعيد العامري"},
    "4444": {"role": "التربية الإسلامية", "dept": "التربية الإسلامية", "name": "أ. بدر الزدجالي"},
    "5555": {"role": "اللغة العربية", "dept": "اللغة العربية", "name": "أ. سعود المعولي"},
    "6666": {"role": "اللغة الإنجليزية", "dept": "اللغة الإنجليزية", "name": "أ. هيثم محمد عثمان"},
    "7777": {"role": "الدراسات الإجتماعية", "dept": "الدراسات الإجتماعية", "name": "أ. صالح الشبيبي"},
    "8888": {"role": "المهارات الفردية", "dept": "المهارات الفردية", "name": "أ. محمد الفلاحي"}
}

WELCOME_MESSAGES = {
    "مدير المدرسة": "👑 أهلاً بك يا قائد المدرسة وربان سفينتها ({name}).. الرادار الإداري وغرفة العمليات رهن إشارتك.",
    "المدير المساعد": "🛡️ أهلاً بالذراع الأيمن للقيادة والسند الإداري ({name}).. صلاحيات التدخل المفتوحة مفعلة.",
    "العلوم": "🌟 مرحباً بقائد الملحمة والمعلم الأول ({name}).. تم تجهيز شاشة قسم العلوم بدقة.",
    "الرياضيات": "✨ نورتنا مهندس الأرقام ({name}) شاشة قسم الرياضيات جاهزة لك.",
    "التربية الإسلامية": "🕌 سُعدنا بانضمامك ({name}) شاشة قسم التربية الإسلامية جاهزة لك.",
    "اللغة العربية": "📜 مايسترو البيان ({name}) نورتنا وقسم اللغة العربية جاهز لك.",
    "اللغة الإنجليزية": "🌍 أهلا بك سفير اللغة ({name}) شاشة قسم اللغة الإنجليزية جاهزة لك.",
    "الدراسات الإجتماعية": "🗺️ مرحبا بك ({name}) قسم الدراسات الإجتماعية جاهز.",
    "المهارات الفردية": "🎨 سُعدنا بانضمامك ({name}) هذه مساحة للتنسيق وتنظيم العمل."
}

last_assigned_teachers = []
processed_absences = set()

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

font_path = "Cairo-Regular.ttf"
try:
    if not os.path.exists(font_path):
        url = "https://raw.githubusercontent.com/google/fonts/main/ofl/cairo/Cairo-Regular.ttf"
        urllib.request.urlretrieve(url, font_path)
except: pass
arabic_font = fm.FontProperties(fname=font_path) if os.path.exists(font_path) else fm.FontProperties()
reshaper_config = {'support_ligatures': False}
reshaper = arabic_reshaper.ArabicReshaper(configuration=reshaper_config)

def fix_arabic(text):
    reshaped = reshaper.reshape(str(text))
    bidi = get_display(reshaped)
    for c in ['\u202a', '\u202b', '\u202c', '\u200e', '\u200f']: bidi = bidi.replace(c, '')
    return bidi

teachers_db = {}
daily_db = []

def save_db():
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f: json.dump(teachers_db, f, ensure_ascii=False)
    except: pass

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
                    
                    for day in ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]:
                        if day in teachers_db[t]:
                            teachers_db[t][day] = {int(k): str(v) for k, v in teachers_db[t][day].items()}
        except Exception as e: print("Error loading DB:", e)
load_db()

def save_daily_db():
    try:
        with open(DAILY_DB_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "daily": daily_db,
                "processed": list(processed_absences)
            }, f, ensure_ascii=False)
    except:
        pass
        
def load_daily_db():
    global daily_db, processed_absences
    if os.path.exists(DAILY_DB_FILE):
        try:
            with open(DAILY_DB_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                daily_db = data  # ← توافق مع الملفات القديمة
            else:
                daily_db = data.get("daily", [])
                processed_absences = set(
                    tuple(x) for x in data.get("processed", [])
                )
        except:
            daily_db = []
            
def format_teacher_name(t_name):
    if t_name in teachers_db:
        role = teachers_db[t_name].get("role", "معلم")
        if role in ["معلم أول"] + ADMIN_ROLES: return f"{t_name} ({role})"
    return t_name

def get_teacher_choices(dept_filter="الكل"):
    t_list = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])
    choices = []
    for t in t_list:
        role = teachers_db[t].get("role", "معلم")
        if role != "معلم": choices.append(f"{t} ({role})")
        else: choices.append(t)
    return choices

def get_absentee_choices(dept_filter="الكل"):
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
            for d in ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]:
                for c in info.get(d, {}).values():
                    if target_fingerprint == get_class_dna(c) and target_fingerprint != "": teaches_same = True
                    
            if teaches_same:
                warn = check_teacher_load(name, day_name, p_int)
                warn_str = f" {warn}" if warn else ""
                candidates.append(f"🦅 {name} (يدرس نفس الصف){warn_str}")
        return candidates
    except Exception as e:
        return []

def add_manual_staff(name, dept, phone, role, dept_filter):
    if not name or not str(name).strip(): return "<div style='color:red; font-weight:bold;'>❌ الرجاء إدخال الاسم.</div>", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update()
    t_name = clean_teacher_name(name)
    if t_name not in teachers_db:
        teachers_db[t_name] = {"dept": dept, "cover_count": 0, "absent_count": 0, "shortcoming_count": 0, "phone": "", "specialty": "", "role": role, "exempt_days": [], "exempt_periods": [], "absence_dates": [], "الأحد": {}, "الإثنين": {}, "الثلاثاء": {}, "الأربعاء": {}, "الخميس": {}}
    else:
        teachers_db[t_name]["dept"] = dept
        teachers_db[t_name]["role"] = role
    if phone:
        phone_clean = re.sub(r'\D', '', str(phone))
        if len(phone_clean) == 8: phone_clean = "968" + phone_clean
        teachers_db[t_name]["phone"] = phone_clean
    save_db()
    choices_all = get_teacher_choices(dept_filter)
    abs_choices = get_absentee_choices(dept_filter)
    t_names_filtered = sorted([t for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter])
    msg = f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px;'>✅ تم إضافة/تحديث ({t_name}) بنجاح كطاقم إداري!</div>"
    return msg, gr.update(choices=abs_choices), gr.update(choices=choices_all), gr.update(choices=choices_all), gr.update(choices=t_names_filtered), gr.update(value=""), gr.update(value="")

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
                    elif "الثامنة" in val: col_to_p[c] = 8
                    
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
        return (gr.update(choices=["الكل"] + OFFICIAL_DEPTS), gr.update(choices=abs_choices), gr.update(choices=choices_all), gr.update(choices=choices_all), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), success_msg, gr.update(choices=t_names_all), gr.update(value=None))
    except Exception as e: return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), f"<div style='color:red; font-weight:bold;'>❌ خطأ أثناء الرفع: {str(e)}</div>", gr.update(), gr.update())

def delete_department_data(dept_to_delete, current_day):
    global teachers_db
    if not dept_to_delete: return (gr.update(), gr.update(), gr.update(), gr.update(), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), "<div style='color:red; font-weight:bold;'>❌ الرجاء تحديد القسم أولاً.</div>", gr.update(), gr.update())
    teachers_to_delete = [t for t, d in teachers_db.items() if d.get("dept") == dept_to_delete]
    for t in teachers_to_delete: del teachers_db[t]
    save_db()
    t_names_all = sorted(list(teachers_db.keys()))
    msg = f"<div style='color:#c62828; background:#ffebee; padding:15px; border-radius:10px; border-right: 5px solid #c62828;'><b style='font-size:1.2em;'>🗑️ تمت عملية المسح بنجاح!</b><br>تم حذف جميع بيانات وسجلات معلمي قسم ({dept_to_delete}).</div>"
    return (gr.update(choices=["الكل"] + OFFICIAL_DEPTS), gr.update(choices=get_absentee_choices("الكل")), gr.update(choices=get_teacher_choices("الكل")), gr.update(choices=get_teacher_choices("الكل")), gr.update(value=get_updated_balance("الكل")), gr.update(value=get_updated_absences("الكل")), gr.update(value=get_day_overview(current_day, "الكل")), msg, gr.update(choices=t_names_all, value=None), gr.update(value=None))

def get_updated_balance(dept_filter="الكل"):
    data = [{"المعلم": format_teacher_name(t), "الرصيد": d["cover_count"]} for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter]
    return pd.DataFrame(data).sort_values("الرصيد", ascending=False) if data else pd.DataFrame(columns=["المعلم", "الرصيد"])

def get_updated_absences(dept_filter="الكل"):
    data = [{"المعلم": format_teacher_name(t), "مرات الغياب": d.get("absent_count", 0)} for t, d in teachers_db.items() if dept_filter == "الكل" or d.get("dept") == dept_filter]
    return pd.DataFrame(data).sort_values("مرات الغياب", ascending=False) if data else pd.DataFrame(columns=["المعلم", "مرات الغياب"])

def get_day_overview(day, dept_filter="الكل"):
    rows = [{"المعلم": format_teacher_name(t), **{f"ح {p}": d.get(day, {}).get(p, "-") for p in range(1, 8)}} for t, d in teachers_db.items() if (dept_filter == "الكل" or d.get("dept") == dept_filter) and d.get("dept") != "الهيئة الإدارية"]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["المعلم"] + [f"ح {p}" for p in range(1, 8)])

def get_teacher_weekly_schedule(teacher_name):
    if not teacher_name or teacher_name not in teachers_db or teachers_db[teacher_name].get("dept") == "الهيئة الإدارية": return pd.DataFrame(columns=["اليوم", "ح 1", "ح 2", "ح 3", "ح 4", "ح 5", "ح 6", "ح 7"])
    rows = [{"اليوم": day, **{f"ح {p}": teachers_db[teacher_name].get(day, {}).get(p, "-") for p in range(1, 8)}} for day in ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]]
    return pd.DataFrame(rows)

def get_dynamic_header(day_name):
    target_date = get_date_of_weekday(day_name)
    return f"<div style='background:#004d40; padding:15px; border-radius:10px; text-align:center;'><div style='font-size:1.4em; font-weight:bold; color:#ffffff !important;'>📅 {day_name} | {target_date}</div></div>"

def get_initial_header(): return get_dynamic_header(get_current_day_oman())

def draw_schedule_image(df, day_name):
    target_date = get_date_of_weekday(day_name)
    absent_list = df["المعلم الغائب"].unique().tolist()
    absent_str = "، ".join([str(x) for x in absent_list]) if absent_list else "لا يوجد"
    fig, ax = plt.subplots(figsize=(10, len(df)*0.8 + 3.0)); ax.axis('off') 
    header_text = f"اليوم: {day_name}  | التاريخ: {target_date}\nالمعلم الغائب: {absent_str}"
    plt.title(fix_arabic(header_text), loc='right', fontproperties=arabic_font, fontsize=14, color='#004d40', pad=25, fontweight='bold', linespacing=1.6)
    
    display_df = df[["المعلم البديل عرض", "الحصة", "الصف", "المعلم الغائب"]]
    table = ax.table(cellText=[[fix_arabic(v) for v in r] for r in display_df.values], colLabels=[fix_arabic(c) for c in ["المعلم البديل", "الحصة", "الصف", "المعلم الغائب"]], loc='center', cellLoc='center', colWidths=[0.35, 0.1, 0.2, 0.3])
    table.auto_set_font_size(False); table.set_fontsize(14); table.scale(1, 2.8)
    for (r, c), cell in table.get_celld().items():
        cell.set_text_props(fontproperties=arabic_font)
        if r == 0: cell.set_facecolor('#004d40'); cell.get_text().set_color('white')
        else: cell.set_facecolor('#ffebee' if "❌" in str(display_df.iloc[r-1]["المعلم البديل عرض"]) else ('#e0f2f1' if "🤝" in str(display_df.iloc[r-1]["المعلم البديل عرض"]) else ('#f1f8e9' if r%2==0 else 'white')))
    plt.savefig("output.png", bbox_inches='tight', dpi=150)
    plt.close('all') 
    return "output.png"

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
        elif "إشراف" in sub_teacher_display: bg_color, text_color, border_style = "#fff3e0", "#e65100", "border-bottom: 2px solid #ffcc80;"
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
    target_date = get_date_of_weekday(day_name)
    display_records = [r for r in daily_db if r["date"] == target_date and (dept == "الكل" or r["dept"] == dept)]
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
    summary = f"📊 ملخص احتياط اليوم: {day_name}\n👨‍🏫 المعلم الغائب: {absents_str}\n✨ تم توزيع حصص الاحتياط بنجاح عبر منظومة الباسط.. يعطيكم العافية جميعاً! 🏫"
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

def refresh_ui_on_change(dept, day_name, is_admin_logged_in, current_abs=None):
    target_date = get_date_of_weekday(day_name)
    display_records = [r for r in daily_db if r["date"] == target_date and (dept == "الكل" or r["dept"] == dept)]
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

    actual_abs = sorted(list(set([r["المعلم الغائب"] for r in display_records])))
    opts_abs = []
    
    if is_admin_logged_in:
        admin_title_val = "<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة العمليات الإدارية والقيادة العليا</h4><p style='text-align:center; color:#555; font-size:13px;'>صلاحيات مطلقة: يمكنك إسناد أي حصة لأي معلم، واعتماد التبادلات، ورصد التقصير.</p>"
        period_update = gr.update(choices=[], value=None, label="2️⃣ اختر الحصة", interactive=is_visible)
        cb_cross_update = gr.update(visible=False, value=False)
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == str(c).split(" (")[0].strip())
            radar_icon = " 🚨 " if has_admin_sup else " ✅ "
            opts_abs.append(f"{c} ({role} {radar_icon})" if role != "معلم" else f"{c} ({radar_icon})")
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
        period_update = gr.update(choices=[], value=None, label="2️⃣ الحصة المراد تعديلها", interactive=is_visible)
        cb_cross_update = gr.update(visible=True, value=False, interactive=True)
        for c in actual_abs:
            role = teachers_db.get(c, {}).get("role", "معلم")
            has_admin_sup = any(str(r.get("المعلم البديل", "")) == "إشراف إداري" for r in display_records if str(r.get("المعلم الغائب", "")).split(" (")[0].strip() == str(c).split(" (")[0].strip())
            radar_icon = " 🚨 " if has_admin_sup else " ✅ "
            opts_abs.append(f"{c} ({role} {radar_icon})" if role != "معلم" else f"{c} ({radar_icon})")
            
    t_names_filtered = sorted([t for t, d in teachers_db.items() if dept == "الكل" or d.get("dept") == dept])
    choices = get_teacher_choices(dept) 
    abs_choices = get_absentee_choices(dept)
    summary_txt, html_cards = generate_whatsapp_html(df, day_name, actual_abs) if not df.empty else ("", "<div style='text-align:center; color:gray; padding:20px;'>لا توجد تكليفات لعرضها</div>")
    styled_table_html = generate_styled_html_table(df)
    
    return (
        gr.update(choices=abs_choices, value=current_abs if current_abs is not None else actual_abs), 
        gr.update(value=get_updated_balance(dept)),      
        gr.update(value=get_updated_absences(dept)),     
        gr.update(value=get_day_overview(day_name, dept)), 
        gr.update(choices=t_names_filtered, value=None), 
        gr.update(choices=choices, value=None),          
        gr.update(choices=choices, value=None),          
        warning_html,                         
        gr.update(value=styled_table_html),              
        gr.update(choices=opts_abs, value=None),         
        df,                                         
        summary_txt,                                     
        html_cards,                                      
        get_dynamic_header(day_name),                    
        admin_title_val,                                 
        period_update,                           
        cb_cross_update,
        gr.update(interactive=is_visible),               
        gr.update(interactive=is_visible)                
    )

def assign_logic(absent_list, day_name, dept_filter, max_reserves, is_alt, is_admin_logged_in):
    global last_assigned_teachers, processed_absences, daily_db
    
    absent_list_clean = [a.split(" (")[0].strip() for a in absent_list] if absent_list else []
    
    target_date = get_date_of_weekday(day_name)
    daily_db = [row for row in daily_db if not (row["date"] == target_date and row["المعلم الغائب"] in absent_list_clean)]
    if not absent_list_clean or not day_name: return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in)
    
    if not is_alt:
        for abs_t in absent_list_clean:
            if (target_date, abs_t) not in processed_absences:
                if abs_t in teachers_db:
                    teachers_db[abs_t]["absent_count"] = teachers_db[abs_t].get("absent_count", 0) + 1
                    if "absence_dates" not in teachers_db[abs_t]: teachers_db[abs_t]["absence_dates"] = []
                    date_entry = f"{day_name} ({target_date})"
                    if date_entry not in teachers_db[abs_t]["absence_dates"] and target_date not in teachers_db[abs_t]["absence_dates"]:
                        teachers_db[abs_t]["absence_dates"].append(date_entry)
                processed_absences.add((target_date, abs_t))
                
    if is_alt and last_assigned_teachers:
        for t in last_assigned_teachers:
            if t in teachers_db: teachers_db[t]["cover_count"] = max(0, teachers_db[t]["cover_count"] - 1)

    res, current_assigned = [], []
    daily_assigned_count = {t: 0 for t in teachers_db}
    assigned_periods_today = {t: set() for t in teachers_db}
    for r in daily_db:
        if r["date"] == target_date and r["المعلم البديل"] != "إشراف إداري" and r.get("حالة_التكليف") != "تقصير":
            t = r["المعلم البديل"]
            if t in daily_assigned_count:
                daily_assigned_count[t] += 1
                assigned_periods_today[t].add(int(r["الحصة"]))

    for abs_t in absent_list_clean:
        abs_dept = teachers_db.get(abs_t, {}).get("dept", "عام") 
        for p_str, cl in teachers_db.get(abs_t, {}).get(day_name, {}).items():
            p_int = int(p_str)
            cands = []
            for t, t_info in teachers_db.items():
                if t in absent_list_clean: continue 
                if t_info.get("dept") != abs_dept: continue 
                if p_int in t_info.get(day_name, {}): continue 
                role = t_info.get("role", "معلم")
                if role in ADMIN_ROLES: continue 
                if p_int in assigned_periods_today[t]: continue 
                if daily_assigned_count[t] >= max_reserves: continue 
                if day_name in t_info.get("exempt_days", []): continue 
                if p_int in t_info.get("exempt_periods", []): continue 
                cands.append(t)
            if not cands: res.append({"المعلم الغائب": abs_t, "الصف": cl, "الحصة": str(p_int), "المعلم البديل": "إشراف إداري", "حالة_التكليف": ""})
            else:
                random.shuffle(cands)
                cands.sort(key=lambda t: teachers_db[t]["cover_count"]) 
                sel = cands[0]
                teachers_db[sel]["cover_count"] += 1
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
            x["date"]          == r["date"]          and
            x["المعلم الغائب"] == r["المعلم الغائب"] and
            x["الحصة"]         == r["الحصة"]
            for x in daily_db
        )
        if not is_dup:
            daily_db.append(r)
    save_daily_db()
    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=absent_list)
    
def cancel_teacher_absence(abs_t, day_name, dept_filter, is_admin_logged_in, current_abs):
    global daily_db, processed_absences, teachers_db
    if not abs_t or not day_name: return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)

    abs_t_clean = abs_t.split(" (")[0].strip()
    target_date = get_date_of_weekday(day_name)
    records_to_keep, records_to_delete = [], []

    for r in daily_db:
        if r["date"] == target_date and r["المعلم الغائب"] == abs_t_clean: records_to_delete.append(r)
        else: records_to_keep.append(r)

    daily_db = records_to_keep

    for r in records_to_delete:
        sub = str(r.get("المعلم البديل", "")).replace(" 🔄", "").replace("🔄", "").strip()
        status = r.get("حالة_التكليف", "")
        if sub != "إشراف إداري" and sub in teachers_db:
            if status == "": teachers_db[sub]["cover_count"] = max(0, teachers_db[sub].get("cover_count", 0) - 1)

    if abs_t_clean in teachers_db:
        teachers_db[abs_t_clean]["absent_count"] = max(0, teachers_db[abs_t_clean].get("absent_count", 0) - 1)
        date_entry = f"{day_name} ({target_date})"
        if "absence_dates" in teachers_db[abs_t_clean]:
            if date_entry in teachers_db[abs_t_clean]["absence_dates"]: teachers_db[abs_t_clean]["absence_dates"].remove(date_entry)
            elif target_date in teachers_db[abs_t_clean]["absence_dates"]: teachers_db[abs_t_clean]["absence_dates"].remove(target_date)
        
    if (target_date, abs_t_clean) in processed_absences: processed_absences.remove((target_date, abs_t_clean))

    save_db()
    save_daily_db()
    
    updated_abs = []
    if current_abs:
        updated_abs = [t for t in current_abs if t.split(" (")[0].strip() != abs_t_clean]
        
    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=updated_abs)

def on_abs_t_change(df_state, abs_t, is_admin_logged_in):
    if not abs_t or df_state is None or df_state.empty:
        cb_update = gr.update(visible=False, value=False) if is_admin_logged_in else gr.update(visible=True, value=False)
        return gr.update(choices=[], value=None), gr.update(choices=[], value=None), cb_update
    
    abs_t_clean = abs_t.split(" (")[0].strip()
    periods_elegant = []
    for _, row in df_state[df_state["المعلم الغائب"] == abs_t_clean].iterrows():
        elegant_class = format_elegant_class(row['الصف'])
        
        # 👇 التعديل المطلوب للرموز فقط
        is_admin_sup = str(row.get("المعلم البديل", "")) == "إشراف إداري"
        radar_icon = " 🚨 " if is_admin_sup else " ✅ "
        display_text = f"الحصة {row['الحصة']} - ({elegant_class}){radar_icon}"
        # 👆 نهاية التعديل
        
        periods_elegant.append(display_text)
        
    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    base_choice = f"نفس القسم ({abs_dept})"
    if is_admin_logged_in:
        choices = [base_choice, "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية", "معلمو الصف", "الهيئة التدريسية", "الهيئة الإدارية"]
        filtered = [c for c in choices if not (c in OFFICIAL_DEPTS and c == abs_dept)]
        return gr.update(choices=periods_elegant, value=None), gr.update(choices=filtered, value=base_choice), gr.update(visible=False, value=False)
    else:
        return gr.update(choices=periods_elegant, value=None), gr.update(choices=[base_choice], value=base_choice, interactive=False), gr.update(visible=True, value=False)
        
def toggle_cross_dept(is_checked, abs_t):
    if not abs_t: return gr.update()
    abs_t_clean = abs_t.split(" (")[0].strip()
    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    base_choice = f"نفس القسم ({abs_dept})"
    if is_checked:
        choices = [base_choice, "التربية الإسلامية", "اللغة العربية", "الرياضيات", "العلوم", "اللغة الإنجليزية", "الدراسات الإجتماعية", "المهارات الفردية", "معلمو الصف", "الهيئة التدريسية"]
        filtered = [c for c in choices if not (c in OFFICIAL_DEPTS and c == abs_dept)]
        return gr.update(choices=filtered, value=base_choice, interactive=True)
    else:
        return gr.update(choices=[base_choice], value=base_choice, interactive=False)

def update_available_subs_smart(abs_t, period, intervention_type, day_name, df_state, is_admin):
    # 🛡️ تحديد الخيار الافتراضي في حالة الخطأ أو الفراغ بناءً على صلاحية المستخدم
    fallback = [] if is_admin else ["إشراف إداري"]

    if not abs_t or not period or not day_name or not intervention_type: return gr.update(choices=fallback, value=None)
    
    try: 
        p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
        p_int = int(p_str_clean)
    except: return gr.update(choices=fallback, value=None)
    
    abs_t_clean = abs_t.split(" (")[0].strip()
    target_date = get_date_of_weekday(day_name)
    already_subbing, absent_today = set(), set()
    if df_state is not None and not df_state.empty:
        subs = df_state[df_state["الحصة"] == str(p_int)]["المعلم البديل"].tolist()
        already_subbing.update(subs)
        absent_today.update(df_state["المعلم الغائب"].tolist())
    
    # ✅ استبعاد المعلمين المكلفين في نفس الحصة من جميع الأقسام
    for r in daily_db:
        if r["date"] == target_date and r["الحصة"] == str(p_int) and r.get("حالة_التكليف") != "تقصير":
            already_subbing.add(r["المعلم البديل"]) 
    
    abs_dept = teachers_db.get(abs_t_clean, {}).get("dept", "عام")
    target_dept = intervention_type
    if "نفس القسم" in target_dept: target_dept = abs_dept
    
    opts = []
    
    # 🚀 الهيئة التدريسية (يستبعد الإداريين)
    if target_dept == "الهيئة التدريسية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today: continue 
            if info.get("dept") == "الهيئة الإدارية": continue
            if p_int not in info.get(day_name, {}):
                available_cands.append(t)
                
        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            c_dept = teachers_db[c].get("dept", "عام")
            warn_str = check_teacher_load(c, day_name, p_int)
            warn_str = f" ⚠️ {warn_str}" if warn_str else ""
            opts.append(f"{c} ({c_dept}){warn_str}")
            
        # 🛡️ الحجب هنا
        if not is_admin: opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None)

    # 🚀 الهيئة الإدارية (خاص بالمدير)
    if target_dept == "الهيئة الإدارية":
        available_cands = []
        for t, info in teachers_db.items():
            if t == abs_t_clean or t in already_subbing or t in absent_today: continue 
            if info.get("dept") == "الهيئة الإدارية":
                if p_int not in info.get(day_name, {}):
                    available_cands.append(t)
                    
        available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
        for c in available_cands:
            role = teachers_db[c].get("role", "إداري")
            opts.append(f"{c} ({role})")
            
        # 🛡️ الحجب هنا
        if not is_admin: opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None)
    
    # --- بقية الأقسام ومعلمو الصف ---
    falcon_cands = get_falcon_eye_candidates(abs_t_clean, period, day_name)
    
    if target_dept != abs_dept:
        for cand_str in falcon_cands:
            name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
            if name_part not in already_subbing and name_part not in absent_today:
                if target_dept == "معلمو الصف" or teachers_db.get(name_part, {}).get("dept") == target_dept:
                    opts.append(cand_str)
        # 🛡️ الحجب هنا
        if not opts and not is_admin: opts.append("إشراف إداري")
        return gr.update(choices=opts, value=None)
        
    for cand_str in falcon_cands:
        name_part = cand_str.split(" (يدرس")[0].replace("🦅 ", "").strip()
        if name_part not in already_subbing and name_part not in absent_today and teachers_db.get(name_part, {}).get("dept") == abs_dept:
            opts.append(cand_str)
            
    available_cands = []
    for t, info in teachers_db.items():
        if t == abs_t_clean or t in already_subbing or t in absent_today: continue 
        if p_int not in info.get(day_name, {}):
            if info.get("dept") == target_dept: available_cands.append(t)

    available_cands.sort(key=lambda x: teachers_db[x].get("cover_count", 0))
    for c in available_cands:
        is_falcon = False
        for opt in opts:
            if c in opt:
                is_falcon = True
                break
        if is_falcon: continue 
        
        warn_str = check_teacher_load(c, day_name, p_int)
        warn_str = f" ⚠️ {warn_str}" if warn_str else ""
        opts.append(f"{c} ({abs_dept}){warn_str}")
        
    # 🛡️ الحجب هنا
    if not is_admin: opts.append("إشراف إداري")
    return gr.update(choices=opts, value=None)
    
def process_admin_action(df_state, abs_t, period, new_sub, day_name, dept_filter, is_admin_logged_in, current_abs, action_type):
    global daily_db
    if df_state is None or df_state.empty or not abs_t or not period: return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
    target_date = get_date_of_weekday(day_name)
    
    abs_t_clean = abs_t.split(" (")[0].strip()
    p_str_clean = str(period).split("-")[0].replace("الحصة", "").strip()
    
    for r in daily_db:
        if r["date"] == target_date and r["المعلم الغائب"] == abs_t_clean and r["الحصة"] == p_str_clean:
            old_sub = r["المعلم البديل"]
            old_status = r.get("حالة_التكليف", "")
            
            if action_type == "penalty": target_sub = old_sub
            else:
                if not new_sub: return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
                if new_sub == "إشراف إداري": target_sub = new_sub
                else: target_sub = new_sub.split(" (")[0].replace("🦅 ", "").strip()
                
            if old_sub == target_sub and action_type == "normal" and old_status == "": break
            
            if old_sub != "إشراف إداري" and old_sub in teachers_db:
                if old_status == "": teachers_db[old_sub]["cover_count"] = max(0, teachers_db[old_sub].get("cover_count", 0) - 1)
                
            if action_type == "penalty":
                if target_sub != "إشراف إداري" and old_status != "تقصير" and target_sub in teachers_db:
                    teachers_db[target_sub]["shortcoming_count"] = teachers_db[target_sub].get("shortcoming_count", 0) + 1
                r["المعلم البديل"] = target_sub 
                r["حالة_التكليف"] = "تقصير"
                
            elif action_type == "tabadul":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = "تبادل"
                
            elif action_type == "normal":
                r["المعلم البديل"] = target_sub
                r["حالة_التكليف"] = ""
                if target_sub != "إشراف إداري" and target_sub in teachers_db:
                    teachers_db[target_sub]["cover_count"] = teachers_db[target_sub].get("cover_count", 0) + 1
                    
            save_db()
            save_daily_db()
            break
    return refresh_ui_on_change(dept_filter, day_name, is_admin_logged_in, current_abs=current_abs)
    
def load_teacher_data_for_edit(selected_teacher):
    if selected_teacher and selected_teacher in teachers_db: 
        dept = teachers_db[selected_teacher].get("dept", "عام")
        spec = teachers_db[selected_teacher].get("specialty", "")
        role = teachers_db[selected_teacher].get("role", "معلم")
        is_admin_staff = dept == "الهيئة الإدارية"
        is_spec_visible = dept in ["العلوم", "المهارات الفردية"]
        return (
            gr.update(value=dept, visible=not is_admin_staff),
            gr.update(value=teachers_db[selected_teacher].get("cover_count", 0)),
            gr.update(value=teachers_db[selected_teacher].get("absent_count", 0)),
            gr.update(value=teachers_db[selected_teacher].get("shortcoming_count", 0)),
            gr.update(value=teachers_db[selected_teacher].get("phone", "")),
            gr.update(value=spec, visible=is_spec_visible and not is_admin_staff),
            gr.update(value=role)
        )
    return gr.update(value="", visible=True), gr.update(value=0), gr.update(value=0), gr.update(value=0), gr.update(value=""), gr.update(value=""), gr.update(value="معلم")
    
def toggle_specialty_visibility(dept): return gr.update(visible=dept in ["العلوم", "المهارات الفردية"])

def update_manual_count(name, new_val, new_abs_val, new_short_val, new_phone, new_specialty, new_role, dept_filter, day_val, df_state, abs_in_list, is_admin_logged_in):
    if name and name in teachers_db:
        if new_val is not None: 
            try: teachers_db[name]["cover_count"] = int(new_val)
            except: pass
        if new_abs_val is not None:
            try: teachers_db[name]["absent_count"] = int(new_abs_val)
            except: pass
        if new_short_val is not None:
            try: teachers_db[name]["shortcoming_count"] = int(new_short_val)
            except: pass
        if new_phone is not None:
            phone_clean = re.sub(r'\D', '', str(new_phone))
            if phone_clean:
                if len(phone_clean) == 8: phone_clean = "968" + phone_clean
                teachers_db[name]["phone"] = phone_clean
            else: teachers_db[name]["phone"] = ""
        if new_specialty is not None: teachers_db[name]["specialty"] = str(new_specialty).strip()
        if new_role is not None: teachers_db[name]["role"] = str(new_role).strip() 
        save_db()
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم حفظ التعديلات للأستاذ ({name}) بنجاح!</div>", gr.update(choices=abs_choices), gr.update(choices=choices_all), gr.update(choices=choices_all))
    return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), "<div style='color:red;'>❌ لم يتم الحفظ</div>", gr.update(), gr.update(), gr.update())

def delete_single_teacher(name, dept_filter, day_val):
    global teachers_db
    if name and name in teachers_db:
        del teachers_db[name]
        save_db()
        choices_all = get_teacher_choices(dept_filter)
        abs_choices = get_absentee_choices(dept_filter)
        msg = f"<div style='color:#c62828; font-weight:bold; background:#ffebee; padding:10px; border-radius:5px; text-align:center;'>🗑️ تم حذف ({name}) نهائياً من النظام!</div>"
        return (gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), msg, gr.update(choices=abs_choices), gr.update(choices=choices_all, value=None), gr.update(choices=choices_all, value=None), gr.update(choices=list(teachers_db.keys()), value=None))
    return (gr.update(), gr.update(), gr.update(), "<div style='color:red;'>❌ المعلم غير موجود</div>", gr.update(), gr.update(), gr.update(), gr.update())

def load_teacher_rules(t_name):
    if t_name and t_name in teachers_db: return gr.update(value=teachers_db[t_name].get("exempt_days", [])), gr.update(value=teachers_db[t_name].get("exempt_periods", []))
    return gr.update(value=[]), gr.update(value=[])

def save_teacher_rules(t_name, days, periods):
    if t_name and t_name in teachers_db:
        teachers_db[t_name]["exempt_days"] = days
        teachers_db[t_name]["exempt_periods"] = [int(p) for p in periods]
        save_db()
        return f"<div style='color:#2e7d32; font-weight:bold; background:#e8f5e9; padding:10px; border-radius:5px; text-align:center;'>✅ تم تثبيت القوانين للأستاذ ({t_name}) بنجاح!</div>"
    return ""

def export_excel_report(dept_filter):
    data = []
    for t, d in teachers_db.items():
        if dept_filter == "الكل" or d.get("dept") == dept_filter:
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

def reset_monthly_balances(dept_filter, day_val):
    global daily_db
    for t in teachers_db:
        teachers_db[t]["cover_count"] = 0
        teachers_db[t]["absent_count"] = 0
        teachers_db[t]["absence_dates"] = [] 
        teachers_db[t]["shortcoming_count"] = 0 
    save_db()
    daily_db = []
    save_daily_db()
    msg = "<div style='color:#1565c0; font-weight:bold; background:#e3f2fd; padding:15px; border-radius:10px; text-align:center; margin-bottom:10px;'>✅ تم إقفال الشهر بنجاح! جميع الأرصدة والتواريخ وحالات التقصير عادت للصفر.</div>"
    return gr.update(value=get_updated_balance(dept_filter)), gr.update(value=get_updated_absences(dept_filter)), gr.update(value=get_day_overview(day_val, dept_filter)), msg

def clear_all_data():
    global teachers_db, daily_db
    teachers_db = {}
    daily_db = []
    if os.path.exists(DB_FILE): os.remove(DB_FILE)
    if os.path.exists(DAILY_DB_FILE): os.remove(DAILY_DB_FILE)
    return (gr.update(choices=["الكل"] + OFFICIAL_DEPTS, value="الكل"), gr.update(choices=[]), gr.update(choices=[]), gr.update(choices=[]), gr.update(value=pd.DataFrame(columns=["المعلم", "الرصيد"])), gr.update(value=pd.DataFrame(columns=["المعلم", "مرات الغياب"])), gr.update(value=pd.DataFrame(columns=["المعلم"] + [f"ح {p}" for p in range(1, 8)])), "<div style='color:orange; font-weight:bold;'>⚠️ تم تصفير المنظومة بالكامل.</div>", gr.update(choices=[]), gr.update(value=None), gr.update(value="", visible=False))

def attempt_login(pin, day_val):
    if pin in AUTH_DB:
        user_info = AUTH_DB[pin]
        role = user_info["role"]
        dept = user_info["dept"]
        name = user_info.get("name", "")  
        is_admin = (dept == "الكل")
        
        raw_msg = WELCOME_MESSAGES.get(role, "مرحباً بك ({name}) في النظام.")
        welcome_msg = f"<div style='background:#004d40; color:#ffca28; padding:15px; border-radius:10px; text-align:center; font-size:18px; font-weight:bold; margin-bottom:15px;'>{raw_msg.format(name=name)}</div>"
        
        if is_admin: up_dept_update, manual_entry_visibility = gr.update(interactive=True), gr.update(visible=True) 
        else: up_dept_update, manual_entry_visibility = gr.update(value=dept, interactive=False), gr.update(visible=False) 
            
        updates = refresh_ui_on_change(dept, day_val, is_admin)
        
        return [gr.update(visible=False), gr.update(visible=True), welcome_msg, gr.update(value=dept, interactive=is_admin), gr.update(value=""), up_dept_update, manual_entry_visibility, is_admin] + list(updates) + [gr.update(visible=dept in ["العلوم", "المهارات الفردية"])]     
        
    else:
        gr.Warning("❌ رمز الدخول غير صحيح! الرجاء المحاولة مرة أخرى.")
        error_updates = [gr.update()]*19
        return [gr.update(), gr.update(), "<div style='color:red; text-align:center; font-weight:bold; margin-top:10px;'>❌ رمز الدخول غير صحيح! حاول مرة أخرى.</div>", gr.update(), gr.update(), gr.update(), gr.update(), False] + error_updates + [gr.update()]

def do_logout(): 
    return gr.update(visible=True), gr.update(visible=False), "", gr.update(value="الكل"), False, None, None, gr.update(visible=False, value=False)
    
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
h1, h2, p, div { color: inherit; }
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
@keyframes pulse { 0% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0.4); } 70% { box-shadow: 0 0 0 10px rgba(198, 40, 40, 0); } 100% { box-shadow: 0 0 0 0 rgba(198, 40, 40, 0); } }
"""

js_code = """
function() {
    const removeDark = () => { document.documentElement.classList.remove('dark'); document.body.classList.remove('dark'); document.documentElement.setAttribute('data-theme', 'light'); document.body.style.backgroundColor = '#ffffff'; };
    removeDark();
    const observer = new MutationObserver(removeDark);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] }); observer.observe(document.body, { attributes: true, attributeFilter: ['class'] });
}
"""

header_html = """
<div class='main-header'>
    <div class='header-grid'>
        <div class='h-logo'><img src='https://i.imgur.com/1cxFlX7.png' alt='Logo'></div>
        <div class='h-ministry'>وزارة التعليم<br>المديرية العامة للتعليم بمحافظة<br>جنوب الباطنة</div>
        <div class='h-title'>🏫 منظومة الباسط الشاملة للاحتياط 📊</div>
        <div class='h-school' style='color: #ffca28 !important; -webkit-text-fill-color: #ffca28 !important; white-space: nowrap;'>مدرسة الباسط للتعليم الأساسي (8-10)</div>
        <div class='h-credits'><div class='credits-box'>👑 فكرة وتطوير: أ. محمود اليحيائي - أ. وليد الهنائي</div></div>
    </div>
</div>
"""

def filter_swap_teachers_safe(dept):
    try:
        valid_t = [t for t, d in teachers_db.items() if d.get("dept") != "الهيئة الإدارية" and d.get("role") != "إداري"]
        if dept != "الكل": 
            valid_t = [t for t in valid_t if teachers_db.get(t, {}).get("dept") == dept]
        if not valid_t: return gr.update(choices=["لا يوجد معلمون"], value=None)
        return gr.update(choices=valid_t, value=None)
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

def run_radar_safe(t, p, d):
    default_msg = "💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا..."
    try:
        if not t or not p or "لا يوجد" in t or "اختر" in p: return gr.update(choices=[], value=None), gr.update(value=default_msg), gr.update(value="")
        
        p_str_clean = str(p).split("-")[0].replace("الحصة", "").strip()
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
            
            for db in ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"]:
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
        import urllib.parse
        parts = choice.split("|")
        t_target = parts[1].split(":")[1].strip()
        details = parts[2].strip()
        
        p_req_clean = str(p_req).split("-")[0].replace("الحصة", "").strip()
        req_class_raw = teachers_db.get(t_req, {}).get(d_req, {}).get(p_req_clean, teachers_db.get(t_req, {}).get(d_req, {}).get(int(p_req_clean) if p_req_clean.isdigit() else p_req_clean, ""))
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
        wa_link = f"https://wa.me/{phone}?text={encoded_msg}"
        
        btn_html = f'<div style="margin-top: 10px; border: 2px solid {btn_color}; border-radius: 8px; padding: 2px;"><a href="{wa_link}" target="_blank" style="display: block; width: 100%; text-align: center; background-color: {btn_color}; color: white; padding: 12px; border-radius: 6px; font-weight: bold; text-decoration: none; font-size: 16px;">{btn_text}</a></div>'
        
        return gr.update(value=msg), gr.update(value=btn_html)

    except Exception as e:
        return gr.update(value=default_msg), gr.update(value="")


# ================================================================
# واجهة Gradio الرئيسية — كل شيء داخل كتلة واحدة
# ================================================================
with gr.Blocks(css=css, js=js_code) as app:
    current_user_is_admin = gr.State(value=False)
    current_schedule_state = gr.State()

    with gr.Column(visible=True, elem_classes="login-box") as login_container:
        gr.HTML("""<div style='text-align: center; margin-top: -75px; margin-bottom: 15px; position: relative; z-index: 10;'><img src='https://i.imgur.com/1cxFlX7.png' style='width: 130px; height: 130px; object-fit: contain; border-radius: 50%; box-shadow: 0 4px 10px rgba(0,0,0,0.15); border: 3px solid #004d40; background-color: white; display: inline-block; margin: 0 auto;'></div><h2 style='text-align:center; color:#004d40; margin-bottom: 5px; font-weight: 900; font-size: 28px; margin-top: 10px;'>🏰 بوابة الدخول</h2><p style='text-align:center; color:#004d40 !important; -webkit-text-fill-color: #004d40 !important; margin-top: 0; font-size: 15px !important; font-weight: bold; white-space: nowrap;'>مدرسة الباسط للتعليم الأساسي (8-10)</p>""")
        pin_input = gr.Textbox(type="password", show_label=False, placeholder="Enter ثم اضغط (PIN) 🔑 أدخل رمز الدخول", text_align="center")
        login_btn = gr.Button("تسجيل الدخول", elem_classes="admin-btn")
        login_msg = gr.HTML()
        gr.HTML("<div style='text-align:center;'><div class='credits-box' style='font-size: 10px; padding: 5px 10px;'>👑 فكرة وتطوير: أ. محمود اليحيائي - أ. وليد الهنائي © 2026</div></div>")

    with gr.Column(visible=False) as main_app_container:
        gr.HTML(header_html)
        
        with gr.Row():
            with gr.Column(scale=5): welcome_html = gr.HTML()
            with gr.Column(scale=1, min_width=120): logout_btn = gr.Button("🚪 خروج و إقفال", elem_classes="reset-btn")
        
        with gr.Row(elem_classes="yellow-box"):
            dept_in = gr.Dropdown(["الكل"] + OFFICIAL_DEPTS, label="📂 مركز التحكم", value="الكل", scale=2)
            day_in = gr.Dropdown(["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"], label="📅 اختر اليوم الدراسي", value=get_current_day_oman(), scale=2)
            refresh_btn = gr.Button("🔄 تحديث الشاشة والبيانات", elem_classes="refresh-btn", scale=1)

        with gr.Tabs():
            with gr.Tab("🗂️ التهيئة والرفع"):
                gr.Markdown("### 📥 1. رفع جداول المعلمين (.xlsx)")
                with gr.Row():
                    up_dept = gr.Dropdown(OFFICIAL_DEPTS[1:], label="حدد القسم لهذا الملف", value="التربية الإسلامية")
                    excel_upload = gr.File(label="ارفع ملف الإكسل")
                with gr.Row():
                    upload_btn = gr.Button("➕ إضافة/تحديث معلمي القسم", variant="primary", elem_classes="action-btn")
                    delete_dept_btn = gr.Button("🗑️ مسح بيانات هذا القسم", elem_classes="reset-btn")
                excel_status_html = gr.HTML()
                
                gr.Markdown("### 📱 2. رفع أرقام الواتساب")
                with gr.Row(): phone_excel_upload = gr.File(label="ارفع ملف الأرقام")
                with gr.Row(): upload_phone_btn = gr.Button("📲 ربط الأرقام بالرادار الذكي", elem_classes="admin-btn")
                phone_status_html = gr.HTML()
                    
                with gr.Column(visible=False) as manual_entry_container:
                    gr.Markdown("### 👨‍💼 3. الإدخال اليدوي للطاقم الإداري")
                    with gr.Row(elem_classes="yellow-box"):
                        manual_name = gr.Textbox(label="الاسم الثلاثي")
                        manual_dept = gr.Dropdown(["الهيئة الإدارية"], label="القسم", value="الهيئة الإدارية", interactive=False)
                        manual_role = gr.Dropdown(ADMIN_ROLES, label="المنصب", value="أخصائي اجتماعي")
                        manual_phone = gr.Textbox(label="رقم الواتساب")
                    with gr.Row(): manual_add_btn = gr.Button("➕ حفظ وإضافة", elem_classes="admin-btn")
                manual_status_html = gr.HTML()
                
                clear_status_html = gr.HTML()
                clear_btn = gr.Button("🧨 مسح وتصفير المنظومة", elem_classes="reset-btn")
                
            with gr.Tab("🛡️ حالات الإعفاء"):
                gr.Markdown("### 🚫 تثبيت الإعفاءات الدائمة")
                with gr.Column(elem_classes="shield-box"):
                    rule_teacher = gr.Dropdown(list(teachers_db.keys()), label="👨‍🏫 اختر المعلم المراد إعفاؤه")
                    with gr.Row():
                        rule_days = gr.CheckboxGroup(["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"], label="📅 أيام معفى منها")
                        rule_periods = gr.CheckboxGroup([1, 2, 3, 4, 5, 6, 7, 8], label="⏱️ حصص معفى منها")
                    rule_save_btn = gr.Button("✅ حفظ قوانين هذا المعلم", elem_classes="admin-btn")
                    rule_status = gr.HTML()

            with gr.Tab("📋 التوزيع والاحتياط"):
                with gr.Column():
                    with gr.Accordion("⚙️ ضوابط التوزيع اليومية", open=True, elem_classes="yellow-box"):
                        max_reserves_input = gr.Number(value=1, label="🛑 الحد الأقصى للاحتياط لكل معلم في اليوم", precision=0)
                    
                    radar_warning_html = gr.HTML()
                    abs_in = gr.Dropdown([], label="👨‍🏫 حدد المعلمين الغائبين", multiselect=True, elem_classes="absent-box")
                    
                    with gr.Row():
                        btn = gr.Button("🚀 توليد وتوزيع الاحتياط", variant="primary", elem_classes="action-btn")
                        btn_alt = gr.Button("🪄 مقترح آخر", interactive=False, elem_classes="action-btn")
                        btn_img = gr.Button("🖼️ تحميل الجدول كصورة", interactive=False, elem_classes="export-btn")
                        
                    date_display = gr.HTML(get_initial_header)
                    img_out = gr.Image(label="الصورة الجاهزة للنسخ", interactive=False)
                    tbl_out = gr.HTML(value="")
                    
                    with gr.Column(elem_classes="whatsapp-box"):
                        gr.Markdown("## 📱 مركز التواصل الذكي ومهام الواتساب")
                        with gr.Row(): msg_summary = gr.Textbox(label="📊 تقرير الجروب الإداري", lines=4, interactive=True)
                        with gr.Row(): msg_individual_html = gr.HTML(label="💌 بطاقات التكليف الفردية")

                    with gr.Accordion("⚙️ لوحة القائد: التعديل اليدوي والتبادل", open=False):
                        with gr.Column(elem_classes="admin-zone"):
                            admin_zone_title = gr.HTML("<h4 style='color:#004d40; text-align:center; margin-top:0;'>🛠️ غرفة العمليات والقيادة</h4>")
                            gr.HTML("<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b;'>💡 <b>توضيح:</b> للتراجع عن الغياب اختر المعلم الغائب ثم اضغط <b>التراجع عن غياب اليوم بالكامل</b>.. لعمل <b>اعتماد كتبادل أو تكليف احتياط رسمي</b> اختر المعلم الغائب ثم الحصة واختر المعلم المنقذ من نفس القسم أو فعّل التعاون مع قسم آخر للاختيار من الأقسام الأخرى.. لعمل <b>رصد تقصير في التكليف</b> اختر المعلم الغائب ثم اختر الحصة ثم اضغط أيقونة رصد تقصير في التكليف.</div>")
                            
                            with gr.Row():
                                edit_abs_t = gr.Dropdown([], label="1️⃣ المعلم الغائب", allow_custom_value=True)
                                edit_period = gr.Dropdown([], label="2️⃣ اختر الحصة", allow_custom_value=False)
                                edit_intervention_type = gr.Dropdown([], label="3️⃣ نطاق البحث عن بديل (تلقائي ذكي)", allow_custom_value=True)
                            
                            with gr.Row():
                                cb_cross_dept = gr.Checkbox(label="🔓 تفعيل التعاون مع قسم آخر 🤝", visible=False)
                            
                            with gr.Row():
                                edit_new_sub = gr.Dropdown([], label="4️⃣ البديل المنقذ", allow_custom_value=True)
                            with gr.Row():
                                btn_apply_override = gr.Button("✍🏻 تكليف احتياط رسمي", elem_classes="admin-btn")
                                btn_apply_tabadul = gr.Button("🤝 اعتماد كـ تبادل", elem_classes="tabadul-btn")
                                btn_apply_penalty = gr.Button("🚨 رصد تقصير في التكليف", elem_classes="reset-btn")
                            
                            with gr.Row():
                                btn_cancel_absence = gr.Button("⏪ التراجع عن غياب اليوم بالكامل", elem_classes="reset-btn")

            with gr.Tab("🤝 التبادل الودي الأسبوعي"):
                gr.Markdown("### ⏳ رادار المقايضة (للاتفاقيات الودية بين المعلمين الحاضرين)")
                with gr.Row(elem_classes="yellow-box"):
                    swap_day = gr.Dropdown(["الأحد", "الإثنين", "الثلاثاء", "الأربعاء", "الخميس"], label="1️⃣ اليوم", value=get_current_day_oman())
                    swap_dept = gr.Dropdown(["الكل"] + [d for d in OFFICIAL_DEPTS if d != "الهيئة الإدارية"], label="2️⃣ القسم", value="الكل")
                    swap_t1 = gr.Dropdown(list(teachers_db.keys()), label="3️⃣ المعلم الطالب للتبادل", allow_custom_value=False)
                    swap_p1 = gr.Dropdown([], label="4️⃣ الحصة المراد مبادلتها", allow_custom_value=False)
                
                btn_run_radar = gr.Button("🚀 تشغيل الرادار والبحث عن بدائل الآن", variant="primary", visible=False)

                swap_options = gr.Radio(label="5️⃣ الخيارات المتاحة (اختر المعلم الذي يناسبك لتوليد الرسالة 💬)", choices=[])
                whatsapp_msg = gr.Textbox(label="💬 معاينة رسالة الواتساب التلقائية (يمكنك التعديل عليها)", lines=6, interactive=True, value="💡 يرجى اختيار أحد المعلمين من القائمة بالأعلى لتوليد مسودة رسالة الواتساب هنا...")
                wa_html_btn = gr.HTML(value="")

                gr.HTML("<div style='color:#00695c; background:#e0f2f1; padding:15px; border-radius:8px; border-right: 4px solid #00897b;'>💡 <b>توضيح:</b> اختر المعلم والحصة، وسيقوم <b>( الرادار بالعمل)</b> لضمان جلب النتائج بدقة.</div>")

            with gr.Tab("⚖️ الأرصدة والتقارير"):
                monthly_status = gr.HTML()

                with gr.Row(elem_classes="yellow-box"):
                    export_btn = gr.Button("📥 تصدير تقرير المدرسة (Excel)", elem_classes="export-btn")
                    reset_month_btn = gr.Button("🔄 إقفال الشهر (تصفير الأرصدة فقط)", elem_classes="reset-btn")
                
                report_file = gr.File(label="📥 التقرير الجاهز للتحميل")

                with gr.Row():
                    with gr.Column():
                        gr.HTML("<h3 style='text-align:center; color:#004d40; font-size: 1.3em; font-weight: 900; margin-bottom: 10px;'>🟢 رصيد الاحتياط</h3>")
                        tbl_bal = gr.Dataframe(headers=["المعلم", "الرصيد"], interactive=False)
                    with gr.Column():
                        gr.HTML("<h3 style='text-align:center; color:#c62828; font-size: 1.3em; font-weight: 900; margin-bottom: 10px;'>🔴 حصر الغياب</h3>")
                        tbl_abs = gr.Dataframe(headers=["المعلم", "مرات الغياب"], interactive=False)
                
                with gr.Accordion("🔒 الخزنة: تعديل يدوي للأرصدة والهواتف", open=False, elem_classes="yellow-box"):
                    with gr.Row():
                        t_name = gr.Dropdown(list(teachers_db.keys()), label="المعلم")
                        t_dept_edit = gr.Textbox(label="القسم / المادة (للعرض فقط)", interactive=False)
                        t_role_edit = gr.Dropdown(ALL_ROLES, label="المنصب الإشرافي")
                    with gr.Row():
                        t_phone_edit = gr.Textbox(label="رقم الهاتف (الواتساب)")
                        t_specialty_edit = gr.Dropdown(
                            choices=["فيزياء", "كيمياء", "أحياء", "تقنية المعلومات",
                                     "الفنون التشكيلية", "الرياضة المدرسية",
                                     "المهارات الحياتية", "المهارات الموسيقية"],
                            label="التخصص الدقيق",
                            visible=False,
                            allow_custom_value=True
                        )
                    with gr.Row():
                        t_val = gr.Number(label="رصيد الاحتياط")
                        t_abs_val = gr.Number(label="مرات الغياب")
                        t_short_val = gr.Number(label="حالات التقصير")
                        t_btn = gr.Button("✅ حفظ التعديلات", elem_classes="admin-btn")
                        t_del_btn = gr.Button("🗑️ حذف السجل", elem_classes="reset-btn")
                    vault_status = gr.HTML()
                            
            with gr.Tab("📅 جدول اليوم"): tbl_day = gr.Dataframe(headers=["المعلم", "ح 1", "ح 2", "ح 3", "ح 4", "ح 5", "ح 6", "ح 7"], interactive=False)
            with gr.Tab("🔍 جدول المعلم"):
                gr.Markdown("### 🧐 شاشة التدقيق")
                check_teacher_in = gr.Dropdown(list(teachers_db.keys()), label="👨‍🏫 اختر المعلم")
                check_tbl = gr.Dataframe(headers=["اليوم", "ح 1", "ح 2", "ح 3", "ح 4", "ح 5", "ح 6", "ح 7"], interactive=False)
                check_teacher_in.change(get_teacher_weekly_schedule, check_teacher_in, check_tbl)

    # ── ربط الأحداث ──────────────────────────────────────────────
    update_outputs = [
        abs_in, tbl_bal, tbl_abs, tbl_day, t_name, check_teacher_in, rule_teacher, 
        radar_warning_html, tbl_out, edit_abs_t, current_schedule_state, 
        msg_summary, msg_individual_html, date_display, admin_zone_title, 
        edit_period, cb_cross_dept, btn_alt, btn_img
    ]

    login_btn.click(attempt_login, inputs=[pin_input, day_in], outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin] + update_outputs + [t_specialty_edit])
    pin_input.submit(attempt_login, inputs=[pin_input, day_in], outputs=[login_container, main_app_container, welcome_html, dept_in, login_msg, up_dept, manual_entry_container, current_user_is_admin] + update_outputs + [t_specialty_edit])
    logout_btn.click(do_logout, inputs=[], outputs=[login_container, main_app_container, welcome_html, dept_in, current_user_is_admin, current_schedule_state, img_out, cb_cross_dept]).then(None, None, None, js="() => { setTimeout(() => { window.location.reload(); }, 300); }")
    
    update_trigger = [dept_in, day_in, current_user_is_admin]
    dept_in.change(
    lambda d, dy, adm: refresh_ui_on_change(d, dy, adm) + (gr.update(visible=d in ["العلوم", "المهارات الفردية"]),),
    update_trigger,
    update_outputs + [t_specialty_edit]
)
    day_in.change(lambda d, dy, adm: refresh_ui_on_change(d, dy, adm), update_trigger, update_outputs)
    refresh_btn.click(force_refresh_data, [dept_in, day_in, current_user_is_admin, abs_in], update_outputs)
    btn_img.click(generate_image_only, [dept_in, day_in], [img_out])
    
    upload_btn.click(process_uploaded_excel, [excel_upload, up_dept, day_in], [dept_in, abs_in, check_teacher_in, rule_teacher, tbl_bal, tbl_abs, tbl_day, excel_status_html, t_name, excel_upload])
    delete_dept_btn.click(delete_department_data, [up_dept, day_in], [dept_in, abs_in, check_teacher_in, rule_teacher, tbl_bal, tbl_abs, tbl_day, excel_status_html, t_name, excel_upload])
    upload_phone_btn.click(process_phone_excel, [phone_excel_upload], [phone_status_html, phone_excel_upload])
    manual_add_btn.click(add_manual_staff, [manual_name, manual_dept, manual_phone, manual_role, dept_in], [manual_status_html, abs_in, check_teacher_in, rule_teacher, t_name, manual_name, manual_phone])
    
    clear_btn.click(clear_all_data, None, [dept_in, abs_in, check_teacher_in, rule_teacher, tbl_bal, tbl_abs, tbl_day, clear_status_html, t_name, excel_upload, tbl_out])
    
    rule_teacher.change(load_teacher_rules, rule_teacher, [rule_days, rule_periods])
    rule_save_btn.click(save_teacher_rules, [rule_teacher, rule_days, rule_periods], rule_status)
    
    btn.click(lambda a, d, dp, mr, adm: assign_logic(a, d, dp, mr, False, adm), [abs_in, day_in, dept_in, max_reserves_input, current_user_is_admin], update_outputs)
    btn_alt.click(lambda a, d, dp, mr, adm: assign_logic(a, d, dp, mr, True, adm), [abs_in, day_in, dept_in, max_reserves_input, current_user_is_admin], update_outputs)
    
    edit_abs_t.change(on_abs_t_change, [current_schedule_state, edit_abs_t, current_user_is_admin], [edit_period, edit_intervention_type, cb_cross_dept])
    cb_cross_dept.change(toggle_cross_dept, [cb_cross_dept, edit_abs_t], [edit_intervention_type])
    edit_period.change(update_available_subs_smart, [edit_abs_t, edit_period, edit_intervention_type, day_in, current_schedule_state, current_user_is_admin], [edit_new_sub])
    edit_intervention_type.change(update_available_subs_smart, [edit_abs_t, edit_period, edit_intervention_type, day_in, current_schedule_state, current_user_is_admin], [edit_new_sub])
    btn_apply_override.click(lambda dfs, at, p, ns, dn, dpt, adm, ca: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "normal"), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in], update_outputs)
    btn_apply_tabadul.click(lambda dfs, at, p, ns, dn, dpt, adm, ca: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "tabadul"), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in], update_outputs)
    btn_apply_penalty.click(lambda dfs, at, p, ns, dn, dpt, adm, ca: process_admin_action(dfs, at, p, ns, dn, dpt, adm, ca, "penalty"), [current_schedule_state, edit_abs_t, edit_period, edit_new_sub, day_in, dept_in, current_user_is_admin, abs_in], update_outputs)
    btn_cancel_absence.click(cancel_teacher_absence, [edit_abs_t, day_in, dept_in, current_user_is_admin, abs_in], update_outputs)

    # ── أحداث الخزنة والتقارير والتبادل ─────────────────────────
    t_name.change(load_teacher_data_for_edit, t_name, [t_dept_edit, t_val, t_abs_val, t_short_val, t_phone_edit, t_specialty_edit, t_role_edit])
    t_dept_edit.change(
    lambda td, d: gr.update(visible=td in ["العلوم", "المهارات الفردية"] or d in ["العلوم", "المهارات الفردية"]),
    [t_dept_edit, dept_in],
    t_specialty_edit
    )
    t_btn.click(update_manual_count, [t_name, t_val, t_abs_val, t_short_val, t_phone_edit, t_specialty_edit, t_role_edit, dept_in, day_in, current_schedule_state, abs_in, current_user_is_admin], [tbl_bal, tbl_abs, tbl_day, vault_status, abs_in, check_teacher_in, rule_teacher])
    t_del_btn.click(delete_single_teacher, [t_name, dept_in, day_in], [tbl_bal, tbl_abs, tbl_day, vault_status, abs_in, check_teacher_in, rule_teacher, t_name])
    export_btn.click(export_excel_report, [dept_in], [report_file])
    reset_month_btn.click(reset_monthly_balances, [dept_in, day_in], [tbl_bal, tbl_abs, tbl_day, monthly_status])
    
    swap_dept.change(filter_swap_teachers_safe, [swap_dept], [swap_t1])
    swap_day.change(get_teacher_periods_safe, [swap_t1, swap_day], [swap_p1])
    swap_t1.change(get_teacher_periods_safe, [swap_t1, swap_day], [swap_p1])
    
    btn_run_radar.click(run_radar_safe, [swap_t1, swap_p1, swap_day], [swap_options, whatsapp_msg, wa_html_btn])
    swap_p1.change(run_radar_safe, [swap_t1, swap_p1, swap_day], [swap_options, whatsapp_msg, wa_html_btn])
    
    swap_options.change(generate_wa_msg, [swap_options, swap_t1, swap_p1, swap_day], [whatsapp_msg, wa_html_btn])

app.launch()
