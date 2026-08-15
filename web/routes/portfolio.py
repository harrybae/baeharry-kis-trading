import os
import sys
import json
import uuid
import time
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config
import kis_api
import stock_master
from position_manager import PositionManager

portfolio_bp = Blueprint("portfolio", __name__)


WATCHLIST_FILE = os.path.join(ROOT_DIR, 'data', 'watchlist.json')
ALERTS_FILE = os.path.join(ROOT_DIR, 'data', 'alerts.json')
TRADES_FILE = os.path.join(ROOT_DIR, 'data', 'trades.json')
PRESETS_FILE = os.path.join(ROOT_DIR, 'data', 'auto_presets.json')


@portfolio_bp.route("/api/watchlist", methods=["GET"])
def get_watchlist():
    try:
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE) as f:
                return jsonify(json.load(f))
        return jsonify({"stocks": []})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/watchlist", methods=["POST"])
def save_watchlist():
    try:
        data = request.get_json()
        stocks = []
        if os.path.exists(WATCHLIST_FILE):
            with open(WATCHLIST_FILE) as f:
                existing = json.load(f)
            if isinstance(existing, dict) and "stocks" in existing:
                stocks = existing["stocks"]
            elif isinstance(existing, list):
                stocks = existing
        code = data.get("code", "")
        if not any((s.get("code") if isinstance(s, dict) else s) == code for s in stocks):
            stocks.append({"code": code, "name": data.get("name", code)})
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump({"stocks": stocks}, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/watchlist/<code>", methods=["DELETE"])
def delete_watchlist(code):
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        stocks = data.get("stocks", []) if isinstance(data, dict) else data
        stocks = [s for s in stocks if (s.get("code") if isinstance(s, dict) else s) != code]
        with open(WATCHLIST_FILE, 'w') as f:
            json.dump({"stocks": stocks}, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/watchlist/prices")
def get_watchlist_prices():
    try:
        if not os.path.exists(WATCHLIST_FILE):
            return jsonify([])
        with open(WATCHLIST_FILE) as f:
            stocks = json.load(f).get("stocks", [])
        token = kis_api.get_access_token()
        results = []
        for stock in stocks:
            code = stock["code"] if isinstance(stock, dict) else stock
            try:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "appkey": config.APP_KEY,
                    "appsecret": config.APP_SECRET,
                    "tr_id": "FHKST01010100",
                    "Content-Type": "application/json; charset=utf-8",
                }
                params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
                import requests as req
                res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                              headers=headers, params=params, timeout=10)
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
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/alerts", methods=["GET"])
def get_alerts():
    try:
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f:
                return jsonify(json.load(f))
        return jsonify({"alerts": []})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/alerts", methods=["POST"])
def save_alerts():
    try:
        data = request.get_json()
        with open(ALERTS_FILE, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
        return jsonify({"ok": True})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/alerts/check")
def check_alerts():
    try:
        if not os.path.exists(ALERTS_FILE):
            return jsonify({"triggered": []})
        with open(ALERTS_FILE) as f:
            data = json.load(f)
        alerts = data.get("alerts", [])
        triggered = []
        token = kis_api.get_access_token()
        price_cache = {}
        import requests as req
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
                    res = req.get(f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                                  headers=headers, params=params, timeout=10)
                    out = res.json().get("output", {})
                    price_cache[code] = {
                        "price": int(out.get("stck_prpr", 0)),
                        "change_pct": float(out.get("prdy_ctrt", 0) or 0),
                        "volume": int(out.get("acml_vol", 0) or 0),
                        "avg_volume": int(out.get("avrg_vol", 0) or 0),
                        "name": out.get("hts_kor_isnm", code),
                    }
                except Exception:
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
                try:
                    tg_url = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage"
                    req.post(tg_url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
                except Exception:
                    pass
        return jsonify({"triggered": triggered, "checked_at": datetime.now().strftime("%H:%M:%S")})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


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


@portfolio_bp.route("/api/portfolio")
def get_portfolio():
    try:
        try:
            holdings, deposit, avg_costs = kis_api.get_balance()
        except Exception as e:
            return jsonify({"holdings": [], "deposit": 0, "deposit_weight": 100,
                            "total_eval": 0, "total": 0, "error": str(e)})

        if not holdings:
            return jsonify({"holdings": [], "deposit": deposit, "deposit_weight": 100,
                            "total_eval": 0, "total": deposit})

        buy_map = {}
        if os.path.exists(TRADES_FILE):
            with open(TRADES_FILE) as f:
                trades = json.load(f).get("trades", [])
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
            if bm.get("qty", 0) > 0:
                avg_cost = bm["cost"] / bm["qty"]
            else:
                avg_cost = avg_costs.get(code, 0)
            try:
                price = kis_api.get_current_price(code)
            except Exception as e:
                print(f"현재가 조회 실패 {code}: {e}")
                price = 0
            try:
                results = stock_master.search_stocks(code, limit=1)
                name = results[0]["name"] if results else code
            except Exception:
                name = code
            eval_amt = price * qty
            pnl = eval_amt - round(avg_cost) * qty if avg_cost > 0 else 0
            pnl_pct = round(pnl / (round(avg_cost) * qty) * 100, 2) if avg_cost > 0 else 0
            total_eval += eval_amt
            items.append({
                "code": code, "name": name, "qty": qty,
                "price": price, "eval_amt": eval_amt,
                "avg_cost": round(avg_cost),
                "pnl": pnl, "pnl_pct": pnl_pct, "weight": 0,
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
        return jsonify({"error": str(e), "holdings": [], "deposit": 0,
                        "deposit_weight": 100, "total_eval": 0, "total": 0}), 200


@portfolio_bp.route("/api/trades", methods=["GET"])
def get_trades():
    data = load_trades()
    trades = data.get("trades", [])
    sell_trades = [t for t in trades if t.get("type") == "SELL"]
    realized = sum(t.get("pnl", 0) for t in sell_trades)
    wins = [t for t in sell_trades if t.get("pnl", 0) > 0]
    losses = [t for t in sell_trades if t.get("pnl", 0) < 0]
    win_rate = round(len(wins) / len(sell_trades) * 100, 1) if sell_trades else 0
    avg_win = round(sum(t.get("pnl", 0) for t in wins) / len(wins)) if wins else 0
    avg_loss = round(sum(t.get("pnl", 0) for t in losses) / len(losses)) if losses else 0
    total_pnl_pct = round(realized / sum(t.get("amount", 0) for t in sell_trades) * 100, 2) if sell_trades else 0
    code_map = {}
    for t in trades:
        code = t.get("code", "")
        if code not in code_map:
            code_map[code] = {"code": code, "name": t.get("name", code), "count": 0, "amount": 0}
        code_map[code]["count"] += 1
        code_map[code]["amount"] += t.get("amount", 0)
    cumulative = []
    cum = 0
    for t in sorted(trades, key=lambda x: x.get("date", "")):
        if t.get("type") == "SELL":
            cum += t.get("pnl", 0)
            cumulative.append({"date": t.get("date"), "pnl": cum})
    return jsonify({
        "trades": trades,
        "stats": {
            "total": len(trades),
            "realized": realized,
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "total_pnl_pct": total_pnl_pct,
        },
        "by_code": list(code_map.values()),
        "cumulative": cumulative,
    })


@portfolio_bp.route("/api/trades", methods=["POST"])
def save_trade():
    data = load_trades()
    trade = request.json
    if not trade:
        return jsonify({"error": "데이터 없음"}), 400
    trade["id"] = str(uuid.uuid4())[:8]
    if "pnl" not in trade:
        trade["pnl"] = 0
    data["trades"].append(trade)
    save_trades_data(data)
    return jsonify({"ok": True, "id": trade["id"]})


@portfolio_bp.route("/api/trades/<tid>", methods=["DELETE"])
def delete_trade(tid):
    data = load_trades()
    data["trades"] = [t for t in data["trades"] if t.get("id") != tid]
    save_trades_data(data)
    return jsonify({"ok": True})


@portfolio_bp.route("/api/positions", methods=["GET"])
def get_positions():
    pm = PositionManager()
    summary = pm.get_summary()
    positions = pm.get_all_positions()
    return jsonify({"summary": summary, "positions": positions})


@portfolio_bp.route("/api/auto-presets", methods=["GET"])
def get_auto_presets():
    try:
        if not os.path.exists(PRESETS_FILE):
            return jsonify({"presets": []})
        with open(PRESETS_FILE) as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({"presets": []})


@portfolio_bp.route("/api/auto-presets", methods=["POST"])
def save_auto_preset():
    try:
        presets = []
        if os.path.exists(PRESETS_FILE):
            with open(PRESETS_FILE) as f:
                presets = json.load(f).get("presets", [])
        data = request.json
        data["id"] = int(time.time() * 1000)
        data["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        presets.append(data)
        with open(PRESETS_FILE, "w") as f:
            json.dump({"presets": presets}, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True, "id": data["id"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@portfolio_bp.route("/api/auto-presets/<int:pid>", methods=["DELETE"])
def delete_auto_preset(pid):
    try:
        if not os.path.exists(PRESETS_FILE):
            return jsonify({"ok": True})
        with open(PRESETS_FILE) as f:
            data = json.load(f)
        data["presets"] = [p for p in data["presets"] if p["id"] != pid]
        with open(PRESETS_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
