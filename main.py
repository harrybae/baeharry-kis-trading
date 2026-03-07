# main.py - 한국투자증권 KIS API 자동매매 봇

import logging
import time
from datetime import datetime, timedelta

from src.data_collector import get_access_token, fetch_stock_data
from src.strategy import moving_average_crossover_strategy
from src.risk_manager import manage_risk

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.config import (
    APP_KEY, APP_SECRET, ACCOUNT_NUMBER, ACCOUNT_SUFFIX,
    BASE_URL, IS_PAPER_TRADING, RISK_TOLERANCE, STOCK_CODE
)
import requests

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
        mode = "모의투자" if IS_PAPER_TRADING else "실전투자"
        logging.info(f"TradingBot 초기화 완료 [{mode}]")
        print(f"✅ TradingBot 시작 [{mode}]")

    def login(self):
        """KIS API 토큰 발급"""
        self.access_token = get_access_token(BASE_URL, APP_KEY, APP_SECRET)
        if self.access_token:
            logging.info("KIS API 로그인 성공")
            print("✅ KIS API 로그인 성공")
        else:
            raise Exception("KIS API 로그인 실패 - App Key/Secret 확인 필요")

    def get_current_price(self, stock_code):
        """현재가 조회"""
        url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHKST01010100",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
        }
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        return int(data["output"]["stck_prpr"])

    def place_order(self, stock_code, quantity, direction):
        """주문 실행"""
        url = f"{BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
        
        # 모의/실전 tr_id 구분
        if IS_PAPER_TRADING:
            tr_id = "VTTC0802U" if direction == "BUY" else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if direction == "BUY" else "TTTC0801U"

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": tr_id,
        }
        body = {
            "CANO": ACCOUNT_NUMBER,
            "ACNT_PRDT_CD": ACCOUNT_SUFFIX,
            "PDNO": stock_code,
            "ORD_DVSN": "01",  # 시장가
            "ORD_QTY": str(quantity),
            "ORD_UNPR": "0",
        }
        response = requests.post(url, headers=headers, json=body)
        data = response.json()
        
        if data.get("rt_cd") == "0":
            logging.info(f"{direction} 주문 성공: {stock_code} {quantity}주")
            print(f"✅ {direction} 주문 성공: {stock_code} {quantity}주")
        else:
            logging.error(f"{direction} 주문 실패: {data.get('msg1')}")
            print(f"❌ {direction} 주문 실패: {data.get('msg1')}")

    def start_trading(self):
        """메인 트레이딩 로직"""
        try:
            print(f"📊 {STOCK_CODE} 데이터 수집 중...")
            df = fetch_stock_data(STOCK_CODE, BASE_URL, APP_KEY, APP_SECRET, self.access_token)
            df = moving_average_crossover_strategy(df)

            latest = df.iloc[-1]
            position = latest["Position"]
            current_price = self.get_current_price(STOCK_CODE)

            print(f"💰 현재가: {current_price:,}원 | 포지션: {position}")
            logging.info(f"현재가: {current_price} | 포지션: {position}")

            risk_status = manage_risk(position, current_price, RISK_TOLERANCE)

            if risk_status == "STOP_LOSS":
                self.place_order(STOCK_CODE, 1, "SELL")
                logging.info("손절매 실행")
            elif position == 1:
                self.place_order(STOCK_CODE, 1, "BUY")
            elif position == -1:
                self.place_order(STOCK_CODE, 1, "SELL")
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
            # 장 운영 시간: 09:00 ~ 15:30
            if now.weekday() < 5 and 9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30):
                self.start_trading()
                time.sleep(60)  # 1분마다 실행
            else:
                print(f"💤 장 마감 시간 ({now.strftime('%H:%M')}) - 대기 중...")
                time.sleep(300)  # 5분마다 체크


if __name__ == "__main__":
    bot = TradingBot()
    bot.run()
