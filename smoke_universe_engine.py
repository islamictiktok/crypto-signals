import asyncio
import json

from video_fvg_fib_bot import CFG, VideoBot, VideoStrategy


async def main():
    bot = VideoBot()
    try:
        await bot.start()
        tickers = {symbol: dict(data) for symbol, data in (await bot.exchange.fetch_tickers()).items()}
        symbols = bot.universe_symbols(tickers)[:3]
        results = []
        for symbol in symbols:
            try:
                frames = await bot.get_frames(symbol)
                ticker = dict(tickers[symbol])
                ticker["symbol"] = symbol
                trade = VideoStrategy.analyze(frames, ticker)
                results.append({"symbol": symbol, "signal": bool(trade), "side": trade.get("side") if trade else None})
            except Exception as exc:
                results.append({"symbol": symbol, "error": str(exc)})
        print(json.dumps({"symbols_checked": symbols, "results": results, "paper_only": True}, indent=2, default=str))
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
