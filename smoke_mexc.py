import asyncio
import json

import ccxt.async_support as ccxt


async def main():
    exchange = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    try:
        await exchange.load_markets()
        symbol = "BTC/USDT:USDT" if "BTC/USDT:USDT" in exchange.markets else next(
            s for s, m in exchange.markets.items() if s.endswith("/USDT:USDT")
        )
        ticker = await exchange.fetch_ticker(symbol)
        bulk = (await exchange.fetch_tickers([symbol])).get(symbol, {})
        candles = await exchange.fetch_ohlcv(symbol, "30m", limit=5)
        print(json.dumps({
            "symbol": symbol,
            "last": ticker.get("last"),
            "quoteVolume": ticker.get("quoteVolume"),
            "has_info": isinstance(ticker.get("info"), dict),
            "info_holdVol": (ticker.get("info") or {}).get("holdVol"),
            "info_fundingRate": (ticker.get("info") or {}).get("fundingRate"),
            "bulk_info_holdVol": (bulk.get("info") or {}).get("holdVol"),
            "bulk_info_fundingRate": (bulk.get("info") or {}).get("fundingRate"),
            "candle_count": len(candles),
            "has_fetchFundingRate": bool(exchange.has.get("fetchFundingRate")),
            "has_fetchOpenInterest": bool(exchange.has.get("fetchOpenInterest")),
        }, indent=2, default=str))
    finally:
        await exchange.close()


if __name__ == "__main__":
    asyncio.run(main())
