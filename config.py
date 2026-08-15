import os
from dotenv import load_dotenv

# .env 파일이 있으면 환경변수로 로드
load_dotenv()

# 한국투자증권 API
APP_KEY = os.getenv("APP_KEY", "")
APP_SECRET = os.getenv("APP_SECRET", "")
ACCOUNT_NUMBER = os.getenv("ACCOUNT_NUMBER", "")
ACCOUNT_SUFFIX = os.getenv("ACCOUNT_SUFFIX", "01")
IS_PAPER_TRADING = os.getenv("IS_PAPER_TRADING", "True").lower() in ("true", "1", "yes")
BASE_URL = os.getenv("BASE_URL", "https://openapivts.koreainvestment.com:29443")

# 매매 설정
RISK_TOLERANCE = float(os.getenv("RISK_TOLERANCE", "0.001"))
TAKE_PROFIT = float(os.getenv("TAKE_PROFIT", "0.03"))
STOCK_CODE = os.getenv("STOCK_CODE", "005930")
STOCK_NAME = os.getenv("STOCK_NAME", "삼성전자")
SHORT_MA = int(os.getenv("SHORT_MA", "5"))
LONG_MA = int(os.getenv("LONG_MA", "20"))
TRADING_INTERVAL = int(os.getenv("TRADING_INTERVAL", "300"))
ORDER_QUANTITY = int(os.getenv("ORDER_QUANTITY", "1"))

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Naver OpenAPI
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# Flask
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-key")
