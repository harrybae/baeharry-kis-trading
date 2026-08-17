# -*- coding: utf-8 -*-
"""
스크립트 기반 매매 엔진
- 사용자가 작성한 Python 스크립트를 샌드박스에서 실행
- 백테스트, 활성 스크립트 평가, 템플릿 관리
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests as req

# 설정
BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BASE_DIR / "data" / "scripts"
SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
SCRIPTS_DB = SCRIPTS_DIR / "scripts.json"

# safe globals에 노출할 모듈/객체
_ALLOWED_MODULES = {
    "__builtins__": {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "range": range,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
    },
}

# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------
@dataclass
class Signal:
    action: str  # "buy" | "sell" | "hold"
    symbol: str
    quantity: int
    price: float
    reason: str = ""
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "price": self.price,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class BacktestResult:
    symbol: str
    start: str
    end: str
    total_return_pct: float
    win_rate: float
    max_drawdown_pct: float
    trade_count: int
    signals: List[Dict[str, Any]]
    error: str = ""
    initial_cash: float = 10_000_000

    def to_dict(self) -> Dict[str, Any]:
        # JSON 직렬화 호환성을 위해 numpy/pandas 타입을 Python 기본 타입으로 변환
        def _convert(value: Any) -> Any:
            if isinstance(value, dict):
                return {k: _convert(v) for k, v in value.items()}
            if isinstance(value, list):
                return [_convert(v) for v in value]
            if hasattr(value, "item"):  # numpy scalar/pandas 타입
                return value.item()
            return value

        return _convert({
            "symbol": self.symbol,
            "start": self.start,
            "end": self.end,
            "total_return_pct": self.total_return_pct,
            "win_rate": self.win_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
            "trade_count": self.trade_count,
            "signals": self.signals,
            "error": self.error,
            "initial_cash": self.initial_cash,
        })


# ---------------------------------------------------------------------------
# 템플릿
# ---------------------------------------------------------------------------
DEFAULT_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "ma-cross",
        "name": "이동평균 골든/데드크로스",
        "code": "def evaluate(data, ctx):\n"
                "    # data: pandas DataFrame (open, high, low, close, volume)\n"
                "    # ctx: {symbol, cash, position}\n"
                "    close = data['close']\n"
                "    ma5 = close.rolling(window=5).mean()\n"
                "    ma20 = close.rolling(window=20).mean()\n"
                "    if len(close) < 20:\n"
                "        return []\n"
                "    if ma5.iloc[-2] <= ma20.iloc[-2] and ma5.iloc[-1] > ma20.iloc[-1]:\n"
                "        return [Signal('buy', ctx['symbol'], 1, close.iloc[-1], 'MA5 > MA20 골든크로스')]\n"
                "    if ma5.iloc[-2] >= ma20.iloc[-2] and ma5.iloc[-1] < ma20.iloc[-1]:\n"
                "        return [Signal('sell', ctx['symbol'], 1, close.iloc[-1], 'MA5 < MA20 데드크로스')]\n"
                "    return []\n",
    },
    {
        "id": "rsi",
        "name": "RSI 과매수/과매도",
        "code": "def evaluate(data, ctx):\n"
                "    close = data['close']\n"
                "    if len(close) < 14:\n"
                "        return []\n"
                "    delta = close.diff()\n"
                "    gain = delta.where(delta > 0, 0).rolling(window=14).mean()\n"
                "    loss = -delta.where(delta < 0, 0).rolling(window=14).mean()\n"
                "    rs = gain / loss\n"
                "    rsi = 100 - (100 / (1 + rs))\n"
                "    if rsi.iloc[-1] < 30 and ctx.get('position', 0) == 0:\n"
                "        return [Signal('buy', ctx['symbol'], 1, close.iloc[-1], f'RSI {rsi.iloc[-1]:.1f} 과매도')]\n"
                "    if rsi.iloc[-1] > 70 and ctx.get('position', 0) > 0:\n"
                "        return [Signal('sell', ctx['symbol'], 1, close.iloc[-1], f'RSI {rsi.iloc[-1]:.1f} 과매수')]\n"
                "    return []\n",
    },
    {
        "id": "volume-spike",
        "name": "거래량 급등",
        "code": "def evaluate(data, ctx):\n"
                "    if len(data) < 20:\n"
                "        return []\n"
                "    vol = data['volume']\n"
                "    avg_vol = vol.rolling(window=20).mean().iloc[-1]\n"
                "    today_vol = vol.iloc[-1]\n"
                "    close = data['close'].iloc[-1]\n"
                "    if today_vol > avg_vol * 2 and ctx.get('position', 0) == 0:\n"
                "        return [Signal('buy', ctx['symbol'], 1, close, f'거래량 {today_vol/avg_vol:.1f}배 급등')]\n"
                "    return []\n",
    },
]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _load_scripts_db() -> Dict[str, Dict[str, Any]]:
    if not SCRIPTS_DB.exists():
        return {}
    try:
        with SCRIPTS_DB.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_scripts_db(data: Dict[str, Dict[str, Any]]) -> None:
    with SCRIPTS_DB.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def list_scripts() -> List[Dict[str, Any]]:
    db = _load_scripts_db()
    items = sorted(db.values(), key=lambda x: x.get("updated_at", x.get("created_at", "")), reverse=True)
    return items


def get_script(script_id: str) -> Optional[Dict[str, Any]]:
    return _load_scripts_db().get(script_id)


def save_script(name: str, code: str, script_id: Optional[str] = None,
                symbols: Optional[List[str]] = None, active: bool = False) -> Dict[str, Any]:
    db = _load_scripts_db()
    now = _now()
    if script_id and script_id in db:
        item = db[script_id]
        item["name"] = name
        item["code"] = code
        item["symbols"] = symbols if symbols is not None else item.get("symbols", [])
        item["active"] = active
        item["updated_at"] = now
    else:
        script_id = script_id or str(uuid.uuid4())[:8]
        item = {
            "id": script_id,
            "name": name,
            "code": code,
            "symbols": symbols or [],
            "active": active,
            "created_at": now,
            "updated_at": now,
        }
    db[script_id] = item
    _save_scripts_db(db)
    return item


def delete_script(script_id: str) -> bool:
    db = _load_scripts_db()
    if script_id not in db:
        return False
    if db[script_id].get("active", False):
        return False
    del db[script_id]
    _save_scripts_db(db)
    return True


def set_script_active(script_id: str, active: bool) -> Optional[Dict[str, Any]]:
    db = _load_scripts_db()
    item = db.get(script_id)
    if not item:
        return None
    item["active"] = bool(active)
    item["updated_at"] = _now()
    _save_scripts_db(db)
    return item


def get_active_scripts() -> List[Dict[str, Any]]:
    return [s for s in list_scripts() if s.get("active", False)]


# ---------------------------------------------------------------------------
# 템플릿
# ---------------------------------------------------------------------------
def get_templates() -> List[Dict[str, Any]]:
    return DEFAULT_TEMPLATES


def get_template_by_id(template_id: str) -> Optional[Dict[str, Any]]:
    for t in DEFAULT_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


# ---------------------------------------------------------------------------
# 코드 검증
# ---------------------------------------------------------------------------
def validate_code(code: str) -> Dict[str, Any]:
    if not code or not code.strip():
        return {"valid": False, "errors": ["코드가 비어 있습니다"]}
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {"valid": False, "errors": [f"구문 오류: {e}"]}

    errors: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            errors.append("import 문은 사용할 수 없습니다. np, pd, Signal, datetime 등은 기본 제공됩니다.")
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in ("open", "exec", "eval", "compile", "input"):
                errors.append(f"금지된 함수 호출: {func.id}")
            if isinstance(func, ast.Attribute) and func.attr in ("open", "read", "write"):
                errors.append(f"금지된 메서드 호출: {func.attr}")

    if errors:
        return {"valid": False, "errors": errors}

    # evaluate 함수 존재 여부 확인
    has_evaluate = any(
        isinstance(node, ast.FunctionDef) and node.name == "evaluate"
        for node in ast.walk(tree)
    )
    if not has_evaluate:
        return {"valid": False, "errors": ["evaluate(data, ctx) 함수가 필요합니다"]}

    return {"valid": True, "errors": []}


# ---------------------------------------------------------------------------
# 안전한 실행 환경
# ---------------------------------------------------------------------------
def _build_safe_globals() -> Dict[str, Any]:
    globals_dict = dict(_ALLOWED_MODULES)
    globals_dict.update({
        "np": np,
        "pd": pd,
        "datetime": datetime,
        "timedelta": timedelta,
        "Signal": Signal,
        "math": math,
    })
    return globals_dict


def run_script(code: str, data: pd.DataFrame, ctx: Dict[str, Any]) -> Tuple[List[Signal], Optional[str]]:
    """
    사용자 스크립트를 샌드박스에서 실행.
    반환: (signals, error)
    """
    validation = validate_code(code)
    if not validation["valid"]:
        return [], validation["errors"][0]

    local_ns: Dict[str, Any] = {}
    safe_globals = _build_safe_globals()
    try:
        exec(code, safe_globals, local_ns)
    except Exception as e:
        return [], f"스크립트 실행 오류: {e}"

    evaluate_fn = local_ns.get("evaluate")
    if not evaluate_fn:
        return [], "evaluate(data, ctx) 함수를 찾을 수 없습니다"

    try:
        result = evaluate_fn(data, ctx)
    except Exception as e:
        return [], f"evaluate() 실행 오류: {e}"

    signals: List[Signal] = []
    if isinstance(result, list):
        for r in result:
            if isinstance(r, Signal):
                if r.timestamp is None:
                    r.timestamp = _now()
                signals.append(r)
            elif isinstance(r, dict):
                try:
                    sig = Signal(**r)
                    if sig.timestamp is None:
                        sig.timestamp = _now()
                    signals.append(sig)
                except Exception:
                    pass
    return signals, None


# ---------------------------------------------------------------------------
# 시세 조회
# ---------------------------------------------------------------------------
def fetch_ohlcv(symbol: str, start: Optional[str] = None, end: Optional[str] = None,
                period: str = "D") -> Optional[pd.DataFrame]:
    """
    KIS API 일별 시세를 조회하여 pandas DataFrame 반환.
    columns: date, open, high, low, close, volume
    """
    try:
        from src import kis_api
        from datetime import datetime, timedelta

        if start is None or end is None:
            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=90)
            start = start or start_dt.strftime("%Y%m%d")
            end = end or end_dt.strftime("%Y%m%d")

        start_str = start.replace("-", "")
        end_str = end.replace("-", "")

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
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_DATE_1": start_str,
            "FID_INPUT_DATE_2": end_str,
            "FID_PERIOD_DIV_CODE": period,
            "FID_ORG_ADJ_PRC": "0",
        }
        res = req.get(
            f"{config.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers, params=params, timeout=10
        )
        res.raise_for_status()
        output = res.json().get("output2", [])
        rows = []
        for item in reversed(output):
            rows.append({
                "date": item["stck_bsop_date"],
                "open": int(item["stck_oprc"]),
                "high": int(item["stck_hgpr"]),
                "low": int(item["stck_lwpr"]),
                "close": int(item["stck_clpr"]),
                "volume": int(item["acml_vol"]),
            })
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        return df
    except Exception as e:
        print(f"fetch_ohlcv 오류 {symbol}: {e}")
        return None


# ---------------------------------------------------------------------------
# 백테스트
# ---------------------------------------------------------------------------
def backtest(code: str, symbol: str, ohlcv: Optional[pd.DataFrame] = None,
             start: Optional[str] = None, end: Optional[str] = None,
             initial_cash: float = 10_000_000) -> BacktestResult:
    """
    스크립트를 OHLCV 데이터로 백테스트.
    """
    if ohlcv is None:
        ohlcv = fetch_ohlcv(symbol, start, end)
        if ohlcv is None or ohlcv.empty:
            return BacktestResult(
                symbol=symbol, start=start or "", end=end or "",
                total_return_pct=0, win_rate=0, max_drawdown_pct=0,
                trade_count=0, signals=[], error="시세 데이터를 조회할 수 없습니다",
                initial_cash=initial_cash,
            )

    # 날짜 범위 필터
    df = ohlcv.copy()
    if start:
        start_dt = pd.to_datetime(start)
        df = df[df["date"] >= start_dt]
    if end:
        end_dt = pd.to_datetime(end)
        df = df[df["date"] <= end_dt]

    if df.empty:
        return BacktestResult(
            symbol=symbol, start=start or "", end=end or "",
            total_return_pct=0, win_rate=0, max_drawdown_pct=0,
            trade_count=0, signals=[], error="선택한 기간에 데이터가 없습니다",
            initial_cash=initial_cash,
        )

    df = df.reset_index(drop=True)

    cash = initial_cash
    position = 0
    trades: List[Dict[str, Any]] = []
    equity = [initial_cash]
    peak = initial_cash
    max_drawdown_pct = 0.0
    wins = 0
    losses = 0

    for i in range(1, len(df)):
        window = df.iloc[: i + 1]
        ctx = {
            "symbol": symbol,
            "cash": cash,
            "position": position,
            "initial_cash": initial_cash,
        }
        signals, err = run_script(code, window, ctx)
        if err:
            return BacktestResult(
                symbol=symbol,
                start=str(df["date"].iloc[0].date()),
                end=str(df["date"].iloc[-1].date()),
                total_return_pct=0, win_rate=0, max_drawdown_pct=0,
                trade_count=0, signals=[], error=err,
                initial_cash=initial_cash,
            )

        price = float(df["close"].iloc[i])
        date_str = str(df["date"].iloc[i].date())

        for sig in signals:
            if sig.action == "buy" and cash >= price * sig.quantity:
                cost = price * sig.quantity
                cash -= cost
                position += sig.quantity
                sig.timestamp = date_str
                trades.append(sig.to_dict())
            elif sig.action == "sell" and position >= sig.quantity:
                proceeds = price * sig.quantity
                # 단순 손익 추정 (마지막 매입가 기준)
                if position > 0:
                    avg_cost = (initial_cash - cash) / position if position > 0 else price
                else:
                    avg_cost = price
                pnl = (price - avg_cost) * sig.quantity
                if pnl > 0:
                    wins += 1
                elif pnl < 0:
                    losses += 1
                cash += proceeds
                position -= sig.quantity
                sig.timestamp = date_str
                trades.append(sig.to_dict())

        current_value = cash + position * price
        equity.append(current_value)
        if current_value > peak:
            peak = current_value
        dd = (peak - current_value) / peak * 100 if peak > 0 else 0
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

    final_value = equity[-1] if equity else initial_cash
    total_return_pct = round((final_value / initial_cash - 1) * 100, 2)
    trade_count = len(trades)
    win_rate = round(wins / (wins + losses) * 100, 2) if (wins + losses) > 0 else 0

    return BacktestResult(
        symbol=symbol,
        start=str(df["date"].iloc[0].date()),
        end=str(df["date"].iloc[-1].date()),
        total_return_pct=total_return_pct,
        win_rate=win_rate,
        max_drawdown_pct=round(max_drawdown_pct, 2),
        trade_count=trade_count,
        signals=trades,
        error="",
        initial_cash=initial_cash,
    )


# ---------------------------------------------------------------------------
# 활성 스크립트 평가
# ---------------------------------------------------------------------------
def evaluate_active_scripts() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for script in get_active_scripts():
        code = script.get("code", "")
        symbols = script.get("symbols", [])
        if not symbols:
            # symbols이 없으면 기본 종목 사용
            symbols = ["005930"]
        script_results: List[Dict[str, Any]] = []
        for symbol in symbols:
            df = fetch_ohlcv(symbol, period="D")
            if df is None or df.empty:
                script_results.append({
                    "symbol": symbol,
                    "signals": [],
                    "error": "시세 조회 실패",
                })
                continue
            try:
                price = int(df["close"].iloc[-1])
            except Exception:
                price = 0
            ctx = {
                "symbol": symbol,
                "cash": 10_000_000,
                "position": 0,
                "initial_cash": 10_000_000,
            }
            signals, err = run_script(code, df, ctx)
            for sig in signals:
                if sig.timestamp is None:
                    sig.timestamp = _now()
            script_results.append({
                "symbol": symbol,
                "signals": [s.to_dict() for s in signals],
                "error": err or "",
            })
        results.append({
            "script_id": script.get("id"),
            "name": script.get("name"),
            "results": script_results,
        })
    return results


# 모듈 로드 시 config 임포트
import config
