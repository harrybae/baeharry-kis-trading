import requests
import json
import time
from datetime import datetime
import config

# 토큰 캐시
_access_token = None
_token_expires = 0


def get_access_token():
    """액세스 토큰 발급 (캐시 포함)"""
    global _access_token, _token_expires

    if _access_token and time.time() < _token_expires:
        return _access_token

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
    _token_expires = time.time() + int(data.get("expires_in", 86400)) - 60
    print(f"[{_now()}] 토큰 발급 완료")
    return _access_token


def _headers(tr_id):
    """공통 헤더 생성"""
    return {
        "Content-Type": "application/json",
        "authorization": f"Bearer {get_access_token()}",
        "appkey": config.APP_KEY,
        "appsecret": config.APP_SECRET,
        "tr_id": tr_id,
    }


def get_current_price(stock_code):
    """현재가 조회"""
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = _headers("FHKST01010100")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": stock_code,
    }

    res = requests.get(url, headers=headers, params=params)
    res.raise_for_status()
    data = res.json()

    output = data.get("output", {})
    price = int(output.get("stck_prpr", 0))
    return price


def get_minute_candles(stock_code, count=30):
    """분봉 데이터 조회 (최근 count개)"""
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
    data = res.json()

    candles = data.get("output2", [])
    prices = [int(c["stck_prpr"]) for c in candles if "stck_prpr" in c]
    return prices[:count]


def get_balance():
    """보유 주식 및 예수금 조회"""
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
    print(f"[DEBUG] balance: {data}")

    holdings = {}
    for item in data.get("output1", []):
        code = item.get("pdno")
        qty = int(item.get("hldg_qty", 0))
        if code and qty > 0:
            holdings[code] = qty

    deposit = int(data.get("output2", [{}])[0].get("dnca_tot_amt", 0))
    return holdings, deposit


def place_order(stock_code, order_type, quantity, price=0):
    """
    주문 실행
    order_type: "BUY" or "SELL"
    price=0 이면 시장가
    """
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/trading/order-cash"

    if order_type == "BUY":
        tr_id = "VTTC0802U" if config.IS_PAPER_TRADING else "TTTC0802U"
        side = "02"  # 매수
    else:
        tr_id = "VTTC0801U" if config.IS_PAPER_TRADING else "TTTC0801U"
        side = "01"  # 매도

    headers = _headers(tr_id)
    body = {
        "CANO": config.ACCOUNT_NUMBER,
        "ACNT_PRDT_CD": config.ACCOUNT_SUFFIX,
        "PDNO": stock_code,
        "ORD_DVSN": "01" if price == 0 else "00",  # 01: 시장가, 00: 지정가
        "ORD_QTY": str(quantity),
        "ORD_UNPR": "0" if price == 0 else str(price),
    }

    res = requests.post(url, headers=headers, json=body)
    res.raise_for_status()
    data = res.json()

    rt_cd = data.get("rt_cd", "")
    msg = data.get("msg1", "")
    order_no = data.get("output", {}).get("ODNO", "")

    success = rt_cd == "0"
    print(f"[{_now()}] {'매수' if order_type == 'BUY' else '매도'} 주문 {'성공' if success else '실패'}: {msg} (주문번호: {order_no})")
    return success, order_no


def _now():
    return datetime.now().strftime("%H:%M:%S")


def get_stock_name(stock_code):
    """종목명 조회"""
    url = f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/search-stock-info"
    headers = _headers("CTPF1002R")
    params = {
        "PRDT_TYPE_CD": "300",
        "PDNO": stock_code,
    }
    try:
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        print(f"[DEBUG] search-stock-info: {data}")
        output = data.get("output", {})
        name = output.get("prdt_abrv_name") or output.get("prdt_name") or stock_code
        return name
    except Exception as e:
        print(f"[DEBUG] get_stock_name 오류: {e}")
        return stock_code
