# بحث استراتيجية السكالب 5–10 دقائق — 25 أغسطس 2026

## الخلاصة

لا توجد طريقة تضمن وصول كل صفقة إلى الهدف خلال 5 أو 10 دقائق. الأفق القصير جداً يتطلب بيانات order book/trades لحظية وتكاليف تنفيذ منخفضة، وليس مجرد شموع 30 دقيقة. أفضل منهج قابل للتحويل إلى بوت هو **event-driven microstructure scalp**: اتجاه قصير من 1m/3m، ثم تأكيد اختلال دفتر الأوامر وتدفق الصفقات، مع فلتر spread/volume/volatility، وهدف قريب بعد خصم الرسوم والانزلاق، وخروج زمني إذا لم تتحرك الصفقة بسرعة.

## المصدر 1

العنوان: *Explainable Patterns in Cryptocurrency Microstructure*

الرابط: https://arxiv.org/abs/2602.00776

الدراسة تستخدم دفاتر أوامر وتداولات لعقود Binance Futures على تردد ثانية واحدة، من 2022 إلى 2025، وتختبر order-flow imbalance والسبريد والانتقاء العكسي، مع backtests منفصلة لـ taker وmaker. النتيجة المهمة للتصميم هي أن سكالب الدقائق يحتاج بيانات order book/trade flow حقيقية، وأن سلوك maker مختلف عن taker عند الصدمات السريعة. الدراسة حديثة أولية وليست ضماناً للأداء.

## المصدر 2

العنوان: *How investible is Bitcoin? Analyzing the liquidity and transaction costs of Bitcoin markets*

الرابط: https://www.sciencedirect.com/science/article/abs/pii/S0165176518302921

الدراسة تفحص تكاليف التداول والأنماط intraday في Bitcoin، وتشير إلى أن التكلفة الضمنية والسيولة تختلف مع الوقت، وأن النشاط والحجم مرتبطان بالتذبذب. لذلك يجب أن يمنع البوت الدخول عندما يكون السبريد أو الانزلاق المتوقع أكبر من جزء جوهري من الهدف.

## تصميم السكالب المقترح

المحلل الأول يحدد اتجاه 15m و5m باستخدام EMA/VWAP وDonchian قصيرة. المحلل الثاني يبحث عن impulse أو breakout على 1m/3m مع ATR صغير بما يكفي لتحقيق الهدف ضمن نافذة 5–10 دقائق. المحلل الثالث يحسب order-book imbalance من أفضل مستويات bid/ask وtrade-flow imbalance من الصفقات الأخيرة. المحلل الرابع يراقب funding/OI/volume ويفحص crowding، لكنه لا يحل محل دفتر الأوامر. المحلل الخامس هو execution/risk gate الذي يرفض الصفقة إذا لم يكن الهدف المتوقع أكبر من الرسوم والسبريد والانزلاق بهامش مناسب.

## قواعد الخروج

يتم وضع TP وSL قبل الدخول. يوجد hard time stop عند 10 دقائق، وsoft time stop مبكر عند 5 دقائق إذا لم تحقق الصفقة حد التقدم المطلوب. إذا تحركت الصفقة بسرعة إلى ربح جزئي، يُنقل الاستوب إلى التعادل بعد خصم التكلفة، مع trailing صغير مناسب لتذبذب 1m/3m. إذا ظهرت إشارة microstructure معاكسة قوية، يتم الخروج قبل الوقت الأقصى.

## القيود التنفيذية

البوت الحالي مبني على REST و`fetch_tickers` وOHLCV، وهذا ليس كافياً لسكالب حقيقي عالي الجودة. يلزم WebSocket للـ order book/trades أو polling سريع جداً، مع حساب latency وstaleness. كما يلزم backtest tick/order-book أو على الأقل trade-level؛ backtest الشموع وحده قد يعطي نتيجة متفائلة وغير قابلة للتنفيذ.

## المصدر 3 — MEXC Futures WebSocket الرسمي

العنوان: *Order book depth* والرابط: https://www.mexc.com/api-docs/futures/websocket-api/order-book-depth

- الاشتراك يتم عبر `sub.depth` مع رمز عقد مثل `BTC_USDT`.
- تحديثات دفتر الأوامر تُدفع كل 200ms، وتحتوي `asks`, `bids`, `version`, و`cts` من محرك المطابقة.
- كل مستوى يتضمن السعر والعدد والكمية، ويمكن حساب imbalance على أفضل 5 أو 10 مستويات.

العنوان: *Deal* والرابط: https://www.mexc.com/api-docs/futures/websocket-api/deal

- الاشتراك يتم عبر `sub.deal` ولا يحتاج تسجيل دخول.
- البيانات تصل عند حدوث الصفقات وتتضمن السعر `p`، الكمية `v`، واتجاه الصفقة `T` حيث 1 شراء و2 بيع، إضافة إلى حالة فتح/تقليل المركز `O` ووقت الصفقة.
- هذه القناة مناسبة لحساب trade-flow imbalance خلال نافذة ثوانٍ أو دقائق، مع ضرورة إعادة الاتصال والتحقق من زمن البيانات.

قرار التنفيذ: إضافة جامع WebSocket خاص بـ MEXC يحتفظ بحالة order book وأحدث الصفقات للرموز المرشحة، ويعطي محلل microstructure إشارة فقط إذا كانت البيانات حديثة والسبريد والعمق مناسبين. لا يكفي REST وحده لهدف 5–10 دقائق.

## المصدر 4 — اتصال MEXC وheartbeat

العنوان: *Native ws endpoint* والرابط: https://www.mexc.com/api-docs/futures/websocket-api/native-ws-endpoint

- نقطة الاتصال الرسمية لعقود Futures هي `wss://contract.mexc.com/edge`.

العنوان: *Command details for data exchange* والرابط: https://www.mexc.com/api-docs/futures/websocket-api/command-details-for-data-exchange

- يرسل العميل `{\"method\":\"ping\"}`.
- إذا لم يصل ping خلال دقيقة تُغلق الجلسة؛ التوثيق يوصي بإرسال ping كل 10–20 ثانية.
- الاشتراكات العامة لا تحتاج مصادقة.
