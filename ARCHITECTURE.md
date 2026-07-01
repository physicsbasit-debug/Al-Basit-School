# البنية المعمارية لمنظومة مسار

هذا الدليل يشرح الوضع المعماري الحالي بعد إغلاق مراحل التفكيك الثقيلة: `3J`, `3K`, `3L-auth`, و`3M-final-safety-note`.

الحالة المستقرة الحالية:

```text
PASS: 477 | WARN: 0 | FAIL: 0 | INFO: 6
```

---

## 1. خريطة الاعتماديات العامة

الخريطة النصية المبسطة:

```text
config.py
   ↓
storage.py
   ↓
auth.py / schedules.py / exemptions.py / balances.py / swaps.py / school_data.py / distribution.py
   ↓
app.py
```

القواعد الأساسية:

- الوحدات النظيفة لا تستورد `app.py`.
- منطق العمل الثقيل يكون داخل وحدات مستقلة.
- `app.py` يبقى طبقة Gradio: واجهة، ربط، wrappers، ودوال عرض.
- تعديل الحالة المحفوظة يتم داخل core مقفل بـ`@state_locked` قدر الإمكان.

---

## 2. جدول الوحدات

| الملف | المسؤولية | أمثلة على الدوال الحرجة | ملاحظة |
|---|---|---|---|
| `app.py` | واجهة Gradio، الربط، wrappers، HTML/JS/render | wrappers مثل `assign_logic`, `save_school_identity_settings` | ليس ملف منطق العمل الثقيل بعد التفكيك |
| `config.py` | الثوابت والأدوار والأقسام | `ADMIN_ROLES`, `OFFICIAL_DEPTS` | مصدر ثوابت النظام |
| `storage.py` | التخزين، الأقفال، audit، safe write | `state_locked`, `safe_write_json`, `write_audit_log` | لا يستورد `app.py` |
| `auth.py` | الحسابات وPIN والصلاحيات | `change_own_account_pin_core`, `owner_reset_account_pin_core` | بلا `gr.update` أو `import gradio` |
| `distribution.py` | التوزيع والاحتياط والخزنة | `assign_logic_core`, `refresh_ui_on_change_core` | أكبر عنقود تم تفكيكه في 3J |
| `school_data.py` | مركز البيانات وإعدادات المدرسة والهوية | `refresh_admins_from_reference_core`, `save_school_identity_settings_core` | يحتوي إرث `gr.update` موثق من 3E-a |
| `swaps.py` | التبادل الودي ورسائل واتساب والصور | `confirm_swap_core`, `generate_wa_msg_core` | يحتوي جزءًا من الصور والتبادل |
| `schedules.py` | جدول اليوم وجدول المعلم | `get_day_table_updates_core` | يعتمد على بيانات الجداول والمعلمين |
| `exemptions.py` | حالات الإعفاء | `is_teacher_exempt_for_slot`, `save_teacher_rules_core` | يقرأ `teachers_db` للتحقق من الإعفاء |
| `balances.py` | الأرصدة والتقارير المختصرة | `get_updated_balance`, `render_compact_rtl_table_html` | تقارير وقراءات مساعدة |
| `check_masar_safety.py` | فحص السلامة المعمارية | فحوص 3J/3K/3L/3M | شبكة أمان قبل الرفع |

---

## 3. نمط core / wrapper

النمط المعتمد:

```text
core = منطق العمل الخام داخل الوحدة المناسبة
wrapper = طبقة app.py التي تحول النتائج إلى مخرجات Gradio
```

مثال مبسط:

```python
def assign_logic(...):
    result = assign_logic_core(...)
    return refresh_ui_on_change(...)
```

القاعدة:

- `@state_locked` يوضع على الـcore الذي يعدل الحالة.
- الـwrapper في `app.py` لا يحمل `@state_locked`.
- الـcore لا يحتوي `gr.update`.
- الـcore لا يستورد `app.py`.
- الـwrapper يحافظ على عدد وترتيب مخرجات Gradio.

هذا النمط استخدم في عناقيد:

- التوزيع والاحتياط.
- مركز البيانات والهوية.
- الحسابات والمصادقة.

---

## 4. قاعدة البيانات في الذاكرة والحفظ الآمن

الكائنات المشتركة الأساسية تشمل:

```text
teachers_db
daily_db
processed_absences
last_assigned_teachers
SCHOOL_CONFIG
AUTH accounts
```

قاعدة مهمة:

```text
لا تعيد تعيين الكائن المشترك إذا كان مستوردًا بالمرجع.
استخدم clear/update أو mutation in-place عند الحاجة.
```

مثال الخلل الذي تم إصلاحه في 3K:

```python
# خطأ سابق
SCHOOL_CONFIG = dict(config)

# الصحيح
SCHOOL_CONFIG.clear()
SCHOOL_CONFIG.update(config)
```

سبب الأهمية: `storage.py` يستخدم `SCHOOL_CONFIG` عند كتابة سجل التدقيق، وإعادة تعيين الاسم في `app.py` فقط كانت تجعل `audit_log` يقرأ اسم النظام القديم.

---

## 5. القاعدة الحمراء: مركز البيانات Fix 13b

مركز البيانات من أكثر مناطق الواجهة حساسية تاريخيًا.

ممنوع العبث العشوائي بـ:

```text
school_data_panel_js
school_data_tab.select
select_tab_js("مركز البيانات")
elem_id للوحات مركز البيانات
CSS/JS المرتبط بالتنقل بين اللوحات
```

اختبار مركز البيانات يجب أن يثبت أن اللوحات الخمس تفتح من النقرة الأولى عبر الأزرار، دون رجوع مشكلة النقرة الثانية.

---

## 6. الدالة الوحيدة المؤجلة عمدًا

الدالة الوحيدة المتبقية في `app.py` التي تحمل `@state_locked` هي:

```text
clear_all_data
```

سبب التأجيل:

- دالة system reset شاملة.
- ترجع 31 مخرجًا مختلطًا.
- تحتوي `gr.update` وقيمًا خامة و`DataFrame`.
- لا يوجد احتياج عاجل لفصلها.
- فصلها الآن مخاطرة أعلى من فائدته.

توثيقها في `check_masar_safety.py` مقصود، ولا يُعد فشلًا.
