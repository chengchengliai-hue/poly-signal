import os
from dotenv import load_dotenv

load_dotenv()

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
PROXY = os.getenv("POLYMARKET_PROXY", "").strip()
DATA_API_KEY = os.getenv("POLYMARKET_DATA_API_KEY", "").strip()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()

MIN_TRADE_USDC = float(os.getenv("MIN_TRADE_USDC", "2000"))
TEST_HEAVY_MIN_USDC = float(os.getenv("TEST_HEAVY_MIN_USDC", "2000"))
TEST_HEAVY_MEDIAN_MULTIPLE = float(os.getenv("TEST_HEAVY_MEDIAN_MULTIPLE", "10"))
TEST_HEAVY_MIN_ORDERS = int(os.getenv("TEST_HEAVY_MIN_ORDERS", "3"))
TEST_HEAVY_MAX_ORDERS = int(os.getenv("TEST_HEAVY_MAX_ORDERS", "50"))
SIGNAL_CONFIRM_DELAY_SECONDS = int(os.getenv("SIGNAL_CONFIRM_DELAY_SECONDS", "45"))
COPY_TRADE_AMOUNT = float(os.getenv("COPY_TRADE_AMOUNT", "5"))
COPY_TRADE_BOOST = float(os.getenv("COPY_TRADE_BOOST", "10"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "50"))
MIN_SHARES = float(os.getenv("MIN_SHARES", "5"))
VIRTUAL_COPY_TRADING = os.getenv("VIRTUAL_COPY_TRADING", "true").lower() in ("1", "true", "yes", "on")
RELATED_WINDOW_MINUTES = int(os.getenv("RELATED_WINDOW_MINUTES", "30"))
RELATED_MARKET_BONUS = int(os.getenv("RELATED_MARKET_BONUS", "10"))
RELATED_MULTI_WALLET_BONUS = int(os.getenv("RELATED_MULTI_WALLET_BONUS", "20"))

SQLITE_PATH = os.getenv("SQLITE_PATH", "data/signal.db")
CLOB_URL = "https://clob.polymarket.com"
CHAIN_ID = 137
