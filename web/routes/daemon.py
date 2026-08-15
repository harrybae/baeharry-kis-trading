import os
import sys
import json
import uuid
import importlib
import subprocess
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT_DIR)
sys.path.insert(0, os.path.join(ROOT_DIR, 'src'))

import config
import kis_api
import requests as req

daemon_bp = Blueprint("daemon", __name__)


TRADES_FILE = os.path.join(ROOT_DIR, 'data', 'trades.json')


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


@daemon_bp.route("/api/daemon/start", methods=["POST"])
def start_daemon():
    try:
        daemon_path = os.path.join(ROOT_DIR, 'daemons', 'auto_trading_with_risk_daemon.py')
        log_path = os.path.join(ROOT_DIR, 'logs', 'auto_trading_risk_daemon.log')
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        proc = subprocess.Popen(["python3", daemon_path],
                                stdout=open(log_path, 'a'),
                                stderr=subprocess.STDOUT)
        return jsonify({"ok": True, "pid": proc.pid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@daemon_bp.route("/api/daemon/stop", methods=["POST"])
def stop_daemon():
    try:
        subprocess.run(["pkill", "-f", "auto_trading_with_risk_daemon.py"], capture_output=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@daemon_bp.route("/api/daemon/status", methods=["GET"])
def daemon_status():
    try:
        import subprocess
        result = subprocess.run(["pgrep", "-f", "auto_trading_with_risk_daemon.py"], capture_output=True, text=True)
        pid = result.stdout.strip()
        return jsonify({
            "running": bool(pid),
            "pid": pid or None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@daemon_bp.route("/api/auto-config", methods=["GET"])
def get_auto_config():
    return jsonify({
        "is_paper": config.IS_PAPER_TRADING,
        "risk_tolerance": config.RISK_TOLERANCE,
        "take_profit": config.TAKE_PROFIT,
        "short_ma": config.SHORT_MA,
        "long_ma": config.LONG_MA,
        "trading_interval": config.TRADING_INTERVAL,
        "order_quantity": config.ORDER_QUANTITY,
    })


@daemon_bp.route("/api/auto-config", methods=["POST"])
def save_auto_config():
    try:
        data = request.json
        cfg_path = os.path.join(ROOT_DIR, "config.py")
        with open(cfg_path) as f:
            lines = f.readlines()

        def safe(val, default):
            return val if val is not None else default

        mapping = {
            "IS_PAPER_TRADING": str(safe(data.get("is_paper"), config.IS_PAPER_TRADING)),
            "RISK_TOLERANCE": str(safe(data.get("risk_tolerance"), config.RISK_TOLERANCE)),
            "TAKE_PROFIT": str(safe(data.get("take_profit"), config.TAKE_PROFIT)),
            "SHORT_MA": str(safe(data.get("short_ma"), config.SHORT_MA)),
            "LONG_MA": str(safe(data.get("long_ma"), config.LONG_MA)),
            "TRADING_INTERVAL": str(safe(data.get("trading_interval"), config.TRADING_INTERVAL)),
            "ORDER_QUANTITY": str(safe(data.get("order_quantity"), config.ORDER_QUANTITY)),
        }

        new_lines = []
        for line in lines:
            replaced = False
            for key, val in mapping.items():
                if line.startswith(key + " ="):
                    new_lines.append(f"{key} = {val}\n")
                    replaced = True
                    break
            if not replaced:
                new_lines.append(line)

        with open(cfg_path, "w") as f:
            f.writelines(new_lines)

        importlib.reload(config)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@daemon_bp.route("/api/auto-status", methods=["GET"])
def get_auto_status():
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'auto_trading_with_risk_daemon.py'],
            capture_output=True, text=True
        )
        is_running = result.returncode == 0
        pid = result.stdout.strip().split()[0] if result.stdout else None
    except Exception:
        is_running = False
        pid = None

    return jsonify({
        "daemon_running": is_running,
        "daemon_pid": pid,
        "mode": "모의투자" if config.IS_PAPER_TRADING else "실전투자",
        "stocks_monitoring": 5,
        "risk_tolerance": f"{(config.RISK_TOLERANCE or 0) * 100}%",
        "take_profit": f"{(config.TAKE_PROFIT or 0) * 100}%",
    })


@daemon_bp.route("/api/trades/fetch", methods=["GET"])
def fetch_trades():
    try:
        token = kis_api.get_access_token()
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": config.APP_KEY,
            "appsecret": config.APP_SECRET,
            "tr_id": "VTTC8001R" if config.IS_PAPER_TRADING else "TTTC8001R",
        }
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
        res = req.get(
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
                "memo": "KIS 자동",
            }
            data["trades"].append(trade)
            added += 1
        save_trades_data(data)
        return jsonify({"ok": True, "added": added})
    except Exception as e:
        import traceback
        print(f"❌ API 에러: {str(e)}")
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
