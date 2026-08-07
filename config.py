import os

class Config:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8506270736:AAF676tt1RM4X3lX-wY1Nb0nXlhNwUmwnrg")
    CHAT_ID = os.getenv("CHAT_ID", "-1003653652451")
    RENDER_URL = "https://crypto-signals-w9wx.onrender.com"
    
    VERSION = "V61.0 (Multi-Timeframe Quant)"
    STATE_FILE = "bot_state_v61.json"
    
    # Timeframes
    TREND_TF = '1h'
    SETUP_TF = '15m'
    ENTRY_TF = '5m'
    
    # Coin Selection
    TOP_COINS_LIMIT = 50 
    MIN_24H_VOLUME_USDT = 15_000_000 
    MAX_ALLOWED_SPREAD = 0.005 
    
    # System Limits
    MAX_TRADES_AT_ONCE = 5 
    COOLDOWN_SECONDS = 1800  
    
    # Strategy & Thresholds
    MIN_SIGNAL_SCORE = 75
    ADX_MIN = 15
    ADX_MAX = 45
    MIN_VOLUME_RATIO = 1.2 # الفوليوم أعلى من المتوسط بـ 20%
    MIN_WICK_PCT = 0.30
    BB_LENGTH = 20
    BB_STD = 2.5
    
    # Money Management (Strictly Unchanged)
    MIN_LEVERAGE = 2  
    MAX_LEVERAGE_CAP = 50 
    MAX_MARGIN_RISK_PCT = 30.0
