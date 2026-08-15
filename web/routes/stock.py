import os
import sys
import math
import re
import json as js
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
import requests as req

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config
import kis_api
import stock_master

stock_bp = Blueprint("stock", __name__)


def validate_stock_code(code):
    if not code or not isinstance(code, str):
        return False
    return bool(re.match(r'^\d{6}$', code.strip()))


@stock_bp.route("/api/search/<keyword>")
def search_stock(keyword):
    try:
        results = stock_master.search_stocks(keyword, limit=8)
        return jsonify(results)
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@stock_bp.route("/api/stock/<stock_code>")
def get_stock(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        token = kis_api.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "FHKST01010100",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                      headers=headers, params=params, timeout=10)
        res.raise_for_status()
        out = res.json().get("output", {})
        price = int(out.get("stck_prpr", 0))
        prev = int(out.get("stck_sdpr", 0))
        change = price - prev

        # 잔고 조회 (캐싱)
        try:
            holdings, deposit, avg_costs = kis_api.get_balance()
            _balance_cache = {"holdings": holdings, "deposit": deposit, "avg_costs": avg_costs}
        except Exception as e:
            print(f"잔고 조회 실패: {e}")
            _balance_cache = {"holdings": {}, "deposit": 0, "avg_costs": {}}
            deposit = 0
            avg_costs = {}

        holdings = _balance_cache.get("holdings", {})
        deposit = _balance_cache.get("deposit", 0)
        avg_costs = _balance_cache.get("avg_costs", {})
        qty = holdings.get(stock_code, 0)

        results = stock_master.search_stocks(stock_code, limit=1)
        stock_name = results[0]["name"] if results else ""
        market = results[0]["market"] if results else ""

        return jsonify({
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": market,
            "price": price,
            "prev_price": prev,
            "change": change,
            "change_pct": out.get("prdy_ctrt", ""),
            "open_price": int(out.get("stck_oprc", 0)) or None,
            "high_price": int(out.get("stck_hgpr", 0)) or None,
            "low_price": int(out.get("stck_lwpr", 0)) or None,
            "upper_limit": int(out.get("stck_mxpr", 0)) or None,
            "lower_limit": int(out.get("stck_llam", 0)) or None,
            "volume": out.get("acml_vol", ""),
            "trade_amount": out.get("acml_tr_pbmn", ""),
            "per": out.get("per", ""),
            "pbr": out.get("pbr", ""),
            "eps": out.get("eps", ""),
            "bps": out.get("bps", ""),
            "frgn_rate": out.get("hts_frgn_ehrt", ""),
            "frgn_net_buy": out.get("frgn_ntby_qty", ""),
            "w52_high": int(out.get("w52_hgpr", 0)) or None,
            "w52_low": int(out.get("w52_lwpr", 0)) or None,
            "vol_turnover": out.get("vol_tnrt", ""),
            "quantity": qty,
            "deposit": deposit,
            "is_paper": config.IS_PAPER_TRADING,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@stock_bp.route("/api/chart/<stock_code>")
def get_chart(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        period = request.args.get("period", "D")
        days = int(request.args.get("days", 90))
        end = datetime.now()
        if period == "D":
            start = end - timedelta(days=days)
        elif period == "W":
            start = end - timedelta(weeks=days)
        else:
            start = end - timedelta(days=days * 30)

        token = kis_api.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
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
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        res = req.get(
            f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        output = res.json().get("output2", [])
        chart = []
        for item in reversed(output):
            chart.append({
                "date": item["stck_bsop_date"],
                "open": int(item["stck_oprc"]),
                "high": int(item["stck_hgpr"]),
                "low": int(item["stck_lwpr"]),
                "close": int(item["stck_clpr"]),
                "volume": int(item["acml_vol"]),
            })

        closes = [c["close"] for c in chart]

        # 볼린저밴드 (20일)
        for i, c in enumerate(chart):
            if i >= 19:
                window = closes[i - 19:i + 1]
                ma = sum(window) / 20
                std = math.sqrt(sum((x - ma) ** 2 for x in window) / 20)
                c["bb_upper"] = round(ma + 2 * std)
                c["bb_mid"] = round(ma)
                c["bb_lower"] = round(ma - 2 * std)
            else:
                c["bb_upper"] = c["bb_mid"] = c["bb_lower"] = None

        # RSI (14일)
        for i, c in enumerate(chart):
            if i >= 14:
                gains = [max(closes[j] - closes[j - 1], 0) for j in range(i - 13, i + 1)]
                losses = [max(closes[j - 1] - closes[j], 0) for j in range(i - 13, i + 1)]
                ag = sum(gains) / 14
                al = sum(losses) / 14
                c["rsi"] = round(100 - 100 / (1 + ag / al), 2) if al > 0 else 100.0
            else:
                c["rsi"] = None

        # MACD (12, 26, 9)
        def ema_calc(data, n):
            if len(data) < n:
                return [None] * len(data)
            k = 2.0 / (n + 1)
            result = [None] * (n - 1)
            val = sum(data[:n]) / n
            result.append(val)
            for x in data[n:]:
                val = x * k + val * (1 - k)
                result.append(val)
            return result

        ema12 = ema_calc(closes, 12)
        ema26 = ema_calc(closes, 26)
        macd_line = [round(ema12[i] - ema26[i], 2) if ema12[i] is not None and ema26[i] is not None else None
                     for i in range(len(closes))]
        macd_vals = [v for v in macd_line if v is not None]
        signal_raw = ema_calc(macd_vals, 9)
        j = 0
        signal_full = []
        for i in range(len(closes)):
            if macd_line[i] is not None:
                signal_full.append(round(signal_raw[j], 2) if signal_raw[j] is not None else None)
                j += 1
            else:
                signal_full.append(None)
        for i, c in enumerate(chart):
            c["macd"] = macd_line[i]
            c["macd_signal"] = signal_full[i]
            c["macd_hist"] = round(macd_line[i] - signal_full[i], 2) if macd_line[i] is not None and signal_full[i] is not None else None

        return jsonify(chart)
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@stock_bp.route("/api/quant/<stock_code>")
def get_quant(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        token = kis_api.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "FHKST01010100",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                      headers=headers, params=params, timeout=10)
        res.raise_for_status()
        out = res.json().get("output", {})

        headers2 = dict(headers)
        headers2["tr_id"] = "FHKST03010100"
        end = datetime.now()
        start = end - timedelta(days=365)
        chart_params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": stock_code,
            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "0",
        }
        res2 = req.get(
            f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers2, params=chart_params, timeout=10
        )
        res2.raise_for_status()
        candles = list(reversed(res2.json().get("output2", [])))
        closes = [int(c["stck_clpr"]) for c in candles if c.get("stck_clpr")]

        price = int(out.get("stck_prpr", 0))
        per = float(out.get("per", 0) or 0)
        pbr = float(out.get("pbr", 0) or 0)
        eps = float(out.get("eps", 0) or 0)
        bps = float(out.get("bps", 0) or 0)
        w52_high = int(out.get("w52_hgpr", 0) or 0)
        w52_low = int(out.get("w52_lwpr", 0) or 0)
        frgn_rate = float(out.get("hts_frgn_ehrt", 0) or 0)

        ret_1m = round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None
        ret_3m = round((closes[-1] / closes[-63] - 1) * 100, 2) if len(closes) >= 63 else None
        ret_6m = round((closes[-1] / closes[-126] - 1) * 100, 2) if len(closes) >= 126 else None
        ret_1y = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else None
        w52_pos = round((price - w52_low) / (w52_high - w52_low) * 100, 1) if w52_high > w52_low else None

        def ma(n):
            if len(closes) >= n:
                return round(sum(closes[-n:]) / n)
            return None

        ma5 = ma(5)
        ma20 = ma(20)
        ma60 = ma(60)
        ma120 = ma(120)
        ma5_sig = "골든크로스" if ma5 and ma20 and ma5 > ma20 else "데드크로스"
        ma_trend = "상승" if ma20 and ma60 and ma20 > ma60 else "하락"

        if len(closes) >= 20:
            returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
            avg_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns))
            annual_vol = round(std_r * math.sqrt(252) * 100, 2)
            peak = closes[0]
            mdd = 0
            for c in closes:
                if c > peak:
                    peak = c
                dd = (c - peak) / peak
                if dd < mdd:
                    mdd = dd
            mdd = round(mdd * 100, 2)
            rf = 0.035 / 252
            excess = [r - rf for r in returns]
            sharpe = round((sum(excess) / len(excess)) / std_r * math.sqrt(252), 2) if std_r > 0 else None
        else:
            annual_vol = mdd = sharpe = None

        per_grade = "저평가" if 0 < per < 10 else ("적정" if per < 20 else ("고평가" if per < 30 else "매우고평가")) if per > 0 else "-"
        pbr_grade = "저평가" if 0 < pbr < 1 else ("적정" if pbr < 2 else "고평가") if pbr > 0 else "-"

        results = stock_master.search_stocks(stock_code, limit=1)
        stock_name = results[0]["name"] if results else stock_code
        market = results[0]["market"] if results else ""

        return jsonify({
            "stock_code": stock_code,
            "stock_name": stock_name,
            "market": market,
            "price": price,
            "per": per,
            "pbr": pbr,
            "eps": eps,
            "bps": bps,
            "per_grade": per_grade,
            "pbr_grade": pbr_grade,
            "frgn_rate": frgn_rate,
            "ret_1m": ret_1m,
            "ret_3m": ret_3m,
            "ret_6m": ret_6m,
            "ret_1y": ret_1y,
            "w52_high": w52_high,
            "w52_low": w52_low,
            "w52_pos": w52_pos,
            "ma5": ma5,
            "ma20": ma20,
            "ma60": ma60,
            "ma120": ma120,
            "ma5_sig": ma5_sig,
            "ma_trend": ma_trend,
            "price_vs_ma20": round((price / ma20 - 1) * 100, 2) if ma20 else None,
            "price_vs_ma60": round((price / ma60 - 1) * 100, 2) if ma60 else None,
            "annual_vol": annual_vol,
            "mdd": mdd,
            "sharpe": sharpe,
        })
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
