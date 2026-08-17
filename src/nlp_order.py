# -*- coding: utf-8 -*-
"""
자연어(한글) 주문 파서
- 한글 문장을 구조화된 주문 조건으로 변환
- 조건 충족 시 kis_api.place_order()로 실행
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from src import kis_api, stock_master

# ---------------------------------------------------------------------------
# 상수 / 설정
# ---------------------------------------------------------------------------
NL_ORDER_DIR = Path(__file__).resolve().parent.parent / "data" / "nl_orders"
NL_ORDER_DIR.mkdir(parents=True, exist_ok=True)
NL_ORDER_DB = NL_ORDER_DIR / "nl_orders.json"

REF_PRICE_DIR = Path(__file__).resolve().parent.parent / "data" / "nl_orders"
REF_PRICE_FILE = NL_ORDER_DIR / "ref_prices.json"

MAX_SAVED_ORDERS = 200

_ORDER_TYPE_KEYWORDS = {
    "buy": ["사", "사줘", "매수", "담아", "담", "들어가", "추가매수", "삼", "구매"],
    "sell": ["팔", "팔아", "매도", "처분", "정리", "빼", "탈출"],
}

_QUANTITY_KEYWORDS = {
    "all": ["전량", "전부", "올인", "모두", "다", "풀매수", "풀매도"],
}

_CONDITION_KEYWORDS = {
    "drop_pct": ["빠지", "하락", "밀리", "떨어지", "내려가"],
    "rise_pct": ["오르", "상승", "올라", "뛰", "강해지"],
    "target_price": ["가격", "원에", "원이"],
    "now": ["지금", "바로", "즉시", "현재", "바로바로"],
}

_PRICE_TYPE_KEYWORDS = {
    "market": ["시장가", "바로", "즉시", "현재가"],
    "limit": ["지정가", "원에", "원이"],
}


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------
@dataclass
class NLOrder:
    id: str
    raw_text: str
    name: str
    stock_code: str
    action: str  # "buy" | "sell"
    quantity: int
    order_type: str  # "market" | "limit"
    limit_price: Optional[int]
    condition_type: str  # "now" | "drop_pct" | "rise_pct" | "target_price"
    condition_value: Optional[float]
    active: bool
    created_at: str
    updated_at: str
    executed_at: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "raw_text": self.raw_text,
            "name": self.name,
            "stock_code": self.stock_code,
            "action": self.action,
            "quantity": self.quantity,
            "order_type": self.order_type,
            "limit_price": self.limit_price,
            "condition_type": self.condition_type,
            "condition_value": self.condition_value,
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "executed_at": self.executed_at,
            "execution_result": self.execution_result,
            "error": self.error,
        }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------
def _load_db() -> Dict[str, Dict[str, Any]]:
    if not NL_ORDER_DB.exists():
        return {}
    try:
        with NL_ORDER_DB.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_db(data: Dict[str, Dict[str, Any]]) -> None:
    with NL_ORDER_DB.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_ref_prices() -> Dict[str, Any]:
    if not REF_PRICE_FILE.exists():
        return {}
    try:
        with REF_PRICE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_ref_prices(data: Dict[str, Any]) -> None:
    with REF_PRICE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------
def _extract_number(text: str) -> Optional[int]:
    """문장에서 첫 번째 정수 추출 (예: 100주, 5% 등)"""
    # 한국식 금액/수량 단위 처리
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)(?:\s*(주|원|%, percent|퍼센트))?", text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    try:
        return int(raw)
    except ValueError:
        return None


def _extract_percent(text: str) -> Optional[float]:
    """백분율 표현 추출 (5%, 5 퍼센트, 5프로 등)"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|퍼센트|프로|pct)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _extract_price(text: str) -> Optional[int]:
    """지정가 단가 추출 (예: 70000원, 7만원)"""
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)\s*만?\s*원", text)
    if not m:
        return None
    raw = m.group(1).replace(",", "")
    if "만원" in text or re.search(r"\d+\s*만\s*원", text):
        try:
            return int(float(raw) * 10000)
        except ValueError:
            return None
    try:
        return int(raw)
    except ValueError:
        return None


def _find_order_action(text: str) -> Optional[str]:
    lowered = text.lower()
    # 매도 키워드를 먼저 체크: "사"가 "팔아"에 포함되어 매수로 잘못 인식되는 경우 방지
    for action in ("sell", "buy"):
        for kw in _ORDER_TYPE_KEYWORDS[action]:
            if kw in lowered:
                return action
    # 문맥 추론: "~면 사줘" 패턴에서 "사줘"가 없으면 마지막 동사로 판단
    if "팔" in text or "매도" in text or "처분" in text:
        return "sell"
    if "사" in text or "매수" in text or "담" in text:
        return "buy"
    return None


def _find_condition_type(text: str) -> str:
    lowered = text.lower()
    # 즉시 실행 키워드
    for kw in _CONDITION_KEYWORDS["now"]:
        if kw in lowered:
            return "now"
    # 가격/지정가 키워드
    if re.search(r"\d+\s*만?\s*원", text):
        return "target_price"
    # 등락률 키워드
    for kw in _CONDITION_KEYWORDS["drop_pct"]:
        if kw in lowered:
            return "drop_pct"
    for kw in _CONDITION_KEYWORDS["rise_pct"]:
        if kw in lowered:
            return "rise_pct"
    return "now"


def _find_order_type(text: str, condition_type: str, limit_price: Optional[int]) -> str:
    lowered = text.lower()
    if limit_price is not None:
        return "limit"
    for kw in _PRICE_TYPE_KEYWORDS["market"]:
        if kw in lowered:
            return "market"
    return "market"


def _resolve_stock(name_keyword: str) -> Optional[Tuple[str, str]]:
    """종목명 키워드 → (code, full_name)"""
    if not name_keyword:
        return None
    results = stock_master.search_stocks(name_keyword, limit=5)
    if not results:
        return None
    # 정확히 일치하는 이름 우선
    for r in results:
        if r["name"] == name_keyword or r["name"] in name_keyword or name_keyword in r["name"]:
            return r["code"], r["name"]
    return results[0]["code"], results[0]["name"]


def _extract_stock_name(text: str) -> Optional[str]:
    """문장에서 종목명 추출 (heuristic: 조사/격조사 앞 명사)"""
    # 패턴 1: "A가", "A이", "A는", "A을", "A를", "A에", "A가 " 등
    m = re.match(r"^([^가-힣]*)([가-힣]{2,})(?:가|이|는|은|을|를|에|의|와|과|도|만|에서|으로|로|하고|한테|께)(.*)$", text)
    if m:
        return m.group(2)
    # 패턴 2: "A 5%" — 종목명 뒤에 숫자/퍼센트
    m = re.match(r"^([^가-힣]*)([가-힣]{2,})\s+\d", text)
    if m:
        return m.group(2)
    # 패턴 3: 문장 시작의 2~6자 한글 단어
    m = re.match(r"^([^가-힣]*)([가-힣]{2,6})(?:\s|$|[^가-힣])", text)
    if m:
        return m.group(2)
    return None


def _extract_quantity(text: str, action: Optional[str]) -> int:
    """수량 추출: 숫자+주 > 전량/올인 > 기본 1주"""
    for kw in _QUANTITY_KEYWORDS["all"]:
        if kw in text:
            return -1  # 전량은 별도 계산
    # "N주" 패턴
    m = re.search(r"(\d{1,3}(?:,\d{3})+|\d+)\s*주", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            pass
    # 퍼센트/가격 없는 일반 숫자는 수량으로 간주 (단, 백분율이나 가격 패턴이면 제외)
    if not re.search(r"\d+\s*(%|퍼센트|프로|원)", text):
        m = re.search(r"\b(\d+)\b", text)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return 1


def parse_sentence(raw_text: str) -> Dict[str, Any]:
    """
    한글 문장을 파싱하여 구조화된 결과 반환.
    반환: {success, order?, error}
    """
    text = raw_text.strip()
    if not text:
        return {"success": False, "error": "문장을 입력하세요"}

    action = _find_order_action(text)
    if not action:
        return {"success": False, "error": "매수/매도 행동을 인식하지 못했습니다. 예: '삼성전자 100주 사줘'"}

    stock_name = _extract_stock_name(text)
    if not stock_name:
        return {"success": False, "error": "종목명을 인식하지 못했습니다. 예: '삼성전자 100주 사줘'"}

    resolved = _resolve_stock(stock_name)
    if not resolved:
        return {"success": False, "error": f"'{stock_name}' 종목을 찾을 수 없습니다"}
    stock_code, full_name = resolved

    condition_type = _find_condition_type(text)
    condition_value: Optional[float] = None
    limit_price = _extract_price(text)

    if condition_type in ("drop_pct", "rise_pct"):
        condition_value = _extract_percent(text)
        if condition_value is None:
            condition_value = 0.0  # "빠지면"만 있을 때는 0% 변화로 즉시 인식
            condition_type = "now"
    elif condition_type == "target_price":
        condition_value = float(limit_price) if limit_price else None

    order_type = _find_order_type(text, condition_type, limit_price)
    quantity = _extract_quantity(text, action)

    return {
        "success": True,
        "order": {
            "raw_text": text,
            "name": full_name,
            "stock_code": stock_code,
            "action": action,
            "quantity": quantity,
            "order_type": order_type,
            "limit_price": limit_price,
            "condition_type": condition_type,
            "condition_value": condition_value,
        },
    }


# ---------------------------------------------------------------------------
# 수량 계산
# ---------------------------------------------------------------------------
def _resolve_quantity(action: str, stock_code: str, quantity: int, limit_price: Optional[int]) -> Tuple[int, str]:
    """전량(-1)이면 보유/예수금 기준 계산. (실제 수량, 에러 메시지) 반환"""
    if quantity != -1:
        return quantity, ""
    try:
        if action == "buy":
            holdings, deposit, _ = kis_api.get_balance()
            price = limit_price if limit_price else kis_api.get_current_price(stock_code)
            if price <= 0:
                return 0, "현재가 조회 실패로 수량 계산 불가"
            qty = max(1, deposit // price)
            return qty, ""
        else:
            holdings, deposit, _ = kis_api.get_balance()
            held = holdings.get(stock_code, 0)
            if held <= 0:
                return 0, "보유 수량이 없습니다"
            return held, ""
    except Exception as e:
        return 0, f"수량 계산 오류: {e}"


# ---------------------------------------------------------------------------
# 조건 평가
# ---------------------------------------------------------------------------
def _set_ref_price(stock_code: str, price: int) -> None:
    data = _load_ref_prices()
    data[stock_code] = {"price": price, "updated_at": _now()}
    _save_ref_prices(data)


def _get_ref_price(stock_code: str) -> Optional[int]:
    data = _load_ref_prices()
    entry = data.get(stock_code)
    if not entry:
        return None
    return entry.get("price")


def evaluate_condition(order: NLOrder) -> Tuple[bool, str]:
    """
    주문의 조건을 평가. (조건 충족 여부, 메시지) 반환.
    """
    if order.condition_type == "now":
        return True, "즉시 실행"

    try:
        current = kis_api.get_current_price(order.stock_code)
    except Exception as e:
        return False, f"현재가 조회 실패: {e}"

    if order.condition_type == "target_price":
        target = int(order.condition_value or 0)
        if target <= 0:
            return False, "지정가가 설정되지 않았습니다"
        if order.action == "buy":
            return current <= target, f"현재가 {current:,}원 / 지정 매수가 {target:,}원"
        else:
            return current >= target, f"현재가 {current:,}원 / 지정 매도가 {target:,}원"

    ref = _get_ref_price(order.stock_code)
    if ref is None:
        _set_ref_price(order.stock_code, current)
        ref = current

    pct = (current - ref) / ref * 100 if ref else 0.0
    threshold = order.condition_value or 0.0

    if order.condition_type == "drop_pct":
        return pct <= -threshold, f"기준가 {ref:,}원 → 현재가 {current:,}원 ({pct:.2f}%) / 하락 조건 -{threshold}%"
    if order.condition_type == "rise_pct":
        return pct >= threshold, f"기준가 {ref:,}원 → 현재가 {current:,}원 ({pct:.2f}%) / 상승 조건 +{threshold}%"

    return False, "알 수 없는 조건"


# ---------------------------------------------------------------------------
# 실행
# ---------------------------------------------------------------------------
def execute_order(order: NLOrder, dry_run: bool = False) -> Tuple[bool, Dict[str, Any]]:
    """주문을 실행하고 결과를 order 객체에 기록."""
    qty, err = _resolve_quantity(order.action, order.stock_code, order.quantity, order.limit_price)
    if err:
        order.error = err
        return False, {"error": err}

    order_type_str = "BUY" if order.action == "buy" else "SELL"
    price = order.limit_price if order.order_type == "limit" else 0

    # 매수 시 잔액 확인, 매도 시 보유 수량 확인
    try:
        holdings, deposit, _ = kis_api.get_balance()
    except Exception as e:
        err = f"계좌 조회 실패: {e}"
        order.error = err
        return False, {"error": err}

    if order.action == "buy":
        buy_price = price if price > 0 else kis_api.get_current_price(order.stock_code)
        if buy_price <= 0:
            err = "매수 가격을 결정할 수 없습니다"
            order.error = err
            return False, {"error": err}
        required_amount = qty * buy_price
        if required_amount > deposit:
            err = f"예수금 부족: 필요 {required_amount:,}원 / 보유 {deposit:,}원"
            order.error = err
            return False, {"error": err}
    else:  # sell
        held = holdings.get(order.stock_code, 0)
        if qty > held:
            err = f"보유 수량 부족: 매도 {qty}주 / 보유 {held}주"
            order.error = err
            return False, {"error": err}

    if dry_run:
        result = {
            "dry_run": True,
            "stock_code": order.stock_code,
            "name": order.name,
            "action": order_type_str,
            "quantity": qty,
            "price": price or "시장가",
            "limit_price": order.limit_price,
            "requested_at": _now(),
        }
        return True, result

    try:
        success, order_no = kis_api.place_order(order.stock_code, order_type_str, qty, price=price)
        result = {
            "success": success,
            "stock_code": order.stock_code,
            "name": order.name,
            "action": order_type_str,
            "quantity": qty,
            "price": price or "시장가",
            "order_no": order_no,
        }
        if success:
            order.executed_at = _now()
            order.execution_result = result
            order.active = False
            order.error = ""
        else:
            order.error = result.get("msg1", "주문 실패")
        return success, result
    except Exception as e:
        order.error = str(e)
        return False, {"error": str(e)}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create_order_from_parse(parse_result: Dict[str, Any]) -> Optional[NLOrder]:
    info = parse_result.get("order")
    if not info:
        return None
    order_id = str(uuid.uuid4())[:8]
    now = _now()
    return NLOrder(
        id=order_id,
        raw_text=info["raw_text"],
        name=info["name"],
        stock_code=info["stock_code"],
        action=info["action"],
        quantity=info["quantity"],
        order_type=info["order_type"],
        limit_price=info["limit_price"],
        condition_type=info["condition_type"],
        condition_value=info["condition_value"],
        active=info["condition_type"] != "now",
        created_at=now,
        updated_at=now,
    )


def save_order(order: NLOrder) -> None:
    db = _load_db()
    db[order.id] = order.to_dict()
    _save_db(db)


def list_orders() -> List[Dict[str, Any]]:
    db = _load_db()
    items = sorted(db.values(), key=lambda x: x.get("created_at", ""), reverse=True)
    return items


def get_order(order_id: str) -> Optional[NLOrder]:
    db = _load_db()
    data = db.get(order_id)
    if not data:
        return None
    return NLOrder(**data)


def update_order(order_id: str, **kwargs) -> Optional[NLOrder]:
    order = get_order(order_id)
    if not order:
        return None
    for k, v in kwargs.items():
        if hasattr(order, k):
            setattr(order, k, v)
    order.updated_at = _now()
    save_order(order)
    return order


def delete_order(order_id: str) -> bool:
    db = _load_db()
    if order_id not in db:
        return False
    del db[order_id]
    _save_db(db)
    return True


def run_order_now(order_id: str, dry_run: bool = False) -> Tuple[bool, Dict[str, Any]]:
    order = get_order(order_id)
    if not order:
        return False, {"error": "주문을 찾을 수 없습니다"}
    success, result = execute_order(order, dry_run=dry_run)
    save_order(order)
    return success, result


# ---------------------------------------------------------------------------
# 모니터링용 주기 평가
# ---------------------------------------------------------------------------
def evaluate_all_active(dry_run: bool = False) -> List[Dict[str, Any]]:
    """활성 주문 중 조건 충족된 것을 실행."""
    results: List[Dict[str, Any]] = []
    for data in list_orders():
        if not data.get("active"):
            continue
        order = NLOrder(**data)
        ok, msg = evaluate_condition(order)
        if ok:
            success, exec_result = execute_order(order, dry_run=dry_run)
            if success:
                save_order(order)
            results.append({
                "id": order.id,
                "name": order.name,
                "action": order.action,
                "triggered": True,
                "success": success,
                "result": exec_result,
                "message": msg,
            })
        else:
            results.append({
                "id": order.id,
                "name": order.name,
                "action": order.action,
                "triggered": False,
                "message": msg,
            })
    return results
