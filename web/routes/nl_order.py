# -*- coding: utf-8 -*-
"""
자연어(한글) 주문 API 블루프린트

Endpoints:
  POST /api/nl-order/parse         - 한글 문장 파싱
  GET  /api/nl-order               - 저장된 주문 목록
  POST /api/nl-order               - 주문 저장 (execute=true면 즉시 실행)
  POST /api/nl-order/<id>/execute  - 주문 즉시 실행
  PUT  /api/nl-order/<id>          - active 상태 변경
  DELETE /api/nl-order/<id>        - 주문 삭제
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import src.nlp_order as nlp_order

bp = Blueprint("nl_order", __name__, url_prefix="/api/nl-order")


def _json_error(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


@bp.route("/parse", methods=["POST"])
def parse_nl_order():
    """한글 주문 문장을 파싱하여 구조 정보 반환"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return _json_error("주문 문장을 입력하세요")

    result = nlp_order.parse_sentence(text)
    return jsonify(result)


@bp.route("", methods=["GET"])
def list_nl_orders():
    """저장된 자연어 주문 목록 반환"""
    orders = nlp_order.list_orders()
    return jsonify({"success": True, "orders": orders})


@bp.route("", methods=["POST"])
def create_nl_order():
    """자연어 주문 저장. execute=true이면 즉시 실행까지 수행"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    execute = bool(data.get("execute", False))
    dry_run = bool(data.get("dry_run", False))

    if not text:
        return _json_error("주문 문장을 입력하세요")

    parse_result = nlp_order.parse_sentence(text)
    if not parse_result.get("success"):
        return _json_error(parse_result.get("error", "해석 실패"))

    order = nlp_order.create_order_from_parse(parse_result)
    if not order:
        return _json_error("주문 객체 생성 실패")

    qty, err = nlp_order._resolve_quantity(order.action, order.stock_code, order.quantity, order.limit_price)
    if err:
        return _json_error(f"수량 계산 오류: {err}")
    order.quantity = qty

    if execute:
        # 즉시 실행 주문은 저장 후 active=false로 처리
        order.active = False
        nlp_order.save_order(order)
        success, exec_result = nlp_order.execute_order(order, dry_run=dry_run)
        nlp_order.save_order(order)
        return jsonify({
            "success": success,
            "order": order.to_dict(),
            "execution": exec_result,
            "message": "주문을 실행했습니다" if success else (exec_result.get("error") or "주문 실행 실패"),
        })

    # 저장 직전 예수금/보유 수량 검증 (모의투자 포함)
    feasible, check_result = nlp_order.execute_order(order, dry_run=True)
    if not feasible:
        return _json_error(check_result.get("error", "주문을 저장할 수 없습니다"))

    # 저장만 하는 경우: 즉시 실행이 아니면 활성화
    if order.condition_type == "now":
        order.active = False
    else:
        order.active = True
    nlp_order.save_order(order)
    return jsonify({"success": True, "order": order.to_dict()})


@bp.route("/<order_id>/execute", methods=["POST"])
def execute_nl_order(order_id: str):
    """주문 즉시 실행"""
    data = request.get_json(silent=True) or {}
    dry_run = bool(data.get("dry_run", False))
    success, exec_result = nlp_order.run_order_now(order_id, dry_run=dry_run)
    order = nlp_order.get_order(order_id)
    return jsonify({
        "success": success,
        "execution": exec_result,
        "order": order.to_dict() if order else None,
        "message": "주문 실행 완료" if success else (exec_result.get("error") or "주문 실행 실패"),
    })


@bp.route("/<order_id>", methods=["PUT"])
def update_nl_order(order_id: str):
    """주문 상태(active) 변경"""
    data = request.get_json(silent=True) or {}
    active = data.get("active")
    if active is None:
        return _json_error("active 값을 입력하세요")

    order = nlp_order.update_order(order_id, active=bool(active))
    if not order:
        return _json_error("주문을 찾을 수 없습니다", 404)
    return jsonify({"success": True, "order": order.to_dict()})


@bp.route("/<order_id>", methods=["DELETE"])
def delete_nl_order(order_id: str):
    """주문 삭제"""
    if nlp_order.delete_order(order_id):
        return jsonify({"success": True})
    return _json_error("주문을 찾을 수 없습니다", 404)
