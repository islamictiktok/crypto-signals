import asyncio
import json

import ccxt.async_support as ccxt

from video_fvg_fib_bot import select_universe_symbols


async def main():
    exchange = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    try:
        await exchange.load_markets()
        tickers = {symbol: dict(data) for symbol, data in (await exchange.fetch_tickers()).items()}
        symbols = select_universe_symbols(
            exchange.markets,
            tickers,
            True,
            "BTC/USDT:USDT",
            20,
            5_000_000,
        )
        print(json.dumps({
            "markets_loaded": len(exchange.markets),
            "tickers_received": len(tickers),
            "eligible_top_20": len(symbols),
            "symbols": symbols,
            "execution_calls": False,
        }, indent=2, default=str))
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
