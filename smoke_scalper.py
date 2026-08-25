import asyncio
import json

from scalper_consensus_bot import ScalperBot


async def main():
    bot = ScalperBot()
    await bot.start()
    stream_task = asyncio.create_task(bot.stream.run())
    try:
        deadline = asyncio.get_running_loop().time() + 20
        while not bot.stream.connected and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.5)
        symbol = bot.symbols[0] if bot.symbols else "BTC/USDT:USDT"
        snapshot = bot.stream.snapshot(symbol)
        print(json.dumps({
            "symbol": symbol,
            "symbols_loaded": len(bot.symbols),
            "ws_connected": bot.stream.connected,
            "book_age": snapshot.get("book_age"),
            "flow_age": snapshot.get("flow_age"),
            "depth_usdt": snapshot.get("depth_usdt"),
        }, indent=2, default=str))
    finally:
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
