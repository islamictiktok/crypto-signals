# Scalper Consensus Bot — 5 إلى 10 دقائق

> أنا لست مستشاراً مالياً مرخصاً؛ هذا النظام تحليل وبرمجة وليس ضماناً للربح. لا يمكن ضمان وصول كل صفقة إلى الهدف خلال 5 أو 10 دقائق، وقد يؤدي التداول بالرافعة إلى خسارة رأس المال.

## ماذا يفعل؟

هذه نسخة مستقلة عن بوت 30 دقيقة. تختار افتراضياً أعلى 10 عقود USDT من حيث حجم التداول، ثم تجمع شموع 1m و3m و5m من REST وبيانات order book وtrades من MEXC WebSocket. لا يرسل البوت إشارة إلا عندما تتفق أربعة طبقات على الأقل من خمسة، ويشترط أن يكون order-book imbalance وaggressive trade flow في نفس الاتجاه.

النسخة تستخدم MEXC WebSocket الرسمي على `wss://contract.mexc.com/edge`. قناة `sub.depth` تعطي depth كل 200ms، وقناة `sub.deal` تعطي الصفقات عند حدوثها. يرسل البوت ping كل 15 ثانية ويعيد الاتصال عند انقطاع الجلسة.

## المحللون الخمسة

| المحلل | قاعدة العمل |
|---|---|
| Short Trend / Donchian | EMA9/EMA21 وVWAP وDonchian 20 على 3m و5m. |
| Momentum Trigger | ROC3 وRSI14 وATR وvolume ratio وbody/ATR على 1m. |
| Order Book Imbalance | يقيس عمق أفضل 10 مستويات bid/ask، السبريد، عمر البيانات، والاختلال. |
| Aggressive Trade Flow | يجمع قيمة صفقات الشراء والبيع خلال آخر 30 ثانية. |
| Execution & Risk Gate | يحسب TP/SL والتكلفة الصريحة والتقديرية وNet RR والسيولة. |

## الأهداف الزمنية

الهدف الافتراضي `0.35%` والاستوب يتراوح بين `0.18%` و`0.32%` حسب ATR. هذه النسب قابلة للتعديل وليست نتيجة مضمونة. إذا لم تصل الصفقة بعد 5 دقائق إلى تقدم يساوي `0.40R`، يخرج البوت في Paper Trading. وفي كل الأحوال يوجد خروج إجباري بعد 10 دقائق. قبل ذلك يمكن أن تخرج الصفقة عند TP أو SL.

## التشغيل

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.scalp.example .env
set -a; source .env; set +a
python scalper_consensus_bot.py
```

المنفذ الافتراضي هو `10001`. افحص `/health` و`/api/stats` بعد التشغيل. لا يحتاج مصدر البيانات العام إلى مفاتيح MEXC، لكن Telegram يحتاج `TELEGRAM_TOKEN` و`CHAT_ID`.

## أهم الإعدادات

| المتغير | الافتراضي | الغرض |
|---|---:|---|
| `SCALP_TOP_SYMBOLS` | `10` | عدد العقود الأعلى سيولة. |
| `SCALP_SCAN_INTERVAL_SEC` | `10` | تكرار التحليل السعري. |
| `SCALP_MAX_BOOK_AGE_SEC` | `2` | أقصى عمر لبيانات دفتر الأوامر. |
| `SCALP_MAX_FLOW_AGE_SEC` | `3` | أقصى عمر لآخر صفقة في نافذة التدفق. |
| `SCALP_MAX_SPREAD_PCT` | `0.0012` | حد السبريد قبل رفض الصفقة. |
| `SCALP_MIN_BOOK_IMBALANCE` | `0.18` | الحد الأدنى لاختلال الدفتر. |
| `SCALP_MIN_FLOW_IMBALANCE` | `0.15` | الحد الأدنى لاختلال التدفق. |
| `SCALP_MIN_CONFIDENCE` | `78` | حد ثقة الإجماع. |
| `SCALP_MIN_NET_RR` | `1.35` | الحد الأدنى للعائد الصافي مقابل الخطر والتكلفة. |
| `SCALP_SOFT_TIMEOUT_SEC` | `300` | خروج مبكر بعد 5 دقائق إذا لم يتحقق التقدم. |
| `SCALP_HARD_TIMEOUT_SEC` | `600` | خروج إجباري بعد 10 دقائق. |
| `SCALP_MAX_POSITIONS` | `2` | الحد الأقصى للصفقات الورقية المفتوحة. |
| `PAPER_TRADING` | `true` | يبقي التشغيل في وضع ورقي/إشارات فقط. |

## لماذا قد لا يرسل إشارات كثيرة؟

هذا مقصود. السكالب عالي الجودة يرفض البيانات القديمة، السبريد الكبير، العمق الضعيف، تدفقاً غير حاسم، أو اختلاف الاتجاه بين المحللين. أول دقيقة بعد التشغيل قد لا تحتوي على سجل صفقات كافٍ. لا ترفع الفلاتر أو الرافعة لمجرد الحصول على إشارات أكثر قبل قياس أثر ذلك على صافي النتيجة بعد التكلفة.

## حدود مهمة

الـ WebSocket يعطي بيانات لحظية، لكنه لا يضمن سرعة التنفيذ أو امتلاء الأمر. نسخة البوت الحالية لا تنفذ أوامر حقيقية؛ لا توجد استدعاءات `create_order`. قبل أي طبقة تنفيذ حقيقية يجب بناء order manager منفصل مع مفاتيح تداول دون سحب، idempotency، kill switch، والتحقق من ملء الأوامر والـ partial fills.

يجب تقييم هذه النسخة ببيانات tick/order-book أو trade-level، لا ببيانات شموع فقط. التقرير الصحيح يجب أن يتضمن إجمالي الصفقات، متوسط R، صافي PnL بعد الرسوم والانزلاق، أكبر تراجع، نسبة وصول TP خلال 5 دقائق، نسبة الخروج الزمني، وفروق النتائج بين كل رمز.

## المصادر

الأساس البحثي موثق في `scalp_research.md`. توثيق MEXC الرسمي لقناة العمق موجود في [Order book depth](https://www.mexc.com/api-docs/futures/websocket-api/order-book-depth)، ولصفقات التنفيذ في [Deal](https://www.mexc.com/api-docs/futures/websocket-api/deal)، وللاتصال والـ heartbeat في [Native ws endpoint](https://www.mexc.com/api-docs/futures/websocket-api/native-ws-endpoint) و[Command details](https://www.mexc.com/api-docs/futures/websocket-api/command-details-for-data-exchange).

This is research and analysis only, not personalized financial advice.
