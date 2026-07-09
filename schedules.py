# -*- coding: utf-8 -*-
"""
schedules.py
دوال الجداول والاختيارات النظيفة - Phase 3F-a.

هذه الوحدة لا تستورد app.py ولا تحتوي ربط Gradio.
"""

from __future__ import annotations

import re
import html as html_lib

import pandas as pd

from config import PAGE_SIZE
from storage import teachers_db, MAX_PERIODS, OFFICIAL_DEPTS, load_db


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


def resolve_effective_dept(dept_value):
    return "الكل" if str(dept_value or "").strip() == "المعلمون" else dept_value


def format_teacher_name(t_name):
    if t_name in teachers_db:
        role = teachers_db[t_name].get("role", "معلم")
        if role == "معلم أول" or teachers_db[t_name].get("is_admin_staff", False):
            return f"{t_name} ({role})"
    return t_name


def get_teacher_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    t_list = sorted([
        t for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and not d.get("is_admin_staff", False)
    ])
    choices = []
    for t in t_list:
        role = teachers_db[t].get("role", "معلم")
        if role != "معلم": choices.append(f"{t} ({role})")
        else: choices.append(t)
    return choices


def get_absentee_choices(dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    t_list = sorted([
        t for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and not d.get("is_admin_staff", False)
    ])
    choices = []
    for t in t_list:
        role = teachers_db[t].get("role", "معلم")
        if role in ["معلم أول", "منسق مادة"]: choices.append(f"{t} ({role})")
        else: choices.append(t)
    return choices


DAY_DEPT_STYLE_MAP = {
    "التربية الإسلامية": {"main": "#0f766e", "light": "#ecfdf5", "border": "#99f6e4", "accent": "#14b8a6"},
    "اللغة العربية": {"main": "#1d4ed8", "light": "#eff6ff", "border": "#bfdbfe", "accent": "#3b82f6"},
    "الرياضيات": {"main": "#b45309", "light": "#fffbeb", "border": "#fde68a", "accent": "#f59e0b"},
    "العلوم": {"main": "#047857", "light": "#ecfdf5", "border": "#a7f3d0", "accent": "#10b981"},
    "اللغة الإنجليزية": {"main": "#6d28d9", "light": "#f5f3ff", "border": "#ddd6fe", "accent": "#8b5cf6"},
    "الدراسات الإجتماعية": {"main": "#9f1239", "light": "#fff1f2", "border": "#fecdd3", "accent": "#f43f5e"},
    "المهارات الفردية": {"main": "#475569", "light": "#f8fafc", "border": "#cbd5e1", "accent": "#64748b"},
}


DAY_DEPT_FALLBACK_STYLES = [
    {"main": "#0f766e", "light": "#ecfdf5", "border": "#99f6e4", "accent": "#14b8a6"},
    {"main": "#1d4ed8", "light": "#eff6ff", "border": "#bfdbfe", "accent": "#3b82f6"},
    {"main": "#b45309", "light": "#fffbeb", "border": "#fde68a", "accent": "#f59e0b"},
    {"main": "#6d28d9", "light": "#f5f3ff", "border": "#ddd6fe", "accent": "#8b5cf6"},
]


def get_day_overview(day, dept_filter="الكل"):
    dept_filter = resolve_effective_dept(dept_filter)
    rows = [
        {"المعلم": format_teacher_name(t), **{f"ح {p}": d.get(day, {}).get(p, "-") for p in range(1, MAX_PERIODS + 1)}}
        for t, d in teachers_db.items()
        if (dept_filter == "الكل" or d.get("dept") == dept_filter)
        and not d.get("is_admin_staff", False)
    ]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=["المعلم"] + [f"ح {p}" for p in range(1, MAX_PERIODS + 1)])


def get_day_dept_style(dept_name, index=0):
    dept_key = str(dept_name or "").strip()
    if dept_key in DAY_DEPT_STYLE_MAP:
        return DAY_DEPT_STYLE_MAP[dept_key]
    return DAY_DEPT_FALLBACK_STYLES[index % len(DAY_DEPT_FALLBACK_STYLES)]


def render_day_department_section_html(dept_name, df, style, index=0):
    safe_df = df.fillna("-").copy() if df is not None else pd.DataFrame()
    dept_label = html_lib.escape(str(dept_name or "—"))
    count = len(safe_df)
    open_attr = " open" if index == 0 else " open"

    if safe_df.empty:
        table_html = "<div style='text-align:center; color:#64748b; padding:14px; background-color:#ffffff !important; border:1px dashed #cbd5e1; border-radius:12px; font-weight:800;'>لا توجد بيانات لهذا القسم.</div>"
    else:
        headers_html = "".join(
            f"<th style='padding:10px 12px; background-color:{style['main']} !important; color:#ffffff !important; -webkit-text-fill-color:#ffffff !important; border:1px solid {style['border']}; white-space:nowrap; font-size:13px; font-weight:900;'>{html_lib.escape(str(col))}</th>"
            for col in safe_df.columns
        )
        rows_html = ""
        for row_idx, (_, row) in enumerate(safe_df.iterrows()):
            bg = "#ffffff" if row_idx % 2 == 0 else "#f8fafc"
            cells_html = "".join(
                f"<td style='padding:9px 10px; background-color:{bg} !important; color:#0f172a !important; -webkit-text-fill-color:#0f172a !important; border:1px solid #d1d5db; white-space:nowrap; font-size:13px; font-weight:700;'>{html_lib.escape(str(row[col]))}</td>"
                for col in safe_df.columns
            )
            rows_html += f"<tr>{cells_html}</tr>"
        table_html = f"""
        <div style='overflow-x:auto; width:100%; -webkit-overflow-scrolling:touch;'>
            <table style='width:100%; min-width:760px; border-collapse:collapse; text-align:center; direction:rtl; font-family:Cairo, Arial, sans-serif; background-color:#ffffff !important;'>
                <thead><tr>{headers_html}</tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        """

    return f"""
    <details{open_attr} style='direction:rtl; background:{style['light']}; border:2px solid {style['border']}; border-right:8px solid {style['accent']}; border-radius:18px; margin:14px 0; overflow:hidden; box-shadow:0 8px 18px rgba(15,23,42,0.08);'>
        <summary style='cursor:pointer; list-style:none; padding:14px 18px; background:linear-gradient(135deg, {style['light']}, #ffffff); color:{style['main']}; font-weight:950; font-size:17px; text-align:right; border-bottom:1px solid {style['border']};'>
            <span style='display:inline-block; min-width:12px; min-height:12px; border-radius:999px; background:{style['accent']}; margin-left:8px; vertical-align:middle;'></span>
            {dept_label}
            <span style='font-size:12px; color:#475569; font-weight:900; margin-right:8px;'>عدد المعلمين: {count}</span>
        </summary>
        <div style='padding:12px; background-color:#ffffff !important;'>
            {table_html}
        </div>
    </details>
    """


def render_day_all_departments_html(day_name):
    sections = []
    total_rows = 0
    display_depts = [d for d in OFFICIAL_DEPTS if str(d).strip() != "الهيئة الإدارية"]
    for idx, dept_name in enumerate(display_depts):
        dept_df = get_day_overview(day_name, dept_name)
        if dept_df is None or dept_df.empty:
            continue
        total_rows += len(dept_df)
        sections.append(render_day_department_section_html(dept_name, dept_df, get_day_dept_style(dept_name, idx), idx))

    if not sections:
        return "<div style='text-align:center; color:#64748b; padding:18px; background:#f8fafc; border:1px dashed #cbd5e1; border-radius:12px; direction:rtl;'>لا توجد بيانات لعرضها في جدول اليوم.</div>", 0

    day_label = html_lib.escape(str(day_name or ""))
    return f"""
    <div style='direction:rtl; font-family:Cairo, Arial, sans-serif;'>
        <div style='text-align:right; color:#004d40; background:linear-gradient(135deg,#f8fffb,#fff8dc); border:1.5px solid #ffca28; border-radius:16px; padding:12px 16px; font-weight:950; margin:8px 0 14px 0;'>
            جداول الأقسام ليوم {day_label} — إجمالي المعلمين المعروضين: {total_rows}
        </div>
        {''.join(sections)}
    </div>
    """, total_rows


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


def get_day_table_updates_core(day_name, dept_filter, page=0):
    """Core logic for day-table updates without Gradio objects.

    Returns 7 raw slots:
    (dataframe, table_html, pager_visible, prev_interactive,
     next_interactive, page_html, current_page)
    """
    effective_dept = resolve_effective_dept(dept_filter)

    if effective_dept == "الكل":
        df = get_day_overview(day_name, effective_dept)
        if df is None or df.empty:
            load_db()
            df = get_day_overview(day_name, effective_dept)
        table_html, total_rows = render_day_all_departments_html(day_name)
        page_html = f"<div style='text-align:center; color:#0f766e; font-weight:bold; padding:8px 0;'>إجمالي المعلمين المعروضين: {total_rows}</div>"
        return (
            df,
            table_html,
            False,
            False,
            False,
            page_html,
            0,
        )

    df = get_day_overview(day_name, effective_dept)

    if df is None or df.empty:
        load_db()
        df = get_day_overview(day_name, effective_dept)

    table_html, safe_page, total_pages, total_rows = render_day_table_html(df, page, PAGE_SIZE)
    label = f"إجمالي معلمي {effective_dept}"
    page_html = f"<div style='text-align:center; color:#0f766e; font-weight:bold; padding:8px 0;'>{label}: {total_rows} | صفحة {safe_page + 1} من {total_pages}</div>"

    return (
        df,
        table_html,
        True,
        safe_page > 0,
        safe_page < total_pages - 1,
        page_html,
        safe_page,
    )
