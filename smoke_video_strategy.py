import asyncio
import json

from video_fvg_fib_bot import CFG, VideoBot, VideoStrategy


async def main():
    bot = VideoBot()
    try:
        await bot.start()
        frames = await bot.get_frames(CFG.symbol)
        ticker = await bot.ticker(CFG.symbol)
        trade = VideoStrategy.analyze(frames, ticker)
        print(json.dumps({
            "symbol": CFG.symbol,
            "last": ticker.get("last"),
            "signal_found": bool(trade),
            "signal_side": trade.get("side") if trade else None,
            "paper_trading": CFG.paper_trading,
        }, indent=2, default=str))
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
