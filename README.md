# Quant Master V61.1 Strict Multi-Timeframe

A sophisticated institutional-grade algorithmic trading signal bot. Focuses strictly on high-probability setups by validating multi-timeframe alignment before triggering.

## Core Architecture (Strict Gates)
1. **Mandatory Bias:** BTC 1H Filter (EMA50/200 + RSI). Rejects mismatch.
2. **Mandatory Trend:** Coin 1H Trend & ADX bounds (15-50). Rejects mismatch.
3. **Mandatory Setup:** Coin 15m Bollinger Bands True Pullback. Rejects mismatch.
4. **Mandatory Trigger:** Coin 5m Wick Rejection (>30% wick) with Volume Surge (>1.2x).
5. **Score Generation:** Base 70, bonuses for exceptional wick size, volume, and momentum.
6. **Risk Management:** Paper execution. Dynamic Leverage (Max 30% Margin). ATR-based SL. 1:2 Target.

## Deployment on Render
1. Upload this repository.
2. Build Command: `pip install -r requirements.txt`
3. Start Command: `python main.py`
4. **CRITICAL Environment Variables:** `TELEGRAM_TOKEN`, `CHAT_ID`

*Note: Render uses an ephemeral filesystem on free/standard tiers. The `bot_state_v61_1.json` may reset upon forced redeploys unless attached to a Persistent Disk.*
