#!/usr/bin/env python3
# auto_trading_with_risk_daemon.py - 손절/익절 포함 다중 종목 자동매매 데몬

import logging
import time
import os
import sys
import json
from datetime import datetime, timedelta

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config
import kis_api
import strategy
import requests
import telegram_bot
from position_manager import PositionManager

# 로그 파일에 동시에 출력
log_file = open(os.path.join(ROOT_DIR, 'logs', 'auto_trading_risk_daemon.log'), 'a', encoding='utf-8')

def print_log(msg):
    """stdout + 파일에 동시 출력"""
    timestamp = datetime.now().strftime('%H:%M:%S')
    formatted_msg = f"[{timestamp}] {msg}"
    print(formatted_msg)
    log_file.write(formatted_msg + '\n')
    log_file.flush()

# 로깅 설정
logging.basicConfig(
    filename=os.path.join(ROOT_DIR, 'logs', 'auto_trading_risk_signal.log'),
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class TradingDaemonWithRiskManagement:
    def __init__(self, watchlist_file=None):
        if watchlist_file is None:
            watchlist_file = os.path.join(ROOT_DIR, 'data', 'watchlist.json')
        self.check_interval = 300  # 5분마다 체크
        self.last_signals = {}  # 종목별 마지막 신호
        self.position_manager = PositionManager()
        self.last_report_time = datetime.now() - timedelta(hours=1)  # 시작 시 보고서 생성 허용
        self.report_interval = timedelta(minutes=30)  # 30분마다 정기 보고서
        
        # watchlist 로드
        self.stocks = self.load_watchlist(watchlist_file)
        
        mode = "모의투자" if config.IS_PAPER_TRADING else "실전투자"
        logging.info(f"TradingDaemonWithRiskManagement 초기화 [{mode}]")
        print_log(f"✅ 손절/익절 기능 포함 자동매매 데몬 시작 [{mode}]")
        print_log(f"   모니터링 종목: {len([s for s in self.stocks if s.get('enabled', True)])}개")
        print_log(f"   손절 기준: {config.RISK_TOLERANCE * 100}% 손실")
        print_log(f"   익절 기준: {config.TAKE_PROFIT * 100}% 수익")
        print_log(f"   체크 간격: {self.check_interval}초")
        print_log(f"   정기 보고서: {self.report_interval.total_seconds() // 60}분 간격")
    
    def load_watchlist(self, watchlist_file):
        """watchlist.json에서 종목 로드"""
        try:
            with open(watchlist_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stocks = data.get('stocks', [])
            active = [s for s in stocks if s.get('enabled', True)]
            inactive = [s for s in stocks if not s.get('enabled', True)]
            print_log(f"📋 Watchlist 로드: {len(stocks)}개 종목")
            print_log(f"   활성: {len(active)}개, 비활성: {len(inactive)}개")
            for s in stocks:
                status = "활성" if s.get('enabled', True) else "비활성"
                print_log(f"   - {s.get('name', 'Unknown')} ({s.get('code', 'N/A')}): {status}")
            return stocks
        except FileNotFoundError:
            print_log(f"❌ watchlist.json 파일 없음")
            logging.error("watchlist.json 파일 없음")
            return []
        except Exception as e:
            print_log(f"❌ watchlist 로드 실패: {e}")
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
            
            response = requests.get(url, headers=headers, params=params, timeout=10)
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
    
    def send_alert(self, stock_name, stock_code, alert_type, current_price, details=""):
        """텔레그램 알림 전송"""
        try:
            if alert_type == "BUY":
                msg = f"📈 [{stock_name}] BUY 신호 감지!\n" \
                      f"코드: {stock_code}\n" \
                      f"진입가: {current_price:,}원\n" \
                      f"손절: -{config.RISK_TOLERANCE * 100}%\n" \
                      f"익절: +{config.TAKE_PROFIT * 100}%\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                
            elif alert_type == "SELL":
                msg = f"📉 [{stock_name}] SELL 신호 감지!\n" \
                      f"코드: {stock_code}\n" \
                      f"현재가: {current_price:,}원\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                      
            elif alert_type == "STOP_LOSS":
                msg = f"🛑 [{stock_name}] 손절 실행!\n" \
                      f"코드: {stock_code}\n" \
                      f"손절가: {current_price:,}원\n" \
                      f"{details}\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                      
            elif alert_type == "TAKE_PROFIT":
                msg = f"🎯 [{stock_name}] 익절 실행!\n" \
                      f"코드: {stock_code}\n" \
                      f"익절가: {current_price:,}원\n" \
                      f"{details}\n" \
                      f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            else:
                return False
            
            success = telegram_bot.send_telegram_message(msg)
            if success:
                logging.info(f"{stock_name} {alert_type} 알림 전송 성공")
                print_log(f"   ✅ 텔레그램 전송: {alert_type}")
            
            return success
            
        except Exception as e:
            logging.error(f"알림 전송 중 오류 ({stock_name}): {e}")
            return False
    
    def is_market_open(self, now=None):
        """장 운영 시간 체크 (평일 09:00~15:30)"""
        if now is None:
            now = datetime.now()
        weekday = now.weekday()
        if weekday >= 5:  # 토요일(5), 일요일(6)
            return False
        market_start = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
        return market_start <= now <= market_end

    def generate_report(self):
        """포지션 및 모니터링 상태 정기 보고서"""
        try:
            summary = self.position_manager.get_summary()
            report_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            msg = f"📊 정기 보고서 ({report_time})\n" \
                  f"모니터링 종목: {len(self.stocks)}개\n" \
                  f"오픈 포지션: {summary['open_positions']}개\n" \
                  f"총 수익률: {summary['avg_pnl_pct']:.2f}%\n" \
                  f"체크 간격: {self.check_interval}초"
            print_log(msg)
            telegram_bot.send_telegram_message(msg)
            logging.info("정기 보고서 생성 및 전송 완료")
            return True
        except Exception as e:
            logging.error(f"보고서 생성 실패: {e}")
            print_log(f"❌ 보고서 생성 실패: {e}")
            return False

    def check_report_schedule(self):
        """30분 간격 보고서 스케줄 체크"""
        now = datetime.now()
        if now - self.last_report_time >= self.report_interval:
            self.generate_report()
            self.last_report_time = now

    def run(self):
        """메인 루프"""
        print_log(f"")
        print_log(f"📊 손절/익절 포함 자동매매 신호 모니터링 시작...")
        print_log(f"   조회 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print_log(f"   간격: {self.check_interval}초 ({self.check_interval//60}분)")
        print_log(f"   장 운영 시간: 평일 09:00~15:30")
        print_log(f"")
        
        try:
            while True:
                now = datetime.now()
                print_log(f"")
                print_log(f"📍 조회 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}")

                # 장 운영 시간 체크
                if not self.is_market_open(now):
                    print_log(f"💤 장 마감 시간 ({now.strftime('%H:%M')}) - 다음 장까지 대기")
                    self.check_report_schedule()
                    time.sleep(self.check_interval)
                    continue
                
                # 포지션 요약
                summary = self.position_manager.get_summary()
                if summary["open_positions"] > 0:
                    print_log(f"📊 포지션: {summary['open_positions']}개 오픈, 총 수익률: {summary['avg_pnl_pct']:.2f}%")
                
                for stock in self.stocks:
                    if not stock.get('enabled', True):
                        continue
                    
                    code = stock['code']
                    name = stock['name']
                    
                    # 신호 조회
                    signal, current_price, ma_short, ma_long = self.get_signal(code)
                    
                    if signal is None or current_price is None:
                        print_log(f"❌ {name}: 신호 조회 실패")
                        continue
                    
                    # 포지션 확인
                    position = self.position_manager.get_position(code)
                    
                    if position:
                        # 포지션 있음 - 손절/익절 체크
                        is_stop_loss, loss_pct = self.position_manager.check_stop_loss(code, current_price)
                        is_take_profit, profit_pct = self.position_manager.check_take_profit(code, current_price)
                        
                        if is_stop_loss:
                            print_log(f"🛑 {name}: 손절 조건 충족 ({loss_pct:.2f}%)")
                            qty = position.get("quantity", config.ORDER_QUANTITY)
                            self.position_manager.close_position(code, current_price)
                            ok, order_no = kis_api.place_order(code, "SELL", qty)
                            if ok:
                                print_log(f"   ✅ 손절 매도 주문 실행: {name} {qty}주 (주문번호: {order_no})")
                            else:
                                print_log(f"   ⚠️ 손절 매도 주문 실패: {name} {qty}주")
                            self.send_alert(name, code, "STOP_LOSS", current_price,
                                          f"손실률: {loss_pct:.2f}%, 수량: {qty}주")
                        elif is_take_profit:
                            print_log(f"🎯 {name}: 익절 조건 충족 ({profit_pct:.2f}%)")
                            qty = position.get("quantity", config.ORDER_QUANTITY)
                            self.position_manager.close_position(code, current_price)
                            ok, order_no = kis_api.place_order(code, "SELL", qty)
                            if ok:
                                print_log(f"   ✅ 익절 매도 주문 실행: {name} {qty}주 (주문번호: {order_no})")
                            else:
                                print_log(f"   ⚠️ 익절 매도 주문 실패: {name} {qty}주")
                            self.send_alert(name, code, "TAKE_PROFIT", current_price,
                                          f"수익률: {profit_pct:.2f}%, 수량: {qty}주")
                        else:
                            current_pnl = (current_price - position["entry_price"]) * position["quantity"]
                            current_pnl_pct = ((current_price - position["entry_price"]) / position["entry_price"]) * 100
                            print_log(f"📊 {name}: 포지션 유지 (손익: {current_pnl:+,.0f}원, {current_pnl_pct:+.2f}%)")
                    else:
                        # 포지션 없음 - 진입 신호 체크
                        if signal in ["BUY", "SELL"]:
                            last_signal = self.last_signals.get(code)
                            if signal != last_signal:
                                print_log(f"🔔 {name}: {signal} 신호 감지")
                                print_log(f"   현재가: {current_price:,}원")
                                print_log(f"   MA{config.SHORT_MA}: {ma_short:,.0f}원")
                                print_log(f"   MA{config.LONG_MA}: {ma_long:,.0f}원")
                                
                                if signal == "BUY":
                                    # 포지션 열기 + 실제 매수 주문
                                    qty = config.ORDER_QUANTITY
                                    self.position_manager.open_position(code, name, current_price, qty)
                                    ok, order_no = kis_api.place_order(code, "BUY", qty)
                                    if ok:
                                        print_log(f"   ✅ 매수 주문 실행: {name} {qty}주 (주문번호: {order_no})")
                                    else:
                                        print_log(f"   ⚠️ 매수 주문 실패: {name} {qty}주")
                                    self.send_alert(name, code, "BUY", current_price, f"수량: {qty}주")
                                elif signal == "SELL":
                                    # 공매도/숏 포지션은 지원하지 않으므로 알림만 전송
                                    print_log(f"   ⚠️ SELL 신호 발생 but 공매도 미지원: {name}")
                                    self.send_alert(name, code, "SELL", current_price, "공매도 미지원 - 알림만")

                                self.last_signals[code] = signal
                        else:
                            print_log(f"⚪ {name}: 관망 ({current_price:,}원)")
                            self.last_signals[code] = None
                
                # 정기 보고서 체크
                self.check_report_schedule()

                # 대기
                print_log(f"⏳ {self.check_interval}초 대기...")
                time.sleep(self.check_interval)
                
        except KeyboardInterrupt:
            print_log(f"🛑 자동매매 모니터링 종료 (사용자 중단)")
            logging.info("사용자가 중단함")
        except Exception as e:
            logging.error(f"런타임 오류: {e}")
            print_log(f"❌ 런타임 오류: {e}")
            raise

if __name__ == "__main__":
    try:
        daemon = TradingDaemonWithRiskManagement()
        daemon.run()
    finally:
        log_file.close()

