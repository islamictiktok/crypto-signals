import asyncio
import ccxt.async_support as ccxt
import pandas as pd
import os
import time

async def collect_data(symbol="BTC/USDT:USDT"):
    exchange = ccxt.mexc({'enableRateLimit': True})
    print(f"Starting Data Collection for {symbol}...")
    file_name = f"data_collection_{symbol.replace('/', '_').replace(':', '_')}.csv"
    
    if not os.path.exists(file_name):
        with open(file_name, 'w') as f:
            f.write("timestamp,bid,ask,buy_vol,sell_vol,ob_imbalance\n")

    while True:
        try:
            ticker = await exchange.fetch_ticker(symbol)
            trades = await exchange.fetch_trades(symbol, limit=100)
            ob = await exchange.fetch_order_book(symbol, limit=20)
            
            buy_v = sum(t['amount'] for t in trades if t['side'] == 'buy')
            sell_v = sum(t['amount'] for t in trades if t['side'] == 'sell')
            b_depth = sum(b[1] for b in ob['bids'])
            a_depth = sum(a[1] for a in ob['asks'])
            ob_imb = (b_depth - a_depth) / (b_depth + a_depth) if (b_depth + a_depth) > 0 else 0
            
            with open(file_name, 'a') as f:
                f.write(f"{int(time.time())},{ticker['bid']},{ticker['ask']},{buy_v},{sell_v},{ob_imb:.3f}\n")
            
            await asyncio.sleep(5) # Collect every 5s
        except Exception as e:
            print(f"Collector error: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(collect_data())
