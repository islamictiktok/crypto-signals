import os
from datetime import datetime, timezone

class Log:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
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
    PAPER_TRADING = True # Must be True. Live execution is NOT implemented.
    
    # Credentials
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    CHAT_ID = os.getenv("CHAT_ID")
    RENDER_URL = os.getenv("RENDER_URL", "http://localhost:10000")
    
    VERSION = "V63.0 (Quant Master Production)"
    STATE_FILE = "bot_state_v63.json"
    
    # Timeframes
    TREND_TF = '1h'
    SETUP_TF = '15m'
    ENTRY_TF = '5m'
    
    # Market Limits
    TOP_COINS_LIMIT = 50 
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_TRADES_AT_ONCE = 5 
    COOLDOWN_SECONDS = 1800  
    MAX_PROCESSED_SIGNALS = 500
    
    # Strategy Filters
    MIN_SIGNAL_SCORE = 75
    ADX_MIN = 15
    ADX_MAX = 50
    MIN_VOLUME_RATIO = 1.2 
    MIN_WICK_PCT = 0.30
    MIN_CLOSE_POS = 0.60
    BB_LENGTH = 20
    BB_STD = 2.5
    
    # Execution & Risk
    MAX_ENTRY_DEVIATION = 0.003 # 0.3%
    MAX_ALLOWED_SPREAD = 0.005  # 0.5%
    ATR_SL_BUFFER = 0.5
    MIN_ATR_RISK_RATIO = 0.8
    MAX_ATR_RISK_RATIO = 3.0
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 30.0 
    MIN_EXPECTED_ROE = 3.0

    # Backtest Assumptions
    BACKTEST_MAKER_FEE = 0.0002
    BACKTEST_TAKER_FEE = 0.0006
    BACKTEST_SPREAD_PCT = 0.001
    BACKTEST_SLIPPAGE_PCT = 0.0005
