# --- إعدادات التلغرام المحدثة ---
# استبدل التوكن بتوكن بوتك
TELEGRAM_TOKEN = "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg" 
# استبدل هذا الرقم بمعرف القناة الذي يبدأ بـ -100
CHAT_ID = "-1003653652451" 

async def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID, 
        "text": message, 
        "parse_mode": "HTML",
        "disable_web_page_preview": True # لإبقاء الرسالة منظمة بدون روابط معاينة
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload)
            if response.status_code != 200:
                print(f"❌ خطأ من تلجرام: {response.text}")
        except Exception as e:
            print(f"❌ فشل الاتصال بتلجرام: {e}")

# --- دالة start_scanning (تأكد من تعديل شكل الرسالة لتكون جذابة في القناة) ---
async def start_scanning(app):
    print("🛰️ رادار القناة الخاصة بدأ العمل...")
    while True:
        for sym in app.state.symbols:
            side, entry = await get_signal(sym)
            if side:
                current_time = time.time()
                signal_key = f"{sym}_{side}"
                
                # منع التكرار لمدة 15 دقيقة (900 ثانية) لعدم إزعاج المشتركين
                if signal_key not in app.state.sent_signals or (current_time - app.state.sent_signals[signal_key]) > 900:
                    app.state.sent_signals[signal_key] = current_time
                    
                    symbol_clean = sym.split(':')[0].split('/')[0] + "/USDT"
                    tp = round(entry * 1.006, 5) if side == "LONG" else round(entry * 0.994, 5)
                    sl = round(entry * 0.995, 5) if side == "LONG" else round(entry * 1.005, 5)

                    # إعداد نص الرسالة بتنسيق احترافي للقناة
                    msg = (
                        f"📊 <b>إشارة تداول جديدة</b>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"<b>العملة:</b> <code>{symbol_clean}</code>\n"
                        f"<b>النوع:</b> {'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n"
                        f"<b>الدخول:</b> <code>{round(entry, 5)}</code>\n"
                        f"<b>الهدف (TP):</b> <code>{tp}</code>\n"
                        f"<b>الاستوب (SL):</b> <code>{sl}</code>\n"
                        f"━━━━━━━━━━━━━━\n"
                        f"⚡ <b>الرافعة:</b> 20x | <b>الفريم:</b> 5m\n"
                        f"🕒 {time.strftime('%H:%M:%S')}"
                    )
                    
                    # الإرسال للموقع وللقناة
                    await manager.broadcast(json.dumps({"symbol": symbol_clean, "side": side, "entry": round(entry, 5), "tp": tp, "sl": sl}))
                    await send_telegram_msg(msg)
                    print(f"✅ تم النشر في القناة: {symbol_clean}")

        await asyncio.sleep(5)
