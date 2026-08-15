import os
import sys
import re
import math
import sqlite3
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config
import kis_api
import requests as req

market_bp = Blueprint("market", __name__)


@market_bp.route("/api/news/<stock_name>")
def get_stock_news(stock_name):
    try:
        import urllib.parse
        query = urllib.parse.quote(f"{stock_name} 주식")
        headers = {
            "X-Naver-Client-Id": config.NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": config.NAVER_CLIENT_SECRET,
        }
        res = req.get(
            f"https://openapi.naver.com/v1/search/news.json?query={query}&display=5&sort=date",
            headers=headers, timeout=10
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        news = []
        for item in items:
            title = re.sub(r"<[^>]+>", "", item.get("title", ""))
            desc = re.sub(r"<[^>]+>", "", item.get("description", ""))
            news.append({
                "title": title,
                "desc": desc,
                "link": item.get("originallink") or item.get("link"),
                "pubDate": item.get("pubDate", ""),
            })
        return jsonify({"news": news})
    except Exception as e:
        return jsonify({"error": str(e), "news": []}), 200


@market_bp.route("/api/market")
def get_market():
    try:
        token = kis_api.get_access_token()
        result = {}

        for code, name, key in [("0001", "KOSPI", "kospi"), ("1001", "KOSDAQ", "kosdaq")]:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "appkey": config.APP_KEY,
                    "appsecret": config.APP_SECRET,
                    "tr_id": "FHPUP02100000",
                    "Content-Type": "application/json; charset=utf-8",
                }
                params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}
                res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price",
                              headers=headers, params=params, timeout=10)
                out = res.json().get("output", {})
                result[key] = {
                    "name": name,
                    "value": float(out.get("bstp_nmix_prpr", 0) or 0),
                    "change": float(out.get("bstp_nmix_prdy_vrss", 0) or 0),
                    "change_pct": float(out.get("prdy_ctrt", 0) or 0),
                }
            except Exception:
                result[key] = {"name": name, "value": 0, "change": 0, "change_pct": 0}

        try:
            res = req.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            krw = res.json().get("rates", {}).get("KRW", 0)
            result["usdkrw"] = {"name": "USD/KRW", "value": round(krw, 1), "change": 0, "change_pct": 0}
        except Exception:
            result["usdkrw"] = {"name": "USD/KRW", "value": 0, "change": 0, "change_pct": 0}

        for sym, key, name in [("NQ=F", "nasdaq", "나스닥"), ("ES=F", "sp500", "S&P500")]:
            try:
                res = req.get(
                    f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=5
                )
                data = res.json()
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = float(meta.get("regularMarketPrice", 0) or 0)
                prev = float(meta.get("chartPreviousClose", 0) or 0)
                chg = round(price - prev, 2)
                chg_pct = round((price / prev - 1) * 100, 2) if prev > 0 else 0
                result[key] = {"name": name, "value": round(price, 1), "change": chg, "change_pct": chg_pct}
            except Exception:
                result[key] = {"name": name, "value": 0, "change": 0, "change_pct": 0}

        result["updated"] = datetime.now().strftime("%H:%M")
        return jsonify(result)
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@market_bp.route("/api/screener")
def run_screener():
    try:
        per_min = float(request.args.get("per_min", 0) or 0)
        per_max = float(request.args.get("per_max", 9999) or 9999)
        pbr_min = float(request.args.get("pbr_min", 0) or 0)
        pbr_max = float(request.args.get("pbr_max", 9999) or 9999)
        pct_min = float(request.args.get("pct_min", -99) or -99)
        pct_max = float(request.args.get("pct_max", 99) or 99)
        rsi_min = float(request.args.get("rsi_min", 0) or 0)
        rsi_max = float(request.args.get("rsi_max", 100) or 100)
        limit = int(request.args.get("limit", 20) or 20)

        db_path = os.path.join(ROOT_DIR, 'db', 'stock_master.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT code, name, market FROM stocks ORDER BY RANDOM() LIMIT 200")
        stocks = cursor.fetchall()
        conn.close()

        token = kis_api.get_access_token()
        results = []
        checked = 0

        for code, name, market in stocks:
            if len(results) >= limit:
                break
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "appkey": config.APP_KEY,
                    "appsecret": config.APP_SECRET,
                    "tr_id": "FHKST01010100",
                    "Content-Type": "application/json; charset=utf-8",
                }
                params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
                res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                              headers=headers, params=params, timeout=3)
                out = res.json().get("output", {})
                price = int(out.get("stck_prpr", 0) or 0)
                per = float(out.get("per", 0) or 0)
                pbr = float(out.get("pbr", 0) or 0)
                pct = float(out.get("prdy_ctrt", 0) or 0)
                if price == 0:
                    continue

                if per_max < 9999 or per_min > 0:
                    if per <= 0 or per < per_min or per > per_max:
                        continue
                if pbr_max < 9999 or pbr_min > 0:
                    if pbr <= 0 or pbr < pbr_min or pbr > pbr_max:
                        continue
                if pct < pct_min or pct > pct_max:
                    continue

                rsi = None
                if rsi_min > 0 or rsi_max < 100:
                    try:
                        h2 = dict(headers)
                        h2["tr_id"] = "FHKST03010100"
                        end = datetime.now()
                        start = end - timedelta(days=30)
                        cp = {
                            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
                        }
                        r2 = req.get(
                            f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                            headers=h2, params=cp, timeout=3
                        )
                        candles = list(reversed(r2.json().get("output2", [])))
                        closes = [int(c["stck_clpr"]) for c in candles if c.get("stck_clpr")]
                        if len(closes) >= 15:
                            gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, 15)]
                            losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, 15)]
                            ag = sum(gains) / 14
                            al = sum(losses) / 14
                            rsi = round(100 - 100 / (1 + ag / al), 1) if al > 0 else 100
                    except Exception:
                        pass
                    if rsi is None or rsi < rsi_min or rsi > rsi_max:
                        continue

                checked += 1
                results.append({
                    "code": code, "name": name, "market": market,
                    "price": price, "per": per, "pbr": pbr,
                    "pct": pct, "rsi": rsi,
                    "volume": int(out.get("acml_vol", 0) or 0),
                    "w52_high": int(out.get("w52_hgpr", 0) or 0),
                    "w52_low": int(out.get("w52_lwpr", 0) or 0),
                })
            except Exception:
                continue

        return jsonify({"results": results, "checked": checked, "total": len(stocks)})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
