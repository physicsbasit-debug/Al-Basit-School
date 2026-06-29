# -*- coding: utf-8 -*-
"""
balances.py
دوال الأرصدة والغياب والتقصير النظيفة - Phase 3G-a.

هذه الوحدة لا تستورد app.py ولا تحتوي ربط Gradio.
"""

from __future__ import annotations

import pandas as pd

from config import ADMIN_ROLES
from storage import teachers_db
from schedules import resolve_effective_dept, format_teacher_name


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
