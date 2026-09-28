# FBX2MTA — FBX → Blender → DragonFF → MTA:SA DFF

خط أنابيب تلقائي كامل (Autonomous pipeline) لتحويل ملفات **FBX** إلى ملفات **DFF**
متوافقة مع **GTA San Andreas PC / MTA:SA / RenderWare**، باستخدام:

- **Blender 4.2.23 LTS** (محرك `bpy` يعمل بلا واجهة — headless)
- **DragonFF** — الإضافة الرسمية من مستودعها الرسمي
  [`Parik27/DragonFF`](https://github.com/Parik27/DragonFF) — وهي المسؤولة فعلياً عن تصدير الـ DFF

```
FBX → Blender FBX Import → Scene Processing → Geometry Validation → Triangulation
→ Optimization → Triangle Analysis / Budget → Materials / UV / Normals
→ DragonFF (GTA SA v3.6.0.3) → DFF → DFF Validation → Round-Trip Test → output/
```

---

## التشغيل / Usage

```bash
# كل ما تحتاجه (يحوّل كل ملفات FBX داخل input/ دفعة واحدة):
python3 scripts/run_pipeline.py                 # budget AUTO
python3 scripts/run_pipeline.py --budget 20000  # حد مثلثات مخصص

# أو ملف واحد مباشرة:
blender/run_blender.sh scripts/convert.py \
    --input input/your_model.fbx \
    --output output/your_model.dff \
    --budget AUTO \
    --log logs/your_model.log \
    --status temp/your_model.status.json
```

- الملفات المدخلة في `input/` **لا تُعدَّل أبداً** (قاعدة 24) — كل العمليات على نسخة داخل الذاكرة/`temp/`.
- أكثر من ملف = batch mode تلقائي مع ملخص `success / failed / skipped`.
- عند الفشل: إعادة محاولة تلقائية (Bin Mesh PLG → إعادة تنظيف normals/دوال → decimation) بحد أقصى 4 محاولات.

## المخرجات / Outputs

| المسار | المحتوى |
|---|---|
| `output/<name>.dff` | الملف النهائي (GTA SA v3.6.0.3) |
| `reports/model_report.txt` | تقرير كامل (أرقام، مواد، نصائح توافق) |
| `logs/converter.log` | سجل الدفعة + سجل لكل ملف |
| `mta_test/` | MTA resource جاهز (meta.xml + client.lua + model.dff) |
| `temp/*.status.json` | تفاصيل JSON لكل ملف |

## بنية المشروع / Structure

```
FBX2MTA/
├── input/      # ملفات FBX (تُكتشف تلقائياً)
├── output/     # DFF النهائي
├── blender/    # محرك bpy (venv) + stubs لـ X11/GL + run_blender.sh
├── dragonff/   # DragonFF الرسمي (cloned from Parik27/DragonFF)
├── scripts/
│   ├── convert.py        # خط التحويل داخل Blender
│   ├── validate_dff.py   # فحص DFF مستقل (بنفس gtaLib/dff.py الخاص بـ DragonFF)
│   ├── roundtrip.py      # اختبار الإعادة: DFF → DragonFF Import → Blender
│   └── run_pipeline.py   # المشغّل: batch + تقرير + MTA test
├── logs/  reports/  temp/
└── mta_test/   # MTA resource للاختبار داخل اللعبة
```

## ملاحظات تقنية / Technical Notes

- **نظام إحداثيات GTA SA**: Blender هو Z-up (مثل SA)، والتحويل مُتحقق منه آلياً
  (4 تركيبات محاور) باختيار الأُقصى لدرجة "الاستقامة" — لا تحويلات تخمينية.
  DragonFF نفسه يعكس UV-v ويرتّب winding عند التصدير.
- **حدود DFF الصارمة**: 65535 رأس/geometry (يُقدَّم قبل التصدير ويُرفض إن تجاوز) —
  نموذج التنين هنا: 22,827 رأس → ضمن الحد دون أي تخفيض.
- **Geometry Budget = AUTO**: لا يُخفَّض نموذج سليم تلقائياً؛ `--budget N` يفرض حداً
  مع decimation يحافظ على UV/normals/skinning.
- **Skinned mesh**: العظام تُصدَّر كـ frames مع HAnim + SkinPLG (230 عظمة هنا،
  118 منها موزونة). ملاحظة: العدد أكبر من هيكل SA المكون من 32 عظمة — صالح كنموذج
  مستقل، أما استبدال موديل ped فسيُعاد للهيكل القياسي.
- **الخصائص**: UV (الخريطة الأولى) + normals لكل-رأس + bump map (Rockstar effect)
  + أسماء مواد نظيفة + أسماء textures مطابقة لأسماء الملفات.

## الاختبار داخل MTA:SA / In-game test

انسخ مجلد `mta_test/` إلى `MTASA/resources/dragon_test/` (مع ملف TXD بنفس أسماء
الtextures: `Dragon_Bump_Col2` و `Dragon_Nor_mirror2`) ثم شغّل الـ resource.
`client.lua` يستخدم `engineLoadDFF()` و `engineReplaceModel()` للطباعة الفورية.
(لم يتم تشغيل MTA هنا — غير مثبت — لذا: **MTA runtime test unavailable**).

## إعادة التهيئة / Reset

```bash
# إعادة تثبيت محرك Blender (عند نقل الجهاز):
python3 -m venv blender/venv && blender/venv/bin/pip install bpy==4.2.23
# stubs جاهزة في blender/stublibs + blender/x11_missing.so (X11 headless)
```
