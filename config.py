import os
from datetime import datetime, timezone

class Log:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'

    @staticmethod
    def print(msg, color=RESET):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        print(f"{color}[{ts}] {msg}{Log.RESET}", flush=True)

class Config:
    # Telegram Security (Must be set in Environment Variables)
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    RENDER_URL = os.getenv("RENDER_URL", "https://crypto-signals-w9wx.onrender.com")
    
    VERSION = "V61.1 (Strict Multi-Timeframe)"
    STATE_FILE = "bot_state_v61_1.json"
    
    # Timeframes
    TREND_TF = '1h'
    SETUP_TF = '15m'
    ENTRY_TF = '5m'
    
    # Market Selection
    TOP_COINS_LIMIT = 50 
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_ALLOWED_SPREAD = 0.005 
    
    # System Limits
    MAX_TRADES_AT_ONCE = 5 
    COOLDOWN_SECONDS = 1800  
    MAX_PROCESSED_SIGNALS = 500 # Prevent memory leak
    
    # Strategy & Thresholds
    MIN_SIGNAL_SCORE = 70
    ADX_MIN = 15
    ADX_MAX = 50
    MIN_VOLUME_RATIO = 1.2 
    MIN_WICK_PCT = 0.30
    BB_LENGTH = 20
    BB_STD = 2.5
    
    # Risk Management (Strictly Unchanged per instructions)
    ATR_SL_BUFFER_MULTIPLIER = 0.5
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 30.0
