# main.py - 한국투자증권 KIS API 자동매매 봇

import logging
import time
import os
import sys
from datetime import datetime, timedelta

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import kis_api
import strategy
import requests
import telegram_bot

# 로깅 설정
logging.basicConfig(
    filename='trading_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class TradingBot:
    def __init__(self):
        self.access_token = None
        self.position = 0
        self.last_report_time = datetime.now()
        self.report_interval = timedelta(minutes=30)
        mode = "모의투자" if config.IS_PAPER_TRADING else "실전투자"
        logging.info(f"TradingBot 초기화 완료 [{mode}]")
        print(f"✅ TradingBot 시작 [{mode}]")

    def login(self):
        """KIS API 토큰 발급"""
        self.access_token = kis_api.get_access_token()
        if self.access_token:
            logging.info("KIS API 로그인 성공")
            print("✅ KIS API 로그인 성공")
        else:
            raise Exception("KIS API 로그인 실패 - App Key/Secret 확인 필요")

    def get_current_price(self, stock_code):
        """현재가 조회"""
        try:
            return kis_api.get_current_price(stock_code)
        except Exception as e:
            logging.error(f"현재가 조회 실패: {e}")
            return 0

    def place_order(self, stock_code, quantity, direction):
        """주문 실행"""
        try:
            success, order_no = kis_api.place_order(stock_code, direction, quantity)
            if success:
                logging.info(f"{direction} 주문 성공: {stock_code} {quantity}주")
                print(f"✅ {direction} 주문 성공: {stock_code} {quantity}주")
            else:
                logging.error(f"{direction} 주문 실패: {order_no}")
                print(f"❌ {direction} 주문 실패: {order_no}")
            return success
        except Exception as e:
            logging.error(f"주문 오류: {e}")
            print(f"❌ 주문 오류: {e}")
            return False

    def start_trading(self):
        """메인 트레이딩 로직"""
        try:
            stock_code = config.STOCK_CODE
            print(f"📊 {stock_code} 데이터 수집 중...")
            
            # KIS API에서 일봉 데이터 조회 (직접 구현)
            from datetime import datetime, timedelta
            end = datetime.now()
            start = end - timedelta(days=90)
            
            url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
            headers = {
                "Authorization": f"Bearer {kis_api.get_access_token()}",
                "appkey": config.APP_KEY,
                "appsecret": config.APP_SECRET,
                "tr_id": "FHKST03010100",
                "Content-Type": "application/json; charset=utf-8",
            }
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }
            response = requests.get(url, headers=headers, params=params)
            data = response.json()
            candles = list(reversed(data.get("output2", [])))
            prices = [int(c["stck_clpr"]) for c in candles if c.get("stck_clpr")]
            
            if len(prices) < config.LONG_MA:
                print("📌 데이터 부족 - 관망")
                logging.info("데이터 부족 - 관망")
                return

            # 전략 신호取得
            signal = strategy.get_signal(prices)
            
            # 현재가 조회
            current_price = self.get_current_price(stock_code)
            if current_price == 0:
                print("📌 현재가 조회 실패 - 관망")
                logging.info("현재가 조회 실패 - 관망")
                return

            print(f"💰 현재가: {current_price:,}원 | 신호: {signal}")
            logging.info(f"현재가: {current_price} | 신호: {signal}")

            # 손절/익절 체크를 위한 보유 정보 조회
            holdings, deposit = kis_api.get_balance()
            qty = holdings.get(stock_code, 0)
            avg_cost = 0  # 평균단가 계산 필요

            # 손절 체크
            if qty > 0 and avg_cost > 0:
                if strategy.check_stop_loss(avg_cost, current_price):
                    print("🛑 손절 조건 충족 - 매도")
                    self.place_order(stock_code, qty, "SELL")
                    logging.info("손절매 실행")
                    return

                if strategy.check_take_profit(avg_cost, current_price):
                    print("🎯 익절 조건 충족 - 매도")
                    self.place_order(stock_code, qty, "SELL")
                    logging.info("익절매 실행")
                    return

            # 매수 신호
            if signal == "BUY" and qty == 0:
                ma_short = strategy.calc_ma(prices, config.SHORT_MA)
                ma_long = strategy.calc_ma(prices, config.LONG_MA)
                msg = f"📈 [{config.STOCK_NAME}] BUY 신호 감지!\n"                       f"현재가: {current_price:,}원\n"                       f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n"                       f"MA{config.LONG_MA}: {ma_long:,.0f}원\n"                       f"신호: 골든크로스 (단기 > 장기)"
                print("📈 매수 신호 - 골든크로스")
                print(msg)
                try:
                    telegram_bot.send_telegram_message(msg)
                except Exception as e:
                    print(f"텔레그램 전송 실패: {e}")
                logging.info(f"BUY 신호 감지 - {msg}")
                # self.place_order(stock_code, config.ORDER_QUANTITY, "BUY")
            
            # 매도 신호
            elif signal == "SELL" and qty > 0:
                ma_short = strategy.calc_ma(prices, config.SHORT_MA)
                ma_long = strategy.calc_ma(prices, config.LONG_MA)
                msg = f"📉 [{config.STOCK_NAME}] SELL 신호 감지!\n"                       f"현재가: {current_price:,}원\n"                       f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n"                       f"MA{config.LONG_MA}: {ma_long:,.0f}원\n"                       f"신호: 데드크로스 (단기 < 장기)"
                print("📉 매도 신호 - 데드크로스")
                print(msg)
                try:
                    telegram_bot.send_telegram_message(msg)
                except Exception as e:
                    print(f"텔레그램 전송 실패: {e}")
                logging.info(f"SELL 신호 감지 - {msg}")
                # self.place_order(stock_code, qty, "SELL")
            
            else:
                print("📌 관망 중...")
                logging.info("관망 중 - 포지션 없음")

        except Exception as e:
            logging.error(f"트레이딩 오류: {str(e)}")
            print(f"❌ 오류 발생: {str(e)}")
        finally:
            self.check_report_schedule()

    def generate_report(self):
        current_time = datetime.now()
        elapsed = current_time - self.last_report_time
        report = f"\n{'='*40}\n"
        report += f"[트레이딩 보고서] {current_time.strftime('%Y-%m-%d %H:%M')}\n"
        report += f"📅 마지막 보고서: {self.last_report_time.strftime('%Y-%m-%d %H:%M')}\n"
        report += f"⏳ 경과 시간: {elapsed}\n"
        report += f"📝 다음 보고서: {(self.last_report_time + self.report_interval).strftime('%Y-%m-%d %H:%M')}\n"
        report += f"{'='*40}\n"
        return report

    def check_report_schedule(self):
        current_time = datetime.now()
        if (current_time - self.last_report_time) >= self.report_interval:
            report = self.generate_report()
            logging.info(report)
            print(report)
            self.last_report_time = current_time

    def run(self):
        """봇 실행 루프"""
        self.login()
        print("🤖 자동매매 봇 시작! (종료: Ctrl+C)")
        while True:
            now = datetime.now()
            # 장 운영 시간: 09:00 ~ 15:30 (평일)
            market_open = now.weekday() < 5 and 9 <= now.hour < 15
            market_close = now.weekday() < 5 and now.hour == 15 and now.minute <= 30
            
            if market_open or market_close:
                self.start_trading()
                time.sleep(config.TRADING_INTERVAL)  # 설정 간격으로 실행
            else:
                print(f"💤 장 마감 시간 ({now.strftime('%H:%M')}) - 대기 중...")
                time.sleep(300)  # 5분마다 체크


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
