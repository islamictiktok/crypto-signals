# Golden Consensus Trading Bot

هذه نسخة مطوّرة من البوت المرفق. بدلاً من الاعتماد على تقاطع EMA واحد، يقوم النظام ببناء **استراتيجية Adaptive Donchian Trend + Volatility Regime + Whale Confirmation**، ثم يمرر نتائج **خمسة محللين مستقلين** عبر محرك إجماع. لا توجد في هذه النسخة أوامر شراء أو بيع حقيقية؛ الوضع الافتراضي هو الإشارات والتداول الورقي.

> أنا لست مستشاراً مالياً مرخصاً؛ هذا النظام تحليل وبرمجة وليس ضماناً للربح، والتداول بالرافعة قد يؤدي إلى خسارة رأس المال.

## المحللون الخمسة

| المحلل | الوظيفة |
|---|---|
| Trend / Donchian Analyst | يقيس اختراقات Donchian متعددة المدد عبر 4H و1H و30M مع اتجاه EMA200. |
| Market Regime Analyst | يميز بين سوق اتجاهي وسوق فوضوي باستخدام ADX وATR وEMA، ويمنع التداول عند سوء النظام. |
| Momentum & Structure Analyst | يؤكد الإغلاق بعد الاختراق، الزخم، RSI/MACD، وموقع السعر بالنسبة للبنية. |
| Volume & Liquidity Analyst | يفحص الحجم، OBV، السبريد التقريبي، وحجم التداول اليومي. |
| Whale & Derivatives Analyst | يدمج funding وopen interest وتغير السعر لرصد الطلب الجديد، التصفيات، والازدحام. |

لا تُرسل إشارة إلا عند اتفاق ثلاثة محللين من خمسة على الأقل، وثقة موزونة لا تقل عن `MIN_CONFIDENCE`، ومع تأكيد محلل الحيتان افتراضياً. كما تُرفض الإشارة عند وجود محلل قوي يعاكس الاتجاه.

## أبرز الإصلاحات مقارنة بالنسخة الأصلية

تم استبدال اعتماد البوت على إشارة واحدة بإجماع موزون مع حفظ أسباب كل محلل داخل حالة الصفقة. كما أصبح التحليل يعتمد على الشموع المغلقة فقط، مع منع تكرار إشارة نفس الشمعة، وتحديد رموز أكثر سيولة، وإضافة حماية يومية للخسائر، وحد أقصى للمخاطرة لكل صفقة، وحد أقصى للتعرض الإجمالي عبر تقليص الحجم حسب رأس المال الافتراضي.

كذلك تم تصحيح احتساب نتيجة الصفقة ليظهر `R-multiple` و`Paper PnL` بعد تقدير الرسوم والانزلاق، وإضافة نقاط صحة `/health` وإحصاءات `/api/stats`، وتحسين الحفظ الذري للحالة والسجلات ومعالجة أخطاء Telegram وMEXC.

## التشغيل

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# عدّل القيم ثم صدّرها إلى البيئة أو استخدم مدير أسرار المنصة
set -a; source .env; set +a
python golden_consensus_bot.py
```

يستمع السيرفر على المنفذ الموجود في `PORT`، والافتراضي `10000`. يفحص السوق بعد إغلاق كل شمعة 30 دقيقة بثماني ثوانٍ، ويتابع الصفقات الورقية كل خمس ثوانٍ.

## الإعدادات الحساسة

| المتغير | الافتراضي | المعنى |
|---|---:|---|
| `PAPER_TRADING` | `true` | يبقي النظام في الإشارات/التداول الورقي. لا تغيّره قبل مراجعة الكود وإضافة طبقة تنفيذ مستقلة. |
| `PAPER_CAPITAL_USDT` | `10000` | رأس المال الافتراضي الذي يحسب عليه الحجم. |
| `RISK_PER_TRADE_PCT` | `0.50` | المخاطرة الاسمية لكل صفقة من رأس المال الافتراضي. |
| `MAX_DAILY_LOSS_R` | `-3.0` | يوقف إرسال إشارات جديدة بعد بلوغ حد الخسارة اليومي. |
| `MAX_TRADES` | `3` | الحد الأقصى للصفقات المفتوحة. |
| `MAX_LEVERAGE` | `5` | سقف حسابي للحجم الاسمي، وليس توصية باستخدام الرافعة. |
| `MIN_CONFIDENCE` | `65` | الحد الأدنى لثقة الإجماع. |
| `WHALE_CONFIRM_REQUIRED` | `true` | يشترط تأكيد funding/OI قبل إرسال الإشارة. |
| `MAX_DERIVATIVE_HISTORY` | `120` | عدد عينات OI/funding المحفوظة لكل رمز. |
| `MIN_24H_VOLUME` | `10000000` | أقل حجم تداول يومي اسمي للمرشحين. |
| `TOP_COINS_LIMIT` | `60` | عدد الرموز الأعلى حجماً التي يجري تحليلها. |
| `TELEGRAM_TOKEN` | فارغ | رمز بوت Telegram. |
| `CHAT_ID` | فارغ | معرّف المحادثة التي تستقبل الإشارات. |

## الاختبار قبل أي تشغيل فعلي

شغّل اختبارات المحرك:

```bash
python -m unittest discover -s tests -v
```

ثم اترك النظام في Paper Trading فترة كافية، وسجّل النتائج حسب الرمز، الإطار الزمني، سبب الخروج، ونتيجة `R`. لا تعتمد على نسبة الفوز وحدها؛ راقب أيضاً متوسط الربح إلى متوسط الخسارة، أكبر تراجع، الانزلاق، وفترات توقف السوق.

## ما لم أفعله عمداً

لم أضف تنفيذ أوامر حقيقية أو مفاتيح API أو سحب أموال. جعل `PAPER_TRADING=false` وحده لا ينفذ أوامر؛ هذه حماية مقصودة. إذا أردت لاحقاً طبقة تنفيذ حقيقية، يجب تصميمها منفصلة مع مفاتيح تداول فقط دون سحب، واختبار أوامر الحد والحجم ودقة السعر، وآلية منع التكرار، ومفتاح إيقاف فوري.

## الملفات

| الملف | المحتوى |
|---|---|
| `golden_consensus_bot.py` | البوت المطوّر. |
| `tests/test_engine.py` | اختبارات وحدات للمؤشرات والإجماع والحجم ومحلل الحيتان. |
| `research_notes.md` | المصادر والافتراضات التي بُني عليها اختيار المنهج. |
| `.env.example` | إعدادات تشغيل نموذجية. |
| `DESIGN.md` | التصميم والمنطق العام. |
| `original_bot.py` | نسخة البوت المرفقة كما وصلت للمقارنة. |

This is research and analysis only, not personalized financial advice.

## الأساس البحثي والقيود

اختيار المنهج مبني على أدلة بحثية حديثة تشير إلى أن trend-following/time-series momentum، وعند تطبيقه كنماذج Donchian متعددة المدد مع position sizing مبني على التقلب، هو أساس قابل للاختبار في الكريبتو، وليس على ادعاء وجود استراتيجية رابحة دائماً. دراسة Swiss Finance Institute/SSRN تقترح ensemble من قنوات Donchian وتدويراً على العملات الأعلى سيولة مع احتساب الرسوم [1]. ودراسة حديثة على arXiv تعرض trend-following تكيفياً مع trailing stop مبني على ATR وتحليل الأنظمة السوقية [2].

تم التحقق من أن توثيق MEXC الرسمي يعرض `holdVol` كـ open interest و`fundingRate` ضمن ticker العام [3]، بينما توثيق CCXT يوضح أن توفر الدوال الموحّدة يختلف حسب المنصة، ولذلك يستخدم البوت aliases من `ticker.info` مع fallback محافظ [4].

هذه المصادر لا تثبت أن النسخة الحالية ستكون رابحة في المستقبل، ولم يتم اختلاق نتيجة backtest للنسخة الجديدة. يجب إجراء walk-forward backtest على نفس رموز MEXC، مع رسوم وانزلاق وتأخر تنفيذ، قبل تغيير الوضع من Paper Trading.

### References

[1]: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907 "Catching Crypto Trends; A Tactical Approach for Bitcoin and Altcoins — Swiss Finance Institute Research Paper No. 25-80"
[2]: https://arxiv.org/html/2602.11708v1 "Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets"
[3]: https://www.mexc.com/api-docs/futures/market-endpoints/get-ticker-contract-market-data "MEXC API — Get Ticker (Contract Market Data)"
[4]: https://github.com/ccxt/ccxt/wiki/manual "CCXT Manual"
