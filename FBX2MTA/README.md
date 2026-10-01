# FBX2MTA — FBX → Blender → DragonFF → MTA:SA (DFF + COL)

خط أنابيب تلقائي كامل (Autonomous pipeline) لتحويل ملفات **FBX** إلى ملفات
**DFF + COL** متوافقة مع **GTA San Andreas PC / MTA:SA / RenderWare**، باستخدام:

- **Blender 4.2.23 LTS** (محرك `bpy` يعمل بلا واجهة — headless)
- **DragonFF** — الإضافة الرسمية من مستودعها الرسمي
  [`Parik27/DragonFF`](https://github.com/Parik27/DragonFF) — وهي المسؤولة فعلياً عن
  تصدير الـ DFF **والـ COL** (نفس الـ COL export الرسمي، بدون أي مُصدِّر مكتوب يدوياً)

```
FBX → Blender Import → Processing (cleanup/triangulate/normals/UV)
    → GTA SA Y-up conversion → DFF export (DragonFF, v3.6.0.3)
    → DFF validation + round-trip
    → COLLISION GENERATION (duplicate → decimate → cleanup → DragonFF COL export, COL3)
    → COL validation (بنفس gtaLib/col.py الخاص بـ DragonFF)
    → output/<name>.dff + output/<name>.col + test_resource/
```

> **الفصل بين DFF وCOL**: فشل الـ COL لا يُفشل الـ DFF أبداً (DFF=PASS / COL=FAILED
> مع السبب) — وفي حال الخطأ القابل للإصلاح تُعاد المحاولة تلقائياً
> (إعادة بناء شبكة الكوليشن + التنظيف + تقليل عدد المثلثات + إعادة التصدير + إعادة الفحص).

---

## التشغيل / Usage

```bash
# ▶️ نقطة الدخول الوحيدة:
python start.py                 # واجهة tkinter (سطح مكتب) — الافتراضية
python start.py --web           # بديل: واجهة ويب على http://localhost:8321
python start.py --cli           # بلا واجهة: يشغّل الخط كامل على input/*.fbx
python start.py --cli --file Dragon_2.5.fbx
python start.py --cli --no-col  # بدون كوليشن
python start.py --cli --col-quality HIGH
python start.py --cli --col-quality CUSTOM --col-triangles 2500

# أو المشغّل المباشر (نفس ما تشغّله الواجهة):
python3 scripts/run_pipeline.py --budget AUTO
python3 scripts/run_pipeline.py --files Dragon_2.5 --col-quality MEDIUM
```

واجهة **tkinter** (الافتراضية — تُفتح مباشرة بلا متصفح/سيرفر) تعرض:

- **Choose FBX file ...**: زر يفتح نافذة اختيار النظام → تختار أي `.fbx` من
  جهازك → يُنسخ إلى `input/` ويظهر في القائمة جاهزاً للتحويل
- قائمة ملفات `input/*.fbx` + زر **+ Generate Test FBX** (توليد FBX اختباري
  حقيقي بنموذج منخفض المضلعات يصدره Blender — للاختبار بدون ملفات خارجية)
- قسم **COLLISION**: [✓] Generate COL، الجودة AUTO/LOW/MEDIUM/HIGH/CUSTOM،
  حقل Maximum Collision Triangles، presets سريعة: 500 / 1000 / 2000 / 3000 / 5000 / 10000
- زر **Convert selected → DFF + COL**
- زر **Generate MTA Test Resource** + زر **Open output folder**
- لوحة النتائج: `Conversion Complete / DFF: PASS / COL: PASS / Triangles /
  Collision Triangles / outputs` + سجل حي (log)
- ملاحظة: tkinter مدمج في Python القياسي على Windows/macOS؛ على لينكس إن لم
  يتوفر: `sudo apt install python3-tk` — أو استخدم `python start.py --web`

واجهة الويب (`--web`) نفس الميزات بالضبط (بدل الاختيار من الجهاز: زر رفع).

## أنظمة التشغيل / Platforms (Windows · Linux · macOS)

المشروع يعمل بالكامل على **Windows و Linux و macOS** — كل الأوامر تمر عبر
`blender/run_blender.py` و `blender/setup_env.py` (Python نقّاء، بدون أي `.sh`
أو bash). لا توجد خطوة يدوية: عند الضغط على **Convert** أول مرة يبني الخط بيئة
Blender تلقائياً (self-heal) ويعيد المحاولة.

بيتان مهمان:

1. **الواجهة (tkinter)** تعمل بأي Python ≥ 3.9 (ومدمجة أصلاً في ثوابت
   Windows/macOS). هذه هي الـ Python التي تشغّل بها `python start.py`.
2. **محرك التحويل** هو `bpy 4.2.23` (نسخة DragonFF المعتمدة)، وتوفّر حزمه
   (wheels) **لـ Python 3.11 فقط**. لذلك:
   - عند الضغط على Convert أول مرة يبحث `blender/setup_env.py` عن Python 3.11
     على جهازك (على Windows يفحص `AppData\Local\Programs\Python` و Program Files
     والمسارات القياسية، وعلى لينكس `python3.11`/`/usr/bin`/`/opt`).
   - **وجد 3.11** ← ينشئ `blender/venv` وينصّب bpy تلقائياً (~350MB، قد تأخذ
     الدقائق الأولى وقتاً — هذا طبيعي).
   - **لا يوجد 3.11** ← يكتب في السجل خطوات التثبيت الدقيقة من
     [python.org](https://www.python.org/downloads/windows/) ثم اضغط Convert
     مرة أخرى. **لا تحذف Python الحالي** — 3.11 يُثبّت بجانبه.
   - مثال (Windows 3.13 + 3.11): الواجهة تعمل بـ 3.13 (`py -3.13 start.py`)
     والمحرك يُبنى على 3.11 (`blender/venv`) — كلاهما يجده النظام تلقائياً.

## المخرجات / Outputs

| المسار | المحتوى |
|---|---|
| `output/<name>.dff` | نموذج العرض (GTA SA v3.6.0.3، Y-up) |
| `output/<name>.txd` | **الخطط (textures) الحقيقية** (RenderWare TXD v5 / PC — تُكتب بواسطة كاتب DragonFF نفسه) — تُنتج إن وجدت صور مادية في الـ FBX |
| `output/<name>.col` | **كوليشن حقيقي** (GTA SA COL3 — `engineLoadCOL()`/`engineReplaceCOL()`) |
| `output/<name>.{dff,txd,col}.validation.json` | تقارير الفحص المستقل |
| `test_resource/` | MTA resource جاهز (meta.xml + client.lua + model.dff + model.txd + model.col) |
| `reports/model_report.txt` | تقرير كامل |
| `logs/converter.log` + `logs/<name>.log` | السجلات |
| `temp/*.status.json` | تفاصيل JSON لكل ملف |

## نظام الكوليشن (COL) / Collision System

- **غير مزوَّر وليس ملفاً جاهزاً**: شبكة الكوليشن تُبنى آلياً من نفس الشبكة
  المُصدَّرة للـ DFF (نسخ → decimate (COLLAPSE) → تنظيف bmesh → تصدير DragonFF).
- **نفس التحويل تماماً**: الـ DFF والـ COL ينتمان لنفس فضاء الإحداثيات
  (نفس الشبكة Y-up بمصفوفة identity) — لا كوليشن منزلق أمتاراً عن النموذج.
- **AUTO**: نموذج صغير (≤2000 مثلث) → لا تخفيض · متوسط (≤10000) → 3000 ·
  كبير → 5000 مثلث كحد أقصى (لا يزيد أبداً عن عدد المثلثات الأصلي).
- **التنظيف قبل التصدير**: إزالة الرؤوس المكررة، الوجوه المتدهورة (degenerate)،
  الرؤوس العارية، الجزر المنفصلة الدقيقة (<0.5% من المساحة — هندسة داخلية/تفاصيل
  غير مفيدة)، التحقق من عدم وجود NaN/Infinity.
- **التصدير**: `bpy.ops.export_col.scene` (version 3 = GTA SA COL3) — quantization
  1/128 وحساب الحدود (bounds) تلقائياً.
- **الفحص المستقل** (`scripts/validate_col.py`) بنفس مُحلل DragonFF: الملف موجود
  وحجمه >0، البنية صالحة، هناك هندسة كوليشن فعلية (وجوه/كرات/صناديق)، الرؤوس
  محدودة، فهارس الوجوه صالحة وغير متدهورة، الحدود (bounds) صالحة.

## نظام الخطط (TXD) / Texture System

- **كشف تلقائي واسع**: لكل مادة (material) يُبحث عن صورتها بالترتيب:
  1) صورة Base Color المباشرة (نفس مسار مُصدِّر DFF — تطابق أسماء
  مضمون)، 2) صورة عبر عقدة وسيطة (Mix/MixRGB/HueSat/RGB...)،
  3) أي عقدة Image Texture في شجرة المادة (Emission/Normal/غير
  مرتبط)، 4) فتحات الـ texture القديمة (legacy). أول صورة صالحة
  (بيانات محمّلة وحجم >1) تُعتمد.
- **صور FBX المضمّنة (embedded)**: Blender يحتفظ ببكسلات الصور المضمّنة
  في ذاكرة داخلية (ملف `.fbm` الجانبي لا يُكتب أصلاً) ولا تتحمّل إلا
  بوصول فعلي إلى مصفوفة البكسلات — المُحوِّل يستدعيها قبل الفحص،
  فلا تضيع صورة مضمّنة في الـ FBX.
- **لا صور → NONE مع سبب لكل مادة**: السجل والتقرير يشرحون حالة كل
  مادة على حدة (لا عقدة صورة / صورة بلا ملف / الصورة غير متاحة…)
  مع نصيحة: أعد تصدير الـ FBX مع تضمين الصور (embedded) ليُنتج TXD.
  لا ملف `.txd` يُنتج، ولا خطأ — النموذج يظهر بلا خط.
- **غير مزوَّر**: يُكتب بصيغة **RenderWare TXD v5 (PC)** — نفس الصيغة التي
  يقرأها SA/MTA (chunk `0x16` + stamp `0x1803FFFF` = RW 3.6.0.3)، وبواسطة
  **كاتب DragonFF نفسه** (`DragonFF/gtaLib/txd.py`) — لا كاتب جديد مكتوب
  من الصفر. صيغة البكسل: BGRA8888 (D3DFMT_A8R8G8B8) — بلا فقدان.
- **مطابقة أسماء دقيقة**: اسم كل خط في الـ TXD = اسم الصورة (بعد إزالة
  الامتداد) — نفس دالة التسمية التي يستخدمها مُصدِّر DFF
  (`extract_texture_info_from_name` + `clear_extension`) — فيجد SA/MTA كل
  مادة في الـ DFF خطها تلقائياً (`engineReplaceModel(dff, txd)`).
- **اتجاه البكسلات**: صفوف الـ TXD تُكتب من الأعلى (PNG order) — تُقلب
  صفوف Blender (bottom-up) رأساً على عقب قبل الكتابة.
- **حدود SA**: الخط أكبر من 1024px يُصغَّر إلى 1024 (حد مُصيِّر SA) مع
  تحذير في السجل.
- **الفصل عن DFF/COL**: فشل TXD (أو غيابه) **لا** يفشل النموذج — يظهر
  `TXD: FAILED/…` في التقرير مع السبب بينما يبقى DFF/COL بـ PASS.
- **الفحص المستقل** (`scripts/validate_txd.py`) بمُحَلِّل DragonFF نفسه:
  الملف موجود وحجمه >0، root chunk = `0x16`، stamp = `0x1803FFFF`، عدد
  الخطوط مطابق، أسماء غير فارغة، أبعاد صحيحة، حجم بيانات البكسلات =
  `width × height × 4`.

## مخرجات كل ملف

كل ملف FBX يعطي (بلا أوضاع اختيار):
- **DFF** — دائماً (النموذج الأساسي).
- **TXD** — تلقائياً عند وجود صور مواد (لا يمكن تعطيله — جزء من حزمة
  الموديل القياسية DFF+TXD).
- **COL** — عند تفعيله (مبدّل `Generate COL` في الواجهة / `--no-col` في
  السطر).

## بنية المشروع / Structure

```
FBX2MTA/
├── start.py        # نقطة الدخول (tkinter افتراضياً / --web / --cli)
├── tkgui.py        # واجهة tkinter (سطح مكتب — الافتراضية)
├── joblib.py       # منطق مشترك (وظائف خلفية + نتائج) — مشترك بين الواجهتين
├── webgui.py       # واجهة الويب (بديل اختياري عبر --web)
├── tests/smoke_tkgui.py  # اختبار دخان للواجهة بدون شاشة (للبنية/الاتصال)
├── input/          # ملفات FBX (تُكتشف تلقائياً)
├── output/         # DFF + TXD + COL النهائية
├── test_resource/  # MTA resource (meta.xml, client.lua, model.dff, model.txd, model.col)
├── blender/        # محرك bpy (venv) + setup_env.py + run_blender.py (عابر للنظام)
│   │               # + stubs لـ X11/GL (لينكس فقط) + setup_env.sh/run_blender.sh (غلاف)
├── dragonff/       # DragonFF الرسمي (cloned from Parik27/DragonFF)
├── scripts/
│   ├── convert.py            # خط التحويل داخل Blender (DFF ثم TXD ثم COL)
│   ├── collision_generator.py# بناء شبكة الكوليشن + تصديرها عبر DragonFF COL
│   ├── validate_dff.py       # فحص DFF مستقل (gtaLib/dff.py)
│   ├── validate_txd.py       # فحص TXD مستقل (gtaLib/txd.py)
│   ├── validate_col.py       # فحص COL مستقل (gtaLib/col.py)
│   ├── roundtrip.py          # DFF → DragonFF Import → Blender
│   ├── generate_test_fbx.py  # توليد FBX اختباري (--animated: سكيلتون متحرك)
│   ├── mta_resource.py       # توليد test_resource/
│   └── run_pipeline.py       # المشغّل: batch + TXD + COL + تقرير
├── logs/  reports/  temp/
```

## ملاحظات تقنية / Technical Notes

- **اتجاه النموذج (Y-up)**: Blender يعمل Z-up بينما فضاء نماذج GTA SA هو **Y-up**
  (إطارات الـ clump الأصلية identity). لذلك مرحلة 4.5 تدوّر النموذج كاملاً
  (شبكة + عظام + empties) بـ -90° حول X: `(x,y,z) → (x,z,-y)` — دوران ميسور
  (بدون mirroring) فتبقى winding/normals صالحة. دقة المطابقة تم التحقق منها:
  حدود الـ COL ضمن حدود الـ DFF بفارق ≤0.7m (تأثير decimation فقط).
- **حدود DFF الصارمة**: 65535 رأس/geometry (يُقدَّم قبل التصدير ويُرفض إن تجاوز).
- **Geometry Budget = AUTO**: لا يُخفَّض نموذج سليم تلقائياً؛ `--budget N` يفرض حداً.
- **Skinned mesh**: العظام تُصدَّر كـ frames مع SkinPLG (230 عظمة في التنين).
  ملاحظة: العدد أكبر من هيكل SA المكون من 32 عظمة — صالح كنموذج مستقل،
  أما استبدال موديل ped فسيُعاد للهيكل القياسي.
  ملاحظة 2: DragonFF لا يكتب الـ skin إلا إذا كان على الـ mesh
  **Armature modifier** — يستعيده خط التحويل آلياً إن غاب (الـ FBX لا
  يضمن وجوده)، ويُثبَّت المشهد على أول مفتاح حركة (وضع الراحة).
- **Rigs على نمط RDR/STK** (السكيلتون معلّق على empty مثل `Sam` أو
  `..._CTRL`): DragonFF الرسمي يصدّر الـ armature قبل الـ empty (ترتيب
  مجموعات الـ FBX عشوائي) فيقذف `Failed to set parent for <arm> to <empty>` —
  الخط يفصل الـ armature عن الـ empty تلقائياً قبل التصدير (المصفوفات
  identity عندئذ → صفر أثر على النموذج)، فيصبح إطار الـ armature هو جذر الـ clump.
- **الملفات المدخلة في `input/` لا تُعدَّل أبداً** — كل العمليات على نسخ.

## الاختبار داخل MTA:SA / In-game test

انسخ مجلد `test_resource/` إلى `MTASA/resources/dragon_test/` ثم شغّل
الـ resource. `client.lua` يستخدم:

```lua
engineLoadDFF(0, 'model.dff')      + engineReplaceModel(206, 'model.dff', 'model.txd')
engineLoadCOL('model.col')         + engineReplaceCOL(206, 'model.col')
createObject(206, 100.0, 1.5, -1000.0, 0, 0, 0)   -- spawn للاختبار الفوري
```

الخطوط (model.txd) تُولد من صور مواد الـ FBX وتطابق أسماء مواد الـ DFF
تلقائياً — لا خطوة يدوية. (لم يتم تشغيل MTA هنا — غير مثبت — لذا:
**MTA runtime test unavailable**).

## إعادة التهيئة / Reset

```bash
# إعادة إنشاء محرك Blender (عابر للنظام — يوجِد Python 3.11 ويبني venv وينصّب bpy):
python blender/setup_env.py
# (setup_env.sh غلاف له في لينكس؛ وفي ويندوز شغّل الـ .py مباشرة)
# stubs جاهزة في blender/stublibs + blender/x11_missing.so (X11 headless — لينكس فقط)
```
