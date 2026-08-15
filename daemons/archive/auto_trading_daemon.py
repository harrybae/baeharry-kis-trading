#!/usr/bin/env python3
# auto_trading_daemon.py - 자동매매 신호 모니터링 데몬

import logging
import time
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import kis_api
import strategy
import requests
import telegram_bot

# 로그 파일에 동시에 출력
log_file = open('auto_trading_daemon.log', 'a', encoding='utf-8')

def print_log(msg):
    """stdout + 파일에 동시 출력"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    log_file.write(formatted_msg + '\n')
    log_file.flush()

# 로깅 설정
logging.basicConfig(
    filename='auto_trading_signal.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AutoTradingDaemon:
    def __init__(self):
        self.stock_code = config.STOCK_CODE
        self.stock_name = config.STOCK_NAME
        self.check_interval = 300  # 5분마다 체크
        self.last_signal = None  # 마지막 신호 (중복 알림 방지)
        
        mode = "모의투자" if config.IS_PAPER_TRADING else "실전투자"
        logging.info(f"AutoTradingDaemon 초기화 [{mode}]")
        print_log(f"✅ AutoTradingDaemon 시작 [{mode}]")
        print_log(f"   종목: {self.stock_name} ({self.stock_code})")
        print_log(f"   체크 간격: {self.check_interval}초")
    
    def get_signal(self):
        """신호 조회"""
        try:
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
                "FID_INPUT_ISCD": self.stock_code,
                "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            }
            
            response = requests.get(url, headers=headers, params=params, verify=False, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            candles = list(reversed(data.get("output2", [])))
            prices = [int(c["stck_clpr"]) for c in candles if c.get("stck_clpr")]
            
            if len(prices) < config.LONG_MA:
                return None, None, None, None
            
            signal = strategy.get_signal(prices)
            ma_short = strategy.calc_ma(prices, config.SHORT_MA)
            ma_long = strategy.calc_ma(prices, config.LONG_MA)
            current_price = prices[-1]
            
            return signal, current_price, ma_short, ma_long
            
        except Exception as e:
            logging.error(f"신호 조회 실패: {e}")
            return None, None, None, None
    
    def send_alert(self, signal, current_price, ma_short, ma_long):
        """텔레그램 알림 전송"""
        try:
            if signal == "BUY":
                msg = f"📈 [{self.stock_name}] BUY 신호 감지!\n" \
                      f"현재가: {current_price:,}원\n" \
                      f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n" \
                      f"MA{config.LONG_MA}: {ma_long:,.0f}원\n" \
                      f"신호: 골든크로스 (단기 > 장기)\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
            elif signal == "SELL":
                msg = f"📉 [{self.stock_name}] SELL 신호 감지!\n" \
                      f"현재가: {current_price:,}원\n" \
                      f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n" \
                      f"MA{config.LONG_MA}: {ma_long:,.0f}원\n" \
                      f"신호: 데드크로스 (단기 < 장기)\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                return False
            
            success = telegram_bot.send_telegram_message(msg)
            if success:
                logging.info(f"{signal} 신호 알림 전송 성공")
                print_log(f"✅ 텔레그램 전송: {signal} 신호")
            else:
                print_log(f"⚠️ 텔레그램 전송 실패")
            
            return success
            
        except Exception as e:
            logging.error(f"알림 전송 중 오류: {e}")
            return False
    
    def run(self):
        """메인 루프"""
        print_log(f"")
        print_log(f"📊 자동매매 신호 모니터링 시작...")
        print_log(f"   조회 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print_log(f"   간격: {self.check_interval}초 ({self.check_interval//60}분)")
        print_log(f"")
        
        try:
            iteration = 0
            while True:
                iteration += 1
                now = datetime.now()
                
                # 신호 조회
                signal, current_price, ma_short, ma_long = self.get_signal()
                
                if signal is None or current_price is None:
                    # 데이터 조회 실패
                    print_log(f"❌ 신호 조회 실패")
                    logging.warning("신호 조회 실패")
                elif signal in ["BUY", "SELL"]:
                    # 신호 감지 - 중복 방지
                    if signal != self.last_signal:
                        print_log(f"🔔 신호 감지: {signal}")
                        print_log(f"   현재가: {current_price:,}원")
                        print_log(f"   MA{config.SHORT_MA}: {ma_short:,.0f}원")
                        print_log(f"   MA{config.LONG_MA}: {ma_long:,.0f}원")
                        
                        # 텔레그램 전송
                        self.send_alert(signal, current_price, ma_short, ma_long)
                        
                        self.last_signal = signal
                    else:
                        print_log(f"⚪ {signal} 신호 유지 (알림 생략)")
                else:
                    # 신호 없음 (관망)
                    print_log(f"⚪ 관망 중 ({current_price:,}원, MA5: {ma_short:,.0f}원, MA20: {ma_long:,.0f}원)")
                    self.last_signal = None
                
                # 대기
                print_log(f"⏳ {self.check_interval}초 대기...")
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            print_log(f"🛑 자동매매 신호 모니터링 종료 (사용자 중단)")
            logging.info("사용자가 중단함")
        except Exception as e:
            logging.error(f"런타임 오류: {e}")
            print_log(f"❌ 런타임 오류: {e}")
            raise

if __name__ == "__main__":
    try:
        daemon = AutoTradingDaemon()
        daemon.run()
    finally:
        log_file.close()

