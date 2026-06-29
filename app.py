تم تجهيز مرحلة 3I-a-5b: تقسيم دوال فلترة معلمي التبادل وحصص المعلم إلى core/wrapper.

النتيجة المحلية:

py_compile: ناجح
import swaps: ناجح
overlay test: ناجح
PASS: 277 | WARN: 0 | FAIL: 0 | INFO: 3

ZIP التعديل فقط

تحميل Masar_phase3i_a5b_swap_filter_periods_changed_files_only.zip⁠￼

يحتوي على:

app.py
swaps.py
check_masar_safety.py

ZIP كامل للملفات المطلوبة

تحميل Masar_phase3i_a5b_swap_filter_periods_all_required_files.zip⁠￼

يحتوي على:

app.py
swaps.py
exemptions.py
storage.py
school_data.py
schedules.py
balances.py
auth.py
config.py
check_masar_safety.py
masar_styles.css

ملفات منفصلة

تحميل app.py⁠￼

تحميل swaps.py⁠￼

تحميل check_masar_safety.py⁠￼

أهم ما تم

* إضافة filter_swap_teachers_safe_core داخل swaps.py.
* إضافة get_teacher_periods_safe_core داخل swaps.py.
* إبقاء filter_swap_teachers_safe في app.py كـwrapper فقط.
* إبقاء get_teacher_periods_safe في app.py كـwrapper فقط.
* اعتماد استيراد:

from schedules import get_teacher_choices

داخل swaps.py، بدون اعتماد دائري.

العقود بعد التعديل

filter_swap_teachers_safe ما زالت ترجع:

gr.update(choices=choices, value=value)

get_teacher_periods_safe ما زالت ترجع:

gr.update(choices=choices, value=value)

ملاحظات السلامة

* لا توجد @state_locked لأن الدوال لا تعدّل البيانات.
* swaps.py لا يحتوي:
    * gr.update
    * import gradio
    * gr.SelectData
    * import app
* تم تحديث check_masar_safety.py بفحص خاص للمرحلة 3I-a-5b.
* تم اختبار overlay فوق حزمة 3I-a-5a بنجاح.

بعد الرفع، المتوقع في Actions

PASS: 277 | WARN: 0 | FAIL: 0 | INFO: 3