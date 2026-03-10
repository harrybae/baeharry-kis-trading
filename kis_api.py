import requests
import json
import time
import os
import threading
from datetime import datetime
import config

TOKEN_FILE = os.path.expanduser("~/trading/.token_cache.json")

_access_token = None
_token_expires = 0
_token_refresh_count = 0
_token_last_issued = 0
_token_lock = threading.Lock()

TOKEN_REFRESH_INTERVAL = int(os.environ.get("KIS_TOKEN_REFRESH_INTERVAL", "7200"))
MAX_TOKEN_REFRESH_PER_HOUR = int(os.environ.get("KIS_MAX_TOKEN_REFRESH", "5"))

def _reset_token_cache():
    global _access_token, _token_expires, _token_refresh_count
    with _token_lock:
        _access_token = None
        _token_expires = 0

def force_token_reset():
    _reset_token_cache()
    if os.path.exists(TOKEN_FILE):
        try:
            os.remove(TOKEN_FILE)
            print(f"[{_now()}] 토큰 캐시 파일 삭제 완료")
        except OSError as e:
            print(f"[{_now()}] 토큰 캐시 파일 삭제 실패: {e}")
    print(f"[{_now()}] 토큰 강제 초기화 완료")

def get_token_refresh_count():
    return _token_refresh_count

def _can_refresh_token():
    global _token_last_issued
    now = time.time()
    if now - _token_last_issued < 3600:
        return _token_refresh_count < MAX_TOKEN_REFRESH_PER_HOUR
    return True

def _issue_new_token():
    global _access_token, _token_expires, _token_refresh_count, _token_last_issued
    
    if not _can_refresh_token():
        print(f"[{_now()}] 토큰 발급 횟수 초과 (1시간内有 {MAX_TOKEN_REFRESH_PER_HOUR}회 제한)")
        raise Exception("토큰 발급 횟수 초과")
    
    url = f"{config.BASE_URL}/oauth2/tokenP"
    headers = {"Content-Type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": config.APP_KEY,
        "appsecret": config.APP_SECRET,
    }
    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    data = res.json()

    _access_token = data["access_token"]
    _token_expires = time.time() + int(data.get("expires_in", 86400)) - 300
    _token_refresh_count += 1
    _token_last_issued = time.time()

    try:
        with open(TOKEN_FILE, 'w') as f:
            json.dump({'token': _access_token, 'expires': _token_expires}, f)
    except IOError as e:
        print(f"[{_now()}] 토큰 캐시 저장 실패: {e}")

    print(f"[{_now()}] 토큰 발급 완료 (발급횟수: {_token_refresh_count})")
    return _access_token

def get_access_token():
    global _access_token, _token_expires

    with _token_lock:
        if _access_token and time.time() < _token_expires:
            return _access_token

    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, 'r') as f:
                cache = json.load(f)
            if cache.get('expires') and time.time() < cache['expires']:
                _access_token = cache['token']
                _token_expires = cache['expires']
                print(f"[{_now()}] 토큰 캐시 사용")
                return _access_token
        except (json.JSONDecodeError, IOError) as e:
            print(f"[{_now()}] 토큰 캐시 읽기 실패: {e}")

    return _issue_new_token()


def _headers(tr_id):
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": config.APP_KEY,
        "appsecret": config.APP_SECRET,
        "tr_id": tr_id,
    }


def get_current_price(stock_code):
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = _headers("FHKST01010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    return int(res.json().get("output", {}).get("stck_prpr", 0))


def get_minute_candles(stock_code, count=30):
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice"
    headers = _headers("FHKST03010200")
    now = datetime.now().strftime("%H%M%S")
    params = {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
        "FID_INPUT_HOUR_1": now,
        "FID_PW_DATA_INCU_YN": "Y",
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    candles = res.json().get("output2", [])
    return [int(c["stck_prpr"]) for c in candles if "stck_prpr" in c][:count]


def get_balance():
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "VTTC8434R" if config.IS_PAPER_TRADING else "TTTC8434R"
    headers = _headers(tr_id)
    params = {
        "CANO": config.ACCOUNT_NUMBER,
        "ACNT_PRDT_CD": config.ACCOUNT_SUFFIX,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    data = res.json()
    holdings = {}
    avg_costs = {}
    for item in data.get("output1", []):
        code = item.get("pdno")
        qty = int(item.get("hldg_qty", 0))
        if code and qty > 0:
            holdings[code] = qty
            avg_costs[code] = float(item.get("pchs_avg_pric", 0) or 0)
    deposit = int(data.get("output2", [{}])[0].get("dnca_tot_amt", 0))
    return holdings, deposit, avg_costs


def place_order(stock_code, order_type, quantity, price=0):
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"
    if order_type == "BUY":
        tr_id = "VTTC0802U" if config.IS_PAPER_TRADING else "TTTC0802U"
    else:
        tr_id = "VTTC0801U" if config.IS_PAPER_TRADING else "TTTC0801U"
    headers = _headers(tr_id)
    body = {
        "CANO": config.ACCOUNT_NUMBER,
        "ACNT_PRDT_CD": config.ACCOUNT_SUFFIX,
        "PDNO": stock_code,
        "ORD_DVSN": "01" if price == 0 else "00",
        "ORD_QTY": str(quantity),
        "ORD_UNPR": "0" if price == 0 else str(price),
    }
    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    data = res.json()
    success = data.get("rt_cd") == "0"
    order_no = data.get("output", {}).get("ODNO", "")
    print(f"[{_now()}] {'매수' if order_type=='BUY' else '매도'} {'성공' if success else '실패'}: {data.get('msg1','')} (주문번호: {order_no})")
    return success, order_no


def get_stock_name(stock_code):
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/search-stock-info"
    headers = _headers("CTPF1002R")
    params = {"PRDT_TYPE_CD": "300", "PDNO": stock_code}
    try:
        res = requests.get(url, headers=headers, params=params)
        res.raise_for_status()
        output = res.json().get("output", {})
        return output.get("prdt_abrv_name") or output.get("prdt_name") or stock_code
    except requests.RequestException as e:
        print(f"[{_now()}] 종목명 조회 실패: {e}")
        return stock_code


def _now():
    return datetime.now().strftime("%H:%M:%S")

_refresh_timer = None

def start_token_refresh_scheduler(interval_seconds=None):
    global _refresh_timer
    if interval_seconds is None:
        interval_seconds = TOKEN_REFRESH_INTERVAL
    
    def _refresh_loop():
        global _refresh_timer
        try:
            print(f"[{_now()}] 토큰 정기 갱신 시작 (주기: {interval_seconds}초)")
            force_token_reset()
            get_access_token()
        except Exception as e:
            print(f"[{_now()}] 토큰 정기 갱신 실패: {e}")
        
        _refresh_timer = threading.Timer(interval_seconds, _refresh_loop)
        _refresh_timer.daemon = True
        _refresh_timer.start()
    
    if _refresh_timer is not None:
        _refresh_timer.cancel()
    
    _refresh_timer = threading.Timer(10, _refresh_loop)
    _refresh_timer.daemon = True
    _refresh_timer.start()
    print(f"[{_now()}] 토큰 갱신 스케줄러 시작 (주기: {interval_seconds}초)")

def stop_token_refresh_scheduler():
    global _refresh_timer
    if _refresh_timer is not None:
        _refresh_timer.cancel()
        _refresh_timer = None
        print(f"[{_now()}] 토큰 갱정 스케줄러 중지")


def get_account_balance():
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-balance"
    tr_id = "VTTC8434R" if config.IS_PAPER_TRADING else "TTTC8434R"
    headers = _headers(tr_id)
    params = {
        "CANO": config.ACCOUNT_NUMBER,
        "ACNT_PRDT_CD": config.ACCOUNT_SUFFIX,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "01",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": "",
    }
    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    return res.json()

def validate_stock_code(code):
    import re
    return bool(re.match(r'^\d{6}$', str(code).strip()))
