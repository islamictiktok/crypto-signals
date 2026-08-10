import os
from datetime import datetime, timezone

class Log:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{color}[INFO] [{ts}] {msg}{Log.RESET}", flush=True)

    @staticmethod
    def error(func, msg):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{Log.RED}[ERROR] [{ts}] {func}: {msg}{Log.RESET}", flush=True)

class Config:
    PAPER_TRADING = True 
    
    # Credentials
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")
    
    VERSION = "V64.0 (Golden Key Strategy)"
    STATE_FILE = "bot_state_v64.json"
    
    # 📌 الفريم الزمني كما في الصورة (شارت اليوم)
    TF_MAIN = '1d' 
    
    # 📌 إعدادات استراتيجية المفتاح الذهبي
    EMA_FAST = 5
    EMA_SLOW = 12
    RSI_LEN = 21
    
    # Market Limits
    TOP_COINS_LIMIT = 50 
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_TRADES_AT_ONCE = 5 
    COOLDOWN_SECONDS = 3600  
    
    # Risk Limits (Safety Nets)
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 30.0 
    
    # أهداف وستوب افتراضية للأمان (الخروج الأساسي سيكون ديناميكي حسب التقاطع العكسي)
    DEFAULT_SL_PCT = 0.10 
    DEFAULT_TP_PCT = 0.20
