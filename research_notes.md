# نتائج البحث الأولية — 25 أغسطس 2026

## خلاصة أولية

لا توجد استراتيجية واحدة يمكن وصفها بأنها الأقوى دائماً أو المضمونة. أقوى أساس قابل للتحويل إلى بوت من المصادر التي تمت مراجعتها هو **trend-following/time-series momentum** مع نماذج دخول متعددة، وفلتر نظام/تقلب، وحجم مركز مبني على التقلب، وتكاليف تداول محسوبة. بيانات المشتقات مثل funding وopen interest تصلح كطبقة تأكيد ومخاطر، وليست بديلاً عن الاتجاه والسعر.

## المصدر 1

العنوان: *Systematic Trend-Following with Adaptive Portfolio Construction: Enhancing Risk-Adjusted Alpha in Cryptocurrency Markets*

الرابط: https://arxiv.org/html/2602.11708v1

نقاط قابلة للاستخدام:

- يقترح إطاراً للتداول الاتجاهي على أطر 6 ساعات مع بناء محفظة تكيفي شهرياً.
- يستخدم زخم/معدل تغير، اختياراً مبنياً على الأداء، وتوزيعاً متكيفاً للمراكز.
- يستخدم trailing stop متحركاً مبنياً على ATR، حيث يتحرك الاستوب في اتجاه الصفقة ولا يتراجع في الصفقة الرابحة.
- النص يعرض نتائج خارج العينة على أكثر من 150 زوجاً وفترة تقييم 2022–2024، لكنه بحث حديث أولي؛ لذلك لا ينبغي التعامل مع الأرقام كضمان أو كأداء مستقبلي.
- يذكر أهمية تحليل الأنظمة السوقية، حساسية المعلمات، وتكاليف المعاملات.

## المصدر 2

العنوان: *Catching Crypto Trends; A Tactical Approach for Bitcoin and Altcoins*

الرابط: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5209907

نقاط قابلة للاستخدام من الملخص المنشور:

- يطبق trend-following على Bitcoin ثم على مجموعة شاملة خالية من survivorship bias منذ 2015.
- يجمع عدة نماذج Donchian ذات مدد مختلفة في إشارة ensemble واحدة بدلاً من الاعتماد على فترة واحدة.
- يستخدم position sizing مبنياً على التقلب، ويطبّق تدويراً على أعلى 20 عملة من حيث السيولة.
- يذكر أن النموذج حقق نتائج صافية من الرسوم مع Sharpe أعلى من 1.5 في الدراسة، لكن هذه نتيجة بحثية تاريخية وليست وعداً، كما أن SSRN ورقة بحثية وليست اعتماداً تنفيذياً.
- يناقش أثر التكاليف وطرق تقليل دوران المحفظة.

## قرار التصميم المقترح

سيُعاد بناء البوت حول **Adaptive Donchian Trend + Volatility Regime + Whale Confirmation**:

1. محلل اتجاه/ترند: ensemble من Donchian قصيرة ومتوسطة وطويلة على 30m/1h/4h وفلتر اتجاه أعلى.
2. محلل نظام السوق: EMA200/ADX/ATR percentile لاختيار trend mode أو no-trade mode، مع خفض النشاط أثناء التذبذب الفوضوي.
3. محلل زخم وبنية: breakout مع تأكيد إغلاق، pullback، RSI/MACD، واستوب ATR متحرك.
4. محلل الحيتان/المشتقات: funding، open interest، volume delta proxy، السيولة والسبريد؛ يُستخدم لكشف crowded longs/shorts وتأكيد الحركة لا لإنتاج إشارة منفردة.

كل رقم أداء يجب أن ينتج من backtest منفصل على بيانات حقيقية، مع walk-forward وفصل زمني وتكاليف وانزلاق. لا يمكن إعلان أن أي استراتيجية هي الأقوى حالياً قبل اختبارها على نفس رموز وبيانات المنصة التي سيستخدمها البوت.

## المصدر 3 — توثيق MEXC الرسمي

العنوان: *Get Funding Rate | MEXC API*

الرابط: https://www.mexc.com/api-docs/futures/market-endpoints/get-funding-rate

النقاط المهمة للتنفيذ:

- نقطة HTTP العامة هي `GET /api/v1/contract/funding_rate/{symbol}`.
- حد الطلبات المعلن هو 20 طلباً خلال ثانيتين.
- الاستجابة تتضمن `fundingRate`, `maxFundingRate`, `minFundingRate`, `collectCycle`, `nextSettleTime`, `idxPrice`, `fairPrice`, و`timestamp`.
- هذا يسمح ببناء محلل مشتقات حقيقي يعتمد على funding الحالي، مع ضرورة تحويل رمز CCXT مثل `BTC/USDT:USDT` إلى رمز عقد MEXC مثل `BTC_USDT` أو استخدام واجهة CCXT الموحّدة والتحقق من شكل الاستجابة.
- funding وحده لا يحدد اتجاه السعر؛ سيُستخدم كفلتر crowding ومخاطر، مثلاً منع LONG عند funding موجب بشكل متطرف مع تضخم OI، أو منع SHORT عند funding سالب بشكل متطرف مع تضخم OI.

## المصدر 4 — توثيق CCXT الرسمي

العنوان: *Manual · ccxt/ccxt Wiki*

الرابط: https://github.com/ccxt/ccxt/wiki/manual

النقاط المهمة:

- CCXT يوفر واجهات موحّدة مثل `fetchOHLCV`, `fetchTicker`, `fetchOpenInterest`, و`fetchFundingRate`، لكن دعم كل دالة يختلف حسب المنصة.
- يجب فحص `exchange.has` قبل استدعاء دوال المشتقات، وعدم افتراض أن MEXC أو أي منصة تعيد كل الحقول بنفس الشكل.
- الأفضل إضافة fallback منظم: إذا تعذر جلب OI أو funding عبر الواجهة الموحّدة، يسجل البوت غياب البيانات ويخفض الثقة أو يمنع الإشارة بدلاً من اختلاق قيمة.

## المصدر 5 — MEXC Contract Ticker

العنوان: *Get Ticker (Contract Market Data) | MEXC API*

الرابط: https://www.mexc.com/api-docs/futures/market-endpoints/get-ticker-contract-market-data

النقاط المهمة:

- نقطة HTTP العامة هي `GET /api/v1/contract/ticker`، وحد الطلبات المعلن 10 طلبات خلال ثانيتين.
- الاستجابة تتضمن `lastPrice`, `bid1`, `ask1`, `volume24`, `amount24`, `holdVol`، و`fundingRate`، إضافة إلى سعر المؤشر والسعر العادل.
- `holdVol` موصوف رسمياً بأنه open interest بالعقود، ولذلك يمكن استخدامه في البوت مع تخزين عينة زمنية وحساب تغير OI بدلاً من الاكتفاء بقيمة لحظية.
- يجب عدم مساواة ارتفاع OI مع شراء أو بيع تلقائياً؛ الاتجاه يُستنتج من الجمع بين تغير السعر وتغير OI وfunding والحجم.

## تحقق حي من MEXC — 25 أغسطس 2026

تمت قراءة بيانات عامة لرمز `BTC/USDT:USDT` دون أي أمر تداول. أعادت MEXC السعر والحجم و`info.holdVol=634450794` و`info.fundingRate=0.0001` في عينة الاختبار، مع وجود `fetchFundingRate=true` و`fetchOpenInterest=false` في CCXT. لذلك يعتمد البوت على `ticker.info` لحقل `holdVol` عند غياب دالة OI الموحّدة، ولا يعتبر غياب الدالة الموحّدة فشلاً ما دامت الاستجابة الرسمية تحتوي الحقل.

تمت إعادة التحقق عبر `fetch_tickers([symbol])`، وكانت استجابة BTC الجماعية تحتوي فعلياً على `info.holdVol=635238701` و`info.fundingRate=0.0001`. هذا يدعم مسار قراءة `ticker.info` في البوت ويؤكد أن طبقة الحيتان قابلة للتشغيل على MEXC مع جمع عينة سابقة للمقارنة.
