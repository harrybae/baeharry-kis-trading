"""
스크립트 매매 API 블루프린트

Endpoints:
  GET    /api/scripts              - 전체 스크립트 목록
  POST   /api/scripts              - 새 스크립트 저장
  GET    /api/scripts/<id>         - 특정 스크립트 조회
  PUT    /api/scripts/<id>         - 특정 스크립트 수정
  DELETE /api/scripts/<id>         - 특정 스크립트 삭제
  POST   /api/scripts/<id>/active - 활성/비활성 전환
  POST   /api/scripts/<id>/backtest - 백테스트 실행
  GET    /api/script-templates     - 템플릿 목록
  GET    /api/script-templates/<id> - 템플릿 상세
  POST   /api/script-evaluate      - 활성 스크립트 평가 실행
  GET    /api/script-active-summary - 활성 스크립트 요약
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

import src.script_engine as script_engine

bp = Blueprint("script", __name__, url_prefix="")


def _json_error(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


@bp.route("/api/scripts", methods=["GET"])
def list_scripts():
    """전체 스크립트 목록 반환"""
    scripts = script_engine.list_scripts()
    return jsonify({"success": True, "scripts": scripts})


@bp.route("/api/scripts", methods=["POST"])
def create_script():
    """새 스크립트 저장"""
    data = request.get_json(silent=True) or {}
    saved = script_engine.save_script(
        name=data.get("name", "Untitled"),
        code=data.get("code", ""),
        symbols=data.get("symbols") or data.get("symbol") and [data.get("symbol")] or [],
        active=data.get("active", False),
    )
    if not saved:
        return _json_error("스크립트 저장에 실패했습니다.")
    return jsonify({"success": True, "script": saved})


@bp.route("/api/scripts/<script_id>", methods=["GET"])
def get_script(script_id: str):
    """특정 스크립트 조회"""
    script = script_engine.get_script(script_id)
    if script is None:
        return _json_error("스크립트를 찾을 수 없습니다.", 404)
    return jsonify({"success": True, "script": script})


@bp.route("/api/scripts/<script_id>", methods=["PUT"])
def update_script(script_id: str):
    """특정 스크립트 수정"""
    data = request.get_json(silent=True) or {}
    saved = script_engine.save_script(
        name=data.get("name", "Untitled"),
        code=data.get("code", ""),
        script_id=script_id,
        symbols=data.get("symbols") or data.get("symbol") and [data.get("symbol")] or [],
        active=data.get("active", False),
    )
    if not saved:
        return _json_error("스크립트 수정에 실패했습니다.")
    return jsonify({"success": True, "script": saved})


@bp.route("/api/scripts/<script_id>", methods=["DELETE"])
def delete_script(script_id: str):
    """특정 스크립트 삭제"""
    if script_engine.delete_script(script_id):
        return jsonify({"success": True})
    return _json_error("스크립트 삭제에 실패했습니다.", 404)


@bp.route("/api/scripts/<script_id>/active", methods=["POST"])
def set_active(script_id: str):
    """활성/비활성 전환"""
    data = request.get_json(silent=True) or {}
    active = data.get("active", True)
    if script_engine.set_script_active(script_id, active):
        return jsonify({"success": True, "id": script_id, "active": active})
    return _json_error("활성 상태 변경에 실패했습니다.", 404)


@bp.route("/api/scripts/<script_id>/backtest", methods=["POST"])
def run_backtest(script_id: str):
    """특정 스크립트 백테스트 실행"""
    script = script_engine.get_script(script_id)
    if script is None:
        return _json_error("스크립트를 찾을 수 없습니다.", 404)

    data = request.get_json(silent=True) or {}
    symbol = data.get("symbol") or script.get("symbols", ["005930"])[0]
    start = data.get("start") or None
    end = data.get("end") or None
    initial_cash = float(data.get("initial_cash", 10_000_000))

    result = script_engine.backtest(
        code=script.get("code", ""),
        symbol=symbol,
        start=start,
        end=end,
        initial_cash=initial_cash,
    )

    response = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    response["success"] = not bool(response.get("error"))
    return jsonify(response)


@bp.route("/api/script-templates", methods=["GET"])
def list_templates():
    """템플릿 목록 반환"""
    templates = script_engine.get_templates()
    return jsonify({"success": True, "templates": templates})


@bp.route("/api/script-templates/<template_id>", methods=["GET"])
def get_template(template_id: str):
    """템플릿 상세 반환"""
    template = script_engine.get_template_by_id(template_id)
    if template is None:
        return _json_error("템플릿을 찾을 수 없습니다.", 404)
    return jsonify({"success": True, "template": template})


@bp.route("/api/script-evaluate", methods=["POST"])
def evaluate_scripts():
    """활성 스크립트 평가 실행"""
    results = script_engine.evaluate_active_scripts()
    return jsonify({"success": True, "results": results})


@bp.route("/api/script-active-summary", methods=["GET"])
def active_summary():
    """활성 스크립트 요약 반환"""
    active = script_engine.get_active_scripts()
    return jsonify({"success": True, "active_scripts": active, "count": len(active)})
