from flask import Flask, jsonify, render_template, request, make_response
import requests as req
import sys, os
import re
sys.path.insert(0, os.path.expanduser("~/trading"))
import config
import kis_api
import stock_master

app = Flask(__name__)
_balance_cache = {"holdings": {}, "deposit": 0}

@app.route("/favicon.ico")
def favicon():
    from flask import send_from_directory
    return send_from_directory(os.path.join(app.root_path, "static"), "favicon.ico", mimetype="image/vnd.microsoft.icon")

def validate_stock_code(code):
    if not code or not isinstance(code, str):
        return False
    return bool(re.match(r'^\d{6}$', code.strip()))
stock_master.ensure_updated()

@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

@app.route("/api/search/<keyword>")
def search_stock(keyword):
    try:
        results = stock_master.search_stocks(keyword, limit=8)
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stock/<stock_code>")
def get_stock(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        from datetime import datetime
        token = kis_api.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "FHKST01010100",
            "Content-Type": "application/json; charset=utf-8",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params)
        res.raise_for_status()
        out = res.json().get("output", {})
        price = int(out.get("stck_prpr", 0))
        prev = int(out.get("stck_sdpr", 0))
        change = price - prev
        try:
            holdings, deposit, avg_costs = kis_api.get_balance()
            qty = holdings.get(stock_code, 0)
            _balance_cache['holdings'] = holdings
            _balance_cache['deposit'] = deposit
            _balance_cache['avg_costs'] = avg_costs
        except Exception as e:
            print(f"잔고 조회 실패: {e}")
            holdings = _balance_cache.get('holdings', {})
            deposit = _balance_cache.get('deposit', 0)
            avg_costs = _balance_cache.get('avg_costs', {})
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/chart/<stock_code>")
def get_chart(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        from datetime import datetime, timedelta
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
            headers=headers, params=params
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
        # 보조지표 계산
        import math
        closes = [c["close"] for c in chart]

        # 볼린저밴드 (20일)
        for i, c in enumerate(chart):
            if i >= 19:
                window = closes[i-19:i+1]
                ma = sum(window) / 20
                std = math.sqrt(sum((x-ma)**2 for x in window) / 20)
                c["bb_upper"] = round(ma + 2*std)
                c["bb_mid"] = round(ma)
                c["bb_lower"] = round(ma - 2*std)
            else:
                c["bb_upper"] = c["bb_mid"] = c["bb_lower"] = None

        # RSI (14일)
        for i, c in enumerate(chart):
            if i >= 14:
                gains = [max(closes[j]-closes[j-1],0) for j in range(i-13,i+1)]
                losses = [max(closes[j-1]-closes[j],0) for j in range(i-13,i+1)]
                ag = sum(gains)/14; al = sum(losses)/14
                c["rsi"] = round(100 - 100/(1+ag/al), 2) if al > 0 else 100.0
            else:
                c["rsi"] = None

        # MACD (12,26,9)
        def ema_calc(data, n):
            if len(data) < n: return [None]*len(data)
            k = 2.0/(n+1)
            result = [None]*(n-1)
            val = sum(data[:n])/n
            result.append(val)
            for x in data[n:]:
                val = x*k + val*(1-k)
                result.append(val)
            return result

        ema12 = ema_calc(closes, 12)
        ema26 = ema_calc(closes, 26)
        macd_line = [round(ema12[i]-ema26[i],2) if ema12[i] is not None and ema26[i] is not None else None for i in range(len(closes))]
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
            c["macd_hist"] = round(macd_line[i]-signal_full[i], 2) if macd_line[i] is not None and signal_full[i] is not None else None

        return jsonify(chart)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/quant/<stock_code>")
def get_quant(stock_code):
    if not validate_stock_code(stock_code):
        return jsonify({"error": "유효하지 않은 종목코드입니다. 6자리 숫자를 입력하세요."}), 400
    try:
        from datetime import datetime, timedelta
        token = kis_api.get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "FHKST01010100",
            "Content-Type": "application/json; charset=utf-8",
        }
        # 현재가 조회
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code}
        res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params)
        res.raise_for_status()
        out = res.json().get("output", {})

        # 일봉 1년치 조회
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
            headers=headers2, params=chart_params
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

        # 모멘텀 계산
        ret_1m = round((closes[-1] / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None
        ret_3m = round((closes[-1] / closes[-63] - 1) * 100, 2) if len(closes) >= 63 else None
        ret_6m = round((closes[-1] / closes[-126] - 1) * 100, 2) if len(closes) >= 126 else None
        ret_1y = round((closes[-1] / closes[0] - 1) * 100, 2) if len(closes) >= 2 else None
        w52_pos = round((price - w52_low) / (w52_high - w52_low) * 100, 1) if w52_high > w52_low else None

        # 이동평균 계산
        def ma(n):
            if len(closes) >= n:
                return round(sum(closes[-n:]) / n)
            return None
        ma5 = ma(5); ma20 = ma(20); ma60 = ma(60); ma120 = ma(120)
        ma5_sig = "골든크로스" if ma5 and ma20 and ma5 > ma20 else "데드크로스"
        ma_trend = "상승" if ma20 and ma60 and ma20 > ma60 else "하락"

        # 변동성/리스크 계산
        if len(closes) >= 20:
            import math
            returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
            avg_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - avg_r) ** 2 for r in returns) / len(returns))
            annual_vol = round(std_r * math.sqrt(252) * 100, 2)
            # MDD
            peak = closes[0]
            mdd = 0
            for c in closes:
                if c > peak: peak = c
                dd = (c - peak) / peak
                if dd < mdd: mdd = dd
            mdd = round(mdd * 100, 2)
            # 샤프지수 (무위험수익률 3.5% 가정)
            rf = 0.035 / 252
            excess = [r - rf for r in returns]
            sharpe = round((sum(excess) / len(excess)) / std_r * math.sqrt(252), 2) if std_r > 0 else None
        else:
            annual_vol = mdd = sharpe = None

        # 가치 평가
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
            # 가치
            "per": per, "pbr": pbr, "eps": eps, "bps": bps,
            "per_grade": per_grade, "pbr_grade": pbr_grade,
            "frgn_rate": frgn_rate,
            # 모멘텀
            "ret_1m": ret_1m, "ret_3m": ret_3m, "ret_6m": ret_6m, "ret_1y": ret_1y,
            "w52_high": w52_high, "w52_low": w52_low, "w52_pos": w52_pos,
            # 이동평균
            "ma5": ma5, "ma20": ma20, "ma60": ma60, "ma120": ma120,
            "ma5_sig": ma5_sig, "ma_trend": ma_trend,
            "price_vs_ma20": round((price / ma20 - 1) * 100, 2) if ma20 else None,
            "price_vs_ma60": round((price / ma60 - 1) * 100, 2) if ma60 else None,
            # 변동성
            "annual_vol": annual_vol, "mdd": mdd, "sharpe": sharpe,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    try:
        import json as js
        wf = os.path.expanduser("~/trading/watchlist.json")
        if os.path.exists(wf):
            with open(wf) as f:
                return jsonify(js.load(f))
        return jsonify({"stocks": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchlist", methods=["POST"])
def save_watchlist():
    try:
        import json as js
        data = request.get_json()
        wf = os.path.expanduser("~/trading/watchlist.json")
        with open(wf, 'w') as f:
            js.dump(data, f)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/watchlist/prices")
def get_watchlist_prices():
    try:
        import json as js
        from datetime import datetime
        wf = os.path.expanduser("~/trading/watchlist.json")
        if not os.path.exists(wf):
            return jsonify([])
        with open(wf) as f:
            stocks = js.load(f).get("stocks", [])
        token = kis_api.get_access_token()
        results = []
        for code in stocks:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "appkey": config.APP_KEY,
                    "appsecret": config.APP_SECRET,
                    "tr_id": "FHKST01010100",
                    "Content-Type": "application/json; charset=utf-8",
                }
                params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
                res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params)
                out = res.json().get("output", {})
                price = int(out.get("stck_prpr", 0))
                prev = int(out.get("stck_sdpr", 0))
                w52h = int(out.get("w52_hgpr", 0) or 0)
                w52l = int(out.get("w52_lwpr", 0) or 0)
                w52_pos = round((price - w52l) / (w52h - w52l) * 100, 1) if w52h > w52l else None
                name_res = stock_master.search_stocks(code, limit=1)
                name = name_res[0]["name"] if name_res else code
                market = name_res[0]["market"] if name_res else ""
                results.append({
                    "code": code, "name": name, "market": market,
                    "price": price,
                    "change": price - prev,
                    "change_pct": float(out.get("prdy_ctrt", 0) or 0),
                    "volume": int(out.get("acml_vol", 0) or 0),
                    "frgn_net_buy": int(out.get("frgn_ntby_qty", 0) or 0),
                    "w52_high": w52h, "w52_low": w52l, "w52_pos": w52_pos,
                    "timestamp": datetime.now().strftime("%H:%M:%S"),
                })
            except Exception as e:
                results.append({"code": code, "name": code, "error": str(e)})
        return jsonify(results)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/alerts", methods=["GET"])
def get_alerts():
    try:
        import json as js
        af = os.path.expanduser("~/trading/alerts.json")
        if os.path.exists(af):
            with open(af) as f:
                return jsonify(js.load(f))
        return jsonify({"alerts": []})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/alerts", methods=["POST"])
def save_alerts():
    try:
        import json as js
        data = request.get_json()
        af = os.path.expanduser("~/trading/alerts.json")
        with open(af, 'w') as f:
            js.dump(data, f, ensure_ascii=False)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/alerts/check")
def check_alerts():
    try:
        import json as js
        from datetime import datetime
        af = os.path.expanduser("~/trading/alerts.json")
        if not os.path.exists(af):
            return jsonify({"triggered": []})
        with open(af) as f:
            data = js.load(f)
        alerts = data.get("alerts", [])
        triggered = []
        token = kis_api.get_access_token()
        price_cache = {}
        for alert in alerts:
            if not alert.get("active", True):
                continue
            code = alert["code"]
            if code not in price_cache:
                try:
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "appkey": config.APP_KEY,
                        "appsecret": config.APP_SECRET,
                        "tr_id": "FHKST01010100",
                        "Content-Type": "application/json; charset=utf-8",
                    }
                    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
                    res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price", headers=headers, params=params)
                    out = res.json().get("output", {})
                    price_cache[code] = {
                        "price": int(out.get("stck_prpr", 0)),
                        "change_pct": float(out.get("prdy_ctrt", 0) or 0),
                        "volume": int(out.get("acml_vol", 0) or 0),
                        "avg_volume": int(out.get("avrg_vol", 0) or 0),
                        "name": out.get("hts_kor_isnm", code),
                    }
                except:
                    continue
            p = price_cache[code]
            price = p["price"]
            change_pct = p["change_pct"]
            volume = p["volume"]
            avg_vol = p["avg_volume"] or 1
            name = p["name"]
            fired = False
            msg = ""
            atype = alert.get("type")
            val = float(alert.get("value", 0))
            if atype == "target" and price >= val:
                fired = True
                msg = f"🎯 목표가 도달! {name}({code}) 현재가 {price:,}원 ≥ 목표가 {int(val):,}원"
            elif atype == "stop" and price <= val:
                fired = True
                msg = f"🛑 손절가 도달! {name}({code}) 현재가 {price:,}원 ≤ 손절가 {int(val):,}원"
            elif atype == "pct_up" and change_pct >= val:
                fired = True
                msg = f"📈 등락률 도달! {name}({code}) 등락률 +{change_pct:.2f}% ≥ +{val:.2f}%"
            elif atype == "pct_dn" and change_pct <= -val:
                fired = True
                msg = f"📉 등락률 도달! {name}({code}) 등락률 {change_pct:.2f}% ≤ -{val:.2f}%"
            elif atype == "volume" and volume >= avg_vol * val:
                fired = True
                msg = f"🔥 거래량 급등! {name}({code}) 거래량 {volume:,} (평균대비 {volume/avg_vol:.1f}배)"
            if fired:
                triggered.append({"alert": alert, "msg": msg, "price": price})
                # 텔레그램 발송
                try:
                    tg_url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
                    req.post(tg_url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg})
                except:
                    pass
        return jsonify({"triggered": triggered, "checked_at": datetime.now().strftime("%H:%M:%S")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/portfolio")
def get_portfolio():
    try:
        import json as js, os
        # 잔고 조회
        try:
            holdings, deposit, avg_costs = kis_api.get_balance()
        except Exception as e:
            return jsonify({"holdings": [], "deposit": 0, "deposit_weight": 100, "total_eval": 0, "total": 0, "error": str(e)})

        if not holdings:
            return jsonify({"holdings": [], "deposit": deposit, "deposit_weight": 100, "total_eval": 0, "total": deposit})

        # 매입가 계산
        tf = os.path.expanduser("~/trading/trades.json")
        buy_map = {}
        if os.path.exists(tf):
            with open(tf) as f:
                trades = js.load(f).get("trades", [])
            for t in sorted(trades, key=lambda x: x["date"]):
                if t["code"] not in buy_map:
                    buy_map[t["code"]] = {"qty": 0, "cost": 0}
                if t["type"] == "BUY":
                    buy_map[t["code"]]["qty"] += t["qty"]
                    buy_map[t["code"]]["cost"] += t["amount"] + (t.get("fee") or 0)
                else:
                    avg = buy_map[t["code"]]["cost"] / buy_map[t["code"]]["qty"] if buy_map[t["code"]]["qty"] > 0 else 0
                    buy_map[t["code"]]["qty"] -= t["qty"]
                    buy_map[t["code"]]["cost"] -= avg * t["qty"]

        items = []
        total_eval = 0
        for code, qty in holdings.items():
            bm = buy_map.get(code, {})
            # trades.json 우선, 없으면 KIS API 평균단가 사용
            if bm.get("qty", 0) > 0:
                avg_cost = bm["cost"] / bm["qty"]
            else:
                avg_cost = avg_costs.get(code, 0)
            # 현재가 조회
            try:
                price = kis_api.get_current_price(code)
            except Exception as e:
                print(f"현재가 조회 실패 {code}: {e}")
                price = 0
            # 종목명 조회
            try:
                results = stock_master.search_stocks(code, limit=1)
                name = results[0]["name"] if results else code
            except:
                name = code
            eval_amt = price * qty
            pnl = eval_amt - round(avg_cost) * qty if avg_cost > 0 else 0
            pnl_pct = round(pnl / (round(avg_cost) * qty) * 100, 2) if avg_cost > 0 else 0
            total_eval += eval_amt
            items.append({
                "code": code, "name": name, "qty": qty,
                "price": price, "eval_amt": eval_amt,
                "avg_cost": round(avg_cost),
                "pnl": pnl, "pnl_pct": pnl_pct, "weight": 0
            })

        total = total_eval + deposit
        for item in items:
            item["weight"] = round(item["eval_amt"] / total * 100, 1) if total > 0 else 0
        return jsonify({
            "holdings": items,
            "deposit": deposit,
            "deposit_weight": round(deposit / total * 100, 1) if total > 0 else 100,
            "total_eval": total_eval,
            "total": total,
        })
    except Exception as e:
        return jsonify({"error": str(e), "holdings": [], "deposit": 0, "deposit_weight": 100, "total_eval": 0, "total": 0}), 200


@app.route("/api/news/<stock_name>")
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
            headers=headers
        )
        res.raise_for_status()
        items = res.json().get("items", [])
        news = []
        for item in items:
            import re
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


@app.route("/api/market")
def get_market():
    try:
        from datetime import datetime
        token = kis_api.get_access_token()
        result = {}

        # KOSPI/KOSDAQ 지수
        for code, name, key in [("0001","KOSPI","kospi"),("1001","KOSDAQ","kosdaq")]:
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "appkey": config.APP_KEY,
                    "appsecret": config.APP_SECRET,
                    "tr_id": "FHPUP02100000",
                    "Content-Type": "application/json; charset=utf-8",
                }
                params = {
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": code,
                }
                res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-index-price", headers=headers, params=params)
                out = res.json().get("output", {})
                result[key] = {
                    "name": name,
                    "value": float(out.get("bstp_nmix_prpr", 0) or 0),
                    "change": float(out.get("bstp_nmix_prdy_vrss", 0) or 0),
                    "change_pct": float(out.get("prdy_ctrt", 0) or 0),
                }
            except:
                result[key] = {"name": name, "value": 0, "change": 0, "change_pct": 0}

        # 원달러 환율 (exchangerate-api 무료)
        try:
            res = req.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=5)
            krw = res.json().get("rates", {}).get("KRW", 0)
            result["usdkrw"] = {"name": "USD/KRW", "value": round(krw, 1), "change": 0, "change_pct": 0}
        except:
            result["usdkrw"] = {"name": "USD/KRW", "value": 0, "change": 0, "change_pct": 0}

        # 나스닥/S&P500 선물 (Yahoo Finance)
        for sym, key, name in [("NQ=F","nasdaq","나스닥"), ("ES=F","sp500","S&P500")]:
            try:
                res = req.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d",
                    headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
                data = res.json()
                meta = data.get("chart",{}).get("result",[{}])[0].get("meta",{})
                price = float(meta.get("regularMarketPrice", 0) or 0)
                prev = float(meta.get("chartPreviousClose", 0) or 0)
                chg = round(price - prev, 2)
                chg_pct = round((price / prev - 1) * 100, 2) if prev > 0 else 0
                result[key] = {"name": name, "value": round(price, 1), "change": chg, "change_pct": chg_pct}
            except:
                result[key] = {"name": name, "value": 0, "change": 0, "change_pct": 0}

        result["updated"] = datetime.now().strftime("%H:%M")
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/screener")
def run_screener():
    try:
        import json as js, math
        from datetime import datetime, timedelta

        per_min = float(request.args.get("per_min", 0) or 0)
        per_max = float(request.args.get("per_max", 9999) or 9999)
        pbr_min = float(request.args.get("pbr_min", 0) or 0)
        pbr_max = float(request.args.get("pbr_max", 9999) or 9999)
        pct_min = float(request.args.get("pct_min", -99) or -99)
        pct_max = float(request.args.get("pct_max", 99) or 99)
        rsi_min = float(request.args.get("rsi_min", 0) or 0)
        rsi_max = float(request.args.get("rsi_max", 100) or 100)
        limit = int(request.args.get("limit", 20) or 20)

        # stock_master에서 전체 종목 가져오기
        import sqlite3, os
        db_path = os.path.expanduser("~/trading/stock_master.db")
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
                if price == 0: continue

                # PER 조건
                if per_max < 9999 or per_min > 0:
                    if per <= 0 or per < per_min or per > per_max: continue
                # PBR 조건
                if pbr_max < 9999 or pbr_min > 0:
                    if pbr <= 0 or pbr < pbr_min or pbr > pbr_max: continue
                # 등락률 조건
                if pct < pct_min or pct > pct_max: continue

                # RSI 조건 (필요시만 계산)
                rsi = None
                if rsi_min > 0 or rsi_max < 100:
                    try:
                        h2 = dict(headers); h2["tr_id"] = "FHKST03010100"
                        end = datetime.now(); start = end - timedelta(days=30)
                        cp = {
                            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                            "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
                            "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
                            "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
                        }
                        r2 = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                            headers=h2, params=cp, timeout=3)
                        candles = list(reversed(r2.json().get("output2", [])))
                        closes = [int(c["stck_clpr"]) for c in candles if c.get("stck_clpr")]
                        if len(closes) >= 15:
                            gains = [max(closes[i]-closes[i-1],0) for i in range(1,15)]
                            losses = [max(closes[i-1]-closes[i],0) for i in range(1,15)]
                            ag = sum(gains)/14; al = sum(losses)/14
                            rsi = round(100 - 100/(1+ag/al), 1) if al > 0 else 100
                    except: pass
                    if rsi is None or rsi < rsi_min or rsi > rsi_max: continue

                checked += 1
                results.append({
                    "code": code, "name": name, "market": market,
                    "price": price, "per": per, "pbr": pbr,
                    "pct": pct, "rsi": rsi,
                    "volume": int(out.get("acml_vol", 0) or 0),
                    "w52_high": int(out.get("w52_hgpr", 0) or 0),
                    "w52_low": int(out.get("w52_lwpr", 0) or 0),
                })
            except: continue

        return jsonify({"results": results, "checked": checked, "total": len(stocks)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ===== 매매일지 =====
TRADES_FILE = os.path.expanduser("~/trading/trades.json")

def load_trades():
    try:
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE) as f:
                return json.load(f)
    except Exception as e:
        print(f"매매일지 로드 오류: {e}")
    return {"trades": []}

def save_trades_data(data):
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

@app.route("/api/trades", methods=["GET"])
def get_trades():
    data = load_trades()
    trades = data.get("trades", [])
    # 통계 계산
    buy_trades = [t for t in trades if t.get("type") == "BUY"]
    sell_trades = [t for t in trades if t.get("type") == "SELL"]
    total = len(trades)
    realized = sum(t.get("pnl", 0) for t in sell_trades)
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("pnl", 0) < 0]
    win_rate = round(len(wins) / len(sell_trades) * 100, 1) if sell_trades else 0
    avg_win = round(sum(t.get("pnl", 0) for t in wins) / len(wins)) if wins else 0
    avg_loss = round(sum(t.get("pnl", 0) for t in losses) / len(losses)) if losses else 0
    total_pnl_pct = round(realized / sum(t.get("amount", 0) for t in sell_trades) * 100, 2) if sell_trades else 0
    # 종목별 거래비중
    code_map = {}
    for t in trades:
        code = t.get("code", "")
        if code not in code_map:
            code_map[code] = {"code": code, "name": t.get("name", code), "count": 0, "amount": 0}
        code_map[code]["count"] += 1
        code_map[code]["amount"] += t.get("amount", 0)
    # 누적 수익 추이
    cumulative = []
    cum = 0
    for t in sorted(trades, key=lambda x: x.get("date", "")):
        if t.get("type") == "SELL":
            cum += t.get("pnl", 0)
            cumulative.append({"date": t.get("date"), "pnl": cum})
    return jsonify({
        "trades": trades,
        "stats": {
            "total": total,
            "realized": realized,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "total_pnl_pct": total_pnl_pct,
        },
        "by_code": list(code_map.values()),
        "cumulative": cumulative
    })

@app.route("/api/trades", methods=["POST"])
def save_trade():
    data = load_trades()
    trade = request.json
    if not trade:
        return jsonify({"error": "데이터 없음"}), 400
    import uuid
    trade["id"] = str(uuid.uuid4())[:8]
    if "pnl" not in trade:
        trade["pnl"] = 0
    data["trades"].append(trade)
    save_trades_data(data)
    return jsonify({"ok": True, "id": trade["id"]})

@app.route("/api/trades/<tid>", methods=["DELETE"])
def delete_trade(tid):
    data = load_trades()
    data["trades"] = [t for t in data["trades"] if t.get("id") != tid]
    save_trades_data(data)
    return jsonify({"ok": True})

@app.route("/api/trades/fetch", methods=["GET"])
def fetch_trades():
    try:
        token = kis_api.get_access_token()
        import requests as _req
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "VTTC8001R" if config.IS_PAPER_TRADING else "TTTC8001R",
        }
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y%m%d")
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
        params = {
            "CANO": config.ACCOUNT_NUMBER,
            "ACNT_PRDT_CD": config.ACCOUNT_SUFFIX,
            "INQR_STRT_DT": week_ago,
            "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00",
            "INQR_DVSN": "00",
            "PDNO": "",
            "CCLD_DVSN": "01",
            "ORD_GNO_BRNO": "",
            "ODNO": "",
            "INQR_DVSN_3": "00",
            "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        res = _req.get(
            f"{config.BASE_URL}/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        items = res.json().get("output1", [])
        data = load_trades()
        existing_ids = {t.get("kis_id") for t in data["trades"] if t.get("kis_id")}
        added = 0
        for item in items:
            kid = item.get("odno", "")
            if kid in existing_ids:
                continue
            trade = {
                "id": kid[:8] if kid else str(uuid.uuid4())[:8],
                "kis_id": kid,
                "date": item.get("ord_dt", ""),
                "code": item.get("pdno", ""),
                "name": item.get("prdt_name", ""),
                "type": "BUY" if item.get("sll_buy_dvsn_cd") == "02" else "SELL",
                "qty": int(item.get("ccld_qty", 0)),
                "price": int(item.get("ccld_unpr3", 0)),
                "amount": int(item.get("ccld_qty", 0)) * int(item.get("ccld_unpr3", 0)),
                "fee": 0,
                "pnl": 0,
                "memo": "KIS 자동"
            }
            data["trades"].append(trade)
            added += 1
        save_trades_data(data)
        return jsonify({"ok": True, "added": added})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("✅ 웹 대시보드 시작: http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
