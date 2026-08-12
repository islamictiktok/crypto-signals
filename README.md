# Quant Master V64.0 (Whale Flow Engine)

Institutional-grade, strict multi-timeframe algorithm. Shifts from lagging technical indicators to real-time market microstructure (Order Flow, Orderbook Imbalance, Liquidity Sweeps).

## 🚀 Architecture
- **Market Context:** Sweep Detection (Price pierced Swing Low + Reclaimed).
- **Flow Analysis:** Aggressive Taker Buy/Sell Vol > 65%.
- **Liquidity Analysis:** OB Imbalance > +0.2.
- **Risk Engine:** ATR-based SL + `MIN_LEVERAGE`. Fixed 1:2 R:R. Strict 10% Margin Risk limit.
- **Data Integrity:** Strict Closed Candle Alignment to prevent Look-Ahead Bias.

## ⚙️ Setup & Run
1. Export variables: `export TELEGRAM_TOKEN="YOUR_TOKEN"` & `export CHAT_ID="YOUR_ID"`
2. Install: `pip install -r requirements.txt`
3. Run Live (Paper): `uvicorn main:app --host 0.0.0.0 --port 10000`

## ⚠️ Limitations & Security
- **Paper Trading Only:** Does not execute live API market/limit orders. Hardcoded `PAPER_TRADING = True`.
- **Backtesting:** True order flow backtesting requires tick data. Run `data_collector.py` to build the required dataset before running `backtest.py`.
