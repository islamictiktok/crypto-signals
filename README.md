# Quant Master V61.0 Multi-Timeframe

A sophisticated institutional-grade algorithmic trading signal bot.

## Core Architecture
- **Market Bias:** BTC 1H Filter (EMA50/200 + RSI).
- **Trend Filter:** Coin 1H Trend & ADX check.
- **Setup Area:** Coin 15m Bollinger Bands Pullback.
- **Entry Trigger:** Coin 5m Wick Rejection (>30% wick) with Volume Surge.
- **Risk Management:** Dynamic Leverage calculation (Max 30% Margin Risk). ATR-based Stop Loss. 1:2 R:R Target.

## Deployment on Render
1. Upload this repository.
2. Set Build Command: `pip install -r requirements.txt`
3. Set Start Command: `python main.py`
4. Environment Variables: `TELEGRAM_TOKEN`, `CHAT_ID`, `PORT` (usually automatic).
