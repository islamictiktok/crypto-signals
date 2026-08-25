import asyncio
import json
import time

import websockets


async def main():
    counts = {"push.depth": 0, "push.deal": 0, "pong": 0}
    first_depth = None
    async with websockets.connect("wss://contract.mexc.com/edge", ping_interval=None, max_queue=1000) as ws:
        await ws.send(json.dumps({"method": "sub.depth", "param": {"symbol": "BTC_USDT"}}))
        await ws.send(json.dumps({"method": "sub.deal", "param": {"symbol": "BTC_USDT"}}))
        deadline = time.time() + 8
        last_ping = 0.0
        while time.time() < deadline:
            if time.time() - last_ping >= 3:
                await ws.send(json.dumps({"method": "ping"}))
                last_ping = time.time()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1)
            except asyncio.TimeoutError:
                continue
            message = json.loads(raw)
            channel = message.get("channel")
            if channel in counts:
                counts[channel] += 1
            if channel == "push.depth" and first_depth is None:
                first_depth = message
        print(json.dumps({"counts": counts, "first_depth": first_depth}, default=str))


if __name__ == "__main__":
    asyncio.run(main())
