import os

# config.py 설정 - 한국투자증권 API
APP_KEY = "PScWGtBZiBzNdqh4vQ6sLk9X5BbLECogTZ5h"
APP_SECRET = "+rp6W8luqu3hM+zsR7YPcQJ2wHI0JQ018rf2SzSbfzD6/CXjqoF+2TfVHKEu3Hc+LoLLk7u/rsO7kqKZ1RwQWTtue5arW/AGxBQ61l2qeNQy9vBh0QvxGiAbeRmsAgbtMKuteLTyV9z0VRyib1eJtLPVz9Ati4/zFX1NV1YT3aNP9DGdmAY="
ACCOUNT_NUMBER = "50173240"
ACCOUNT_SUFFIX = "01"
IS_PAPER_TRADING = True
BASE_URL = "https://openapivts.koreainvestment.com:29443"
RISK_TOLERANCE = 0.001
TAKE_PROFIT = 0.03
STOCK_CODE = "005930"
STOCK_NAME = "삼성전자"
SHORT_MA = 5
LONG_MA = 2
TRADING_INTERVAL = 300
ORDER_QUANTITY = 1
TELEGRAM_TOKEN = "8741753378:AAFCqXI1Wjd69WUaQgieEvLRLhEfHhYPI9s"
TELEGRAM_CHAT_ID = "8743534470"
NAVER_CLIENT_ID = "RDw8G16ju1bxrRUaoboG"
NAVER_CLIENT_SECRET = "QXxOSkrdFQ"
# Brave Search API key (무료 tier: 월 2,000회) - https://api.search.brave.com
BRAVE_API_KEY = ""

# Google Gemini API key (무료 tier: web search grounding 500 RPD)
# 발급: https://aistudio.google.com/api-keys
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
