# Quant Master V63.0 - Production / Paper Trading

Institutional-grade, strict multi-timeframe algorithm. Fully event-driven to ensure zero look-ahead bias and mathematical precision in risk management.

## 🚀 Setup & Run
1. Set env vars: `TELEGRAM_TOKEN`, `CHAT_ID`
2. Install: `pip install -r requirements.txt`
3. Run Live (Paper): `python main.py`
4. Run Backtest: `python backtest.py` (Requires CSV data feeding)

## ⚖️ Features
- **Strict Data Alignment:** Indicators ONLY use closed candles `iloc[-1]` after slicing `[:-1]`.
- **Accurate Spread:** Validation on actual Ask/Bid before entry.
- **Robust Risk:** SL calculated via Swing + ATR Buffer. Fixed 1:2 R:R.
- **Atomic State:** Protection against JSON corruption via tempfiles.
