import aiohttp
from config import Config, Log

class TelegramBot:
    def __init__(self):
        self.base_url = f"https://api.telegram.org/bot{Config.TELEGRAM_TOKEN}"
        self.session = None

    async def _init_session(self):
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))

    async def send_message(self, text, reply_to=None):
        try:
            await self._init_session()
            payload = {"chat_id": Config.CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_to: payload["reply_to_message_id"] = reply_to
            
            async with self.session.post(f"{self.base_url}/sendMessage", json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get('result', {}).get('message_id')
                else:
                    Log.error("Telegram", f"Failed to send: {await resp.text()}")
        except Exception as e:
            Log.error("Telegram", str(e))
        return None

    async def close(self):
        if self.session: await self.session.close()
