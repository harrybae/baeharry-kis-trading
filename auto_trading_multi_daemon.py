#!/usr/bin/env python3
# auto_trading_multi_daemon.py - 다중 종목 자동매매 신호 모니터링 데몬

import logging
import time
import os
import sys
import json
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
import kis_api
import strategy
import requests
import telegram_bot

# 로그 파일에 동시에 출력
log_file = open('auto_trading_multi_daemon.log', 'a', encoding='utf-8')

def print_log(msg):
    """stdout + 파일에 동시 출력"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    log_file.write(formatted_msg + '\n')
    log_file.flush()

# 로깅 설정
logging.basicConfig(
    filename='auto_trading_multi_signal.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class MultiStockTradingDaemon:
    def __init__(self, watchlist_file='watchlist.json'):
        self.check_interval = 300  # 5분마다 체크
        self.last_signals = {}  # 종목별 마지막 신호 (중복 알림 방지)
        
        # watchlist 로드
        self.stocks = self.load_watchlist(watchlist_file)
        
        mode = "모의투자" if config.IS_PAPER_TRADING else "실전투자"
        logging.info(f"MultiStockTradingDaemon 초기화 [{mode}]")
        print_log(f"✅ MultiStockTradingDaemon 시작 [{mode}]")
        print_log(f"   모니터링 종목: {len([s for s in self.stocks if s['enabled']])}개")
        print_log(f"   체크 간격: {self.check_interval}초")
    
    def load_watchlist(self, watchlist_file):
        """watchlist.json에서 종목 로드"""
        try:
            with open(watchlist_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stocks = data.get('stocks', [])
            print_log(f"📋 Watchlist 로드: {len(stocks)}개 종목")
            for s in stocks:
                print_log(f"   - {s['name']} ({s['code']}): {'활성' if s.get('enabled', True) else '비활성'}")
            return stocks
        except FileNotFoundError:
            print_log(f"❌ watchlist.json 파일 없음")
            logging.error("watchlist.json 파일 없음")
            return []
        except Exception as e:
            print_log(f"❌ watchlist 로드 실패: {e}")
            logging.error(f"watchlist 로드 실패: {e}")
            return []
    
    def get_signal(self, stock_code):
        """종목별 신호 조회"""
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
                "FID_INPUT_ISCD": stock_code,
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
            logging.error(f"신호 조회 실패 ({stock_code}): {e}")
            return None, None, None, None
    
    def send_alert(self, stock_name, stock_code, signal, current_price, ma_short, ma_long):
        """텔레그램 알림 전송"""
        try:
            if signal == "BUY":
                msg = f"📈 [{stock_name}] BUY 신호 감지!\n" \
                      f"코드: {stock_code}\n" \
                      f"현재가: {current_price:,}원\n" \
                      f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n" \
                      f"MA{config.LONG_MA}: {ma_long:,.0f}원\n" \
                      f"신호: 골든크로스 (단기 > 장기)\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
            elif signal == "SELL":
                msg = f"📉 [{stock_name}] SELL 신호 감지!\n" \
                      f"코드: {stock_code}\n" \
                      f"현재가: {current_price:,}원\n" \
                      f"MA{config.SHORT_MA}: {ma_short:,.0f}원\n" \
                      f"MA{config.LONG_MA}: {ma_long:,.0f}원\n" \
                      f"신호: 데드크로스 (단기 < 장기)\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                return False
            
            success = telegram_bot.send_telegram_message(msg)
            if success:
                logging.info(f"{stock_name} {signal} 신호 알림 전송 성공")
                print_log(f"   ✅ 텔레그램 전송: {signal}")
            else:
                print_log(f"   ⚠️ 텔레그램 전송 실패")
            
            return success
            
        except Exception as e:
            logging.error(f"알림 전송 중 오류 ({stock_name}): {e}")
            return False
    
    def run(self):
        """메인 루프"""
        print_log(f"")
        print_log(f"📊 다중 종목 자동매매 신호 모니터링 시작...")
        print_log(f"   조회 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print_log(f"   간격: {self.check_interval}초 ({self.check_interval//60}분)")
        print_log(f"")
        
        try:
            while True:
                now = datetime.now()
                print_log(f"")
                print_log(f"📍 조회 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                
                for stock in self.stocks:
                    if not stock.get('enabled', True):
                        continue
                    
                    code = stock['code']
                    name = stock['name']
                    
                    # 신호 조회
                    signal, current_price, ma_short, ma_long = self.get_signal(code)
                    
                    if signal is None or current_price is None:
                        # 데이터 조회 실패
                        print_log(f"❌ {name}: 신호 조회 실패")
                        logging.warning(f"{name} 신호 조회 실패")
                    elif signal in ["BUY", "SELL"]:
                        # 신호 감지 - 중복 방지
                        last_signal = self.last_signals.get(code)
                        if signal != last_signal:
                            print_log(f"🔔 {name}: {signal} 신호 감지")
                            print_log(f"   현재가: {current_price:,}원")
                            print_log(f"   MA{config.SHORT_MA}: {ma_short:,.0f}원")
                            print_log(f"   MA{config.LONG_MA}: {ma_long:,.0f}원")
                            
                            # 텔레그램 전송
                            self.send_alert(name, code, signal, current_price, ma_short, ma_long)
                            
                            self.last_signals[code] = signal
                        else:
                            print_log(f"⚪ {name}: {signal} 신호 유지 (알림 생략)")
                    else:
                        # 신호 없음 (관망)
                        print_log(f"⚪ {name}: 관망 ({current_price:,}원)")
                        self.last_signals[code] = None
                
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
        daemon = MultiStockTradingDaemon()
        daemon.run()
    finally:
        log_file.close()

