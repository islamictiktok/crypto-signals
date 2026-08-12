import os
from datetime import datetime, timezone

class Log:
    @staticmethod
    def _print(level, category, msg, color):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"{color}[{level}] [{ts}] [{category}] {msg}\033[0m", flush=True)

    @staticmethod
    def info(category, msg): Log._print("INFO", category, msg, "\033[92m")
    
    @staticmethod
    def error(category, msg): Log._print("ERROR", category, msg, "\033[91m")
    
    @staticmethod
    def warn(category, msg): Log._print("WARN", category, msg, "\033[93m")

class Config:
    PAPER_TRADING = True # إجباري
    
    # Credentials
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    
    VERSION = "V64.0 (Whale Flow Engine)"
    STATE_FILE = "bot_state_v64.json"
    
    # Timeframes (هذه هي المتغيرات التي كانت ناقصة)
    TREND_TF = '1h'
    SETUP_TF = '15m'
    ENTRY_TF = '5m'
    
    # Market Limits
    TOP_COINS_LIMIT = 50 
    MIN_24H_VOLUME_USDT = 10_000_000 
    MAX_TRADES_AT_ONCE = 5 
    COOLDOWN_SECONDS = 1800
    MAX_PROCESSED_SIGNALS = 500
    
    # Strategy & Flow Thresholds
    MIN_WHALE_SCORE = 80
    MIN_RVOL = 1.2
    OB_DEPTH_LIMIT = 20
    TRADE_HISTORY_LIMIT = 500
    
    # Risk Limits
    MAX_ENTRY_DEVIATION = 0.003
    MAX_ALLOWED_SPREAD = 0.005 
    ATR_SL_BUFFER = 0.5
    MIN_ATR_RISK_RATIO = 0.8
    MAX_ATR_RISK_RATIO = 3.0
    RR_TARGET = 2.0
    
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 10.0 # إدارة مخاطر صارمة
