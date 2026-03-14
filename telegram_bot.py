import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
import ollama
import config
import kis_api

TELEGRAM_TOKEN = config.TELEGRAM_TOKEN
CHAT_ID = config.TELEGRAM_CHAT_ID
conversation_history = []

# ── 날씨 조회 ─────────────────────────────────────────────────────

# ── 뉴스 조회 ─────────────────────────────────────────────────────

def translate_keyword(keyword):
    """검색어 영문 번역 (API 키 불필요)"""
    try:
        from deep_translator import GoogleTranslator
        result = GoogleTranslator(source="ko", target="en").translate(keyword)
        print(f"[DEBUG] 번역 결과: {result}")
        return result
    except Exception as e:
        print(f"[DEBUG] 번역 실패: {e}")
        return keyword

def get_news(keyword="경제", period="1d", display=10):
    """네이버 뉴스 검색 API로 뉴스 조회"""
    from urllib.parse import quote
    from datetime import datetime, timedelta

    encoded_keyword = quote(keyword)
    url = f"https://openapi.naver.com/v1/search/news.json?query={encoded_keyword}&display={display}&sort=date"
    headers = {
        "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
    }

    res = requests.get(url, headers=headers, timeout=10)
    res.raise_for_status()
    data = res.json()
    items = data.get("items", [])

    # 기간 필터링
    period_days = {"1d": 1, "7d": 7, "1m": 30, "1y": 365}
    days = period_days.get(period, 1)
    cutoff = datetime.now() - timedelta(days=days)

    news_list = []
    for item in items:
        title = item.get("title", "").replace("<b>", "").replace("</b>", "").replace("&quot;", '"')
        link = item.get("originallink") or item.get("link", "")
        pub_date = item.get("pubDate", "")[:16]

        try:
            from email.utils import parsedate_to_datetime
            pub_dt = parsedate_to_datetime(item.get("pubDate", ""))
            if pub_dt.replace(tzinfo=None) < cutoff:
                continue
        except:
            pass

        if title:
            news_list.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
            })

    return news_list

def make_news_file(keyword, period, news_list):
    """뉴스 목록을 HTML 파일로 저장"""
    from datetime import datetime
    period_str = {"1d": "오늘", "7d": "일주일", "1m": "한달", "1y": "1년"}.get(period, "오늘")
    filename = f"/tmp/news_{keyword}_{period}.html"

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{keyword} 뉴스 ({period_str})</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        h1 {{ color: #333; border-bottom: 2px solid #007aff; padding-bottom: 10px; }}
        .meta {{ color: #888; font-size: 0.9em; margin-bottom: 20px; }}
        .news-item {{ background: white; border-radius: 10px; padding: 15px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .news-item a {{ color: #007aff; text-decoration: none; font-size: 1.0em; font-weight: bold; line-height: 1.5; }}
        .news-item a:hover {{ text-decoration: underline; }}
        .date {{ color: #999; font-size: 0.85em; margin-top: 6px; }}
        .count {{ color: #007aff; font-weight: bold; }}
    </style>
</head>
<body>
    <h1>📰 '{keyword}' 뉴스</h1>
    <div class="meta">기간: {period_str} | 생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 총 <span class="count">{len(news_list)}건</span></div>
""")
        for i, item in enumerate(news_list, 1):
            f.write(f"""    <div class="news-item">
        <span style="color:#999">[{i}]</span>
        <a href="{item['link']}" target="_blank">{item['title']}</a>
        <div class="date">📅 {item['pub_date']}</div>
    </div>
""")
        f.write("</body>\n</html>")

    return filename


# ── 명령어 핸들러 ─────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 안녕하세요! KIS 자동매매 + AI 어시스턴트 봇입니다.\n\n"
        "📌 사용 가능한 명령어:\n"
        "/status [종목코드] - 현재가 및 보유 현황\n"
        "/buy - 수동 매수\n"
        "/sell - 수동 매도\n"
        "/news - 최신 경제 뉴스\n"
        "/clear - 대화 초기화\n\n"
        "💬 일반 메시지를 입력하면 AI가 답변합니다!"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        stock_code = context.args[0] if context.args else config.STOCK_CODE
        price = kis_api.get_current_price(stock_code)
        try:
            holdings, deposit = kis_api.get_balance()
            qty = holdings.get(stock_code, 0)
            balance_str = f"보유수량: {qty}주\n예수금: {deposit:,}원\n"
        except:
            balance_str = "보유수량: 조회불가\n"

        msg = (
            f"📊 현재 현황\n"
            f"━━━━━━━━━━━━━━\n"
            f"종목코드: {stock_code}\n"
            f"현재가: {price:,}원\n"
            f"{balance_str}"
            f"모드: {'🟡 모의투자' if config.IS_PAPER_TRADING else '🔴 실전투자'}"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        await update.message.reply_text(f"❌ 조회 실패: {e}")

async def buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = kis_api.get_current_price(config.STOCK_CODE)
        ok, order_no = kis_api.place_order(config.STOCK_CODE, "BUY", config.ORDER_QUANTITY)
        if ok:
            await update.message.reply_text(
                f"✅ 매수 완료!\n"
                f"종목코드: {config.STOCK_CODE}\n"
                f"가격: {price:,}원\n"
                f"수량: {config.ORDER_QUANTITY}주\n"
                f"주문번호: {order_no}"
            )
        else:
            await update.message.reply_text("❌ 매수 실패")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")

async def sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = kis_api.get_current_price(config.STOCK_CODE)
        holdings, _ = kis_api.get_balance()
        qty = holdings.get(config.STOCK_CODE, 0)
        if qty == 0:
            await update.message.reply_text("⚠️ 보유 중인 주식이 없습니다.")
            return
        ok, order_no = kis_api.place_order(config.STOCK_CODE, "SELL", qty)
        if ok:
            await update.message.reply_text(
                f"✅ 매도 완료!\n"
                f"종목코드: {config.STOCK_CODE}\n"
                f"가격: {price:,}원\n"
                f"수량: {qty}주\n"
                f"주문번호: {order_no}"
            )
        else:
            await update.message.reply_text("❌ 매도 실패")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")

async def news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        keyword = context.args[0] if len(context.args) > 0 else "경제"
        period = context.args[1] if len(context.args) > 1 else "1d"

        if period not in ["1d", "7d", "1m", "1y"]:
            await update.message.reply_text(
                "⚠️ 기간은 아래 중 하나를 입력하세요:\n"
                "1d (오늘) / 7d (일주일) / 1m (한달) / 1y (1년)"
            )
            return

        await update.message.reply_text("⏳ 뉴스 조회 중...")
        news_list = get_news(keyword, period, display=10)
        period_str = {"1d": "오늘", "7d": "일주일", "1m": "한달", "1y": "1년"}.get(period, "오늘")

        if not news_list:
            await update.message.reply_text(f"📰 '{keyword}' 관련 뉴스를 찾을 수 없습니다. ({period_str})")
            return

        # 5개 이하면 텔레그램에 바로 표시
        if len(news_list) <= 5:
            msg = f"📰 '{keyword}' 뉴스 ({period_str})\n━━━━━━━━━━━━━━\n\n"
            for item in news_list:
                msg += f"• <a href='{item['link']}'>{item['title']}</a>\n  📅 {item['pub_date']}\n\n"
            await update.message.reply_text(msg, parse_mode="HTML")

        # 5개 초과면 파일로 전송
        else:
            # 텔레그램에 5개만 미리보기
            msg = f"📰 '{keyword}' 뉴스 ({period_str}) - 총 {len(news_list)}건\n━━━━━━━━━━━━━━\n\n"
            for item in news_list[:5]:
                msg += f"• <a href='{item['link']}'>{item['title']}</a>\n  📅 {item['pub_date']}\n\n"
            msg += f"\n📎 전체 {len(news_list)}건은 아래 파일을 확인하세요."
            await update.message.reply_text(msg, parse_mode="HTML")

            # 전체 파일 전송
            filename = make_news_file(keyword, period, news_list)
            with open(filename, "rb") as f:
                await update.message.reply_document(
                    document=f,
                    filename=f"{keyword}_{period}_뉴스.html",
                    caption=f"📰 '{keyword}' 전체 뉴스 ({period_str}) - {len(news_list)}건"
                )

    except Exception as e:
        await update.message.reply_text(f"❌ 뉴스 조회 실패: {e}")

async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global conversation_history
    conversation_history = []
    await update.message.reply_text("🗑️ 대화 히스토리가 초기화되었습니다.")

# ── 메인 실행 ─────────────────────────────────────────────────────

# ── 자동매매 신호 알림 ─────────────────────────────────────────────────────

def send_telegram_message(message):
    """텔레그램으로 메시지 전송 (비동기 없이 동기 방식으로)"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print(f"✅ 텔레그램 메시지 전송 성공: {message[:50]}...")
            return True
        else:
            print(f"❌ 텔레그램 전송 실패: {response.text}")
            return False
    except Exception as e:
        print(f"❌ 텔레그램 전송 오류: {e}")
        return False

def run_bot():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("news", news))
    app.add_handler(CommandHandler("clear", clear))
    print("✅ 텔레그램 봇 시작됨")
    app.run_polling()

if __name__ == "__main__":
    run_bot()
