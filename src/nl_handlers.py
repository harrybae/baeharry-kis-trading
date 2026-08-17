# -*- coding: utf-8 -*-
"""
텔레그램 봇 - 자연어 주문 핸들러
검색/AI 대화 기능과 분리된 주문 전용 모듈입니다.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
from src import nlp_order

# ConversationHandler 상태
NL_INPUT, NL_CONFIRM_SAVE, NL_CONFIRM_EXECUTE, NL_ASK_MISSING = range(4)


def _format_order_summary(order: dict) -> str:
    action_text = "매수" if order.get("action") == "buy" else ("매도" if order.get("action") == "sell" else order.get("action", "-"))
    condition = order.get("condition_type", "now")
    condition_str = {
        "now": "즉시 실행",
        "drop_pct": f"{order.get('condition_value', 0)}% 하락 시",
        "rise_pct": f"{order.get('condition_value', 0)}% 상승 시",
        "target_price": f"{order.get('limit_price') or order.get('condition_value')}원 도달 시",
    }.get(condition, condition)
    order_type_str = "지정가" if order.get("order_type") == "limit" else "시장가"
    qty = order.get("quantity", 1)
    qty_str = "전량" if qty == -1 else f"{qty}주"
    return (
        f"📋 주문 내용 확인\n"
        f"━━━━━━━━━━━━━━\n"
        f"종목: {order.get('name', '-')} ({order.get('stock_code', '-')})\n"
        f"행동: {action_text}\n"
        f"수량: {qty_str}\n"
        f"주문 유형: {order_type_str}\n"
        f"조건: {condition_str}\n"
        f"원문: {order.get('raw_text', '-')}"
    )


async def nl_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """[주문] 접두사로 진입. 파싱 후 저장 확인 또는 재질문."""
    text = update.message.text or ""
    raw_text = text.replace("[주문]", "").strip()

    if not raw_text:
        await update.message.reply_text(
            "❌ 주문 내용이 비어 있습니다.\n"
            "예: [주문] 삼성전자 100주 사줘"
        )
        return ConversationHandler.END

    parse_result = nlp_order.parse_sentence(raw_text)

    # 필수 정보 누락/불명확 → 재질문
    if not parse_result.get("success"):
        error = parse_result.get("error", "알 수 없는 오류")
        partial_order = parse_result.get("order")
        if partial_order:
            context.user_data["pending_order_info"] = partial_order
            context.user_data["original_input"] = raw_text
            await update.message.reply_text(
                f"{error}\n\n"
                f"현재까지 인식한 내용:\n"
                f"{partial_order}\n\n"
                f"누락된 정보를 입력해 주세요. (취소: '그만')"
            )
            return NL_ASK_MISSING
        await update.message.reply_text(
            f"❌ 주문을 이해하지 못했습니다.\n이유: {error}\n\n"
            f"다시 입력해 주세요. 예시:\n"
            f"[주문] 삼성전자 100주 사줘\n"
            f"[주문] 현대차 5% 빠지면 10주 사줘\n"
            f"[주문] SK하이닉스 70000원에 50주 매도해줘"
        )
        return ConversationHandler.END

    order_info = parse_result["order"]
    context.user_data["pending_order_info"] = order_info

    order = nlp_order.create_order_from_parse(parse_result)
    if not order:
        await update.message.reply_text("❌ 주문 객체 생성에 실패했습니다. 다시 시도해 주세요.")
        return ConversationHandler.END

    # 전량 수량 실제 계산
    qty, err = nlp_order._resolve_quantity(order.action, order.stock_code, order.quantity, order.limit_price)
    if err:
        await update.message.reply_text(f"⚠️ 수량 계산 경고: {err}\n\n주문은 저장하지 않고 취소합니다.")
        return ConversationHandler.END
    order.quantity = qty
    # 저장/실행 전 예수금/보유 수량 검증 (모의투자 포함)
    feasible, check_result = nlp_order.execute_order(order, dry_run=True)
    if not feasible:
        await update.message.reply_text(
            f"❌ 주문을 저장할 수 없습니다.\n이유: {check_result.get('error', '알 수 없는 오류')}"
        )
        return ConversationHandler.END
    context.user_data["pending_order"] = order
    summary = _format_order_summary(order_info)
    await update.message.reply_text(
        f"{summary}\n\n이 주문을 저장하시겠습니까?\n(예/아니오/그만)"
    )
    return NL_CONFIRM_SAVE


async def nl_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """저장 여부 확인."""
    text = update.message.text.strip().lower()
    order = context.user_data.get("pending_order")

    if text in ("아니오", "no", "n", "취소", "그만"):
        await update.message.reply_text("주문 저장을 취소했습니다.")
        context.user_data.clear()
        return ConversationHandler.END

    if text not in ("예", "네", "yes", "y", "저장", "확인"):
        await update.message.reply_text("'예' 또는 '아니오'로 답해 주세요.")
        return NL_CONFIRM_SAVE

    if not order:
        await update.message.reply_text("❌ 저장할 주문 정보가 없습니다. 다시 시도해 주세요.")
        context.user_data.clear()
        return ConversationHandler.END

    # 저장 직전 다시 한번 예수금/보유 수량 검증 (모의투자 포함)
    feasible, check_result = nlp_order.execute_order(order, dry_run=True)
    if not feasible:
        await update.message.reply_text(
            f"❌ 주문을 저장할 수 없습니다.\n이유: {check_result.get('error', '알 수 없는 오류')}"
        )
        context.user_data.clear()
        return ConversationHandler.END

    nlp_order.save_order(order)
    await update.message.reply_text("✅ 주문이 저장되었습니다.")

    if order.condition_type == "now":
        await update.message.reply_text(
            "⚡ 즉시 실행 주문입니다.\n지금 바로 실행하시겠습니까?\n(예/아니오/그만)"
        )
        context.user_data["order_id"] = order.id
        return NL_CONFIRM_EXECUTE

    context.user_data.clear()
    return ConversationHandler.END


async def nl_confirm_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """즉시 실행 여부 확인."""
    text = update.message.text.strip().lower()
    order_id = context.user_data.get("order_id")

    if text in ("아니오", "no", "n", "취소", "그만"):
        await update.message.reply_text("즉시 실행을 취소했습니다. 주문은 저장되어 있습니다.")
        context.user_data.clear()
        return ConversationHandler.END

    if text not in ("예", "네", "yes", "y", "실행", "확인"):
        await update.message.reply_text("'예' 또는 '아니오'로 답해 주세요.")
        return NL_CONFIRM_EXECUTE

    order = nlp_order.get_order(order_id) if order_id else None
    if not order:
        await update.message.reply_text("❌ 주문을 찾을 수 없습니다.")
        context.user_data.clear()
        return ConversationHandler.END

    dry_run = config.IS_PAPER_TRADING
    ok, result = nlp_order.execute_order(order, dry_run=dry_run)
    if ok:
        if result.get("dry_run"):
            title = "✅ 모의투자 주문 요청 완료"
        else:
            title = "✅ 실전투자 주문 실행 완료"
        price = result.get('price', '시장가')
        order_no = result.get('order_no', '-')
        exec_time = result.get('requested_at', result.get('time', '-'))
        await update.message.reply_text(
            f"{title}\n"
            f"━━━━━━━━━━━━━━\n"
            f"종목: {order.name} ({order.stock_code})\n"
            f"행동: {'매수' if order.action == 'buy' else '매도'}\n"
            f"수량: {result.get('quantity', order.quantity)}주\n"
            f"가격: {price}\n"
            f"주문번호: {order_no}\n"
            f"시간: {exec_time}"
        )
    else:
        await update.message.reply_text(f"❌ 주문 실행 실패: {result.get('error', '알 수 없는 오류')}")

    context.user_data.clear()
    return ConversationHandler.END


async def nl_ask_missing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """누락 정보 재입력 처리."""
    text = update.message.text or ""
    if text.strip().lower() in ("그만", "취소", "exit", "quit"):
        await update.message.reply_text("주문 입력을 취소했습니다.")
        context.user_data.clear()
        return ConversationHandler.END

    # 기존 입력과 결합해 재파싱
    original = context.user_data.get("original_input", "")
    combined = f"{original} {text}".strip()
    parse_result = nlp_order.parse_sentence(combined)

    if not parse_result.get("success"):
        error = parse_result.get("error", "알 수 없는 오류")
        partial_order = parse_result.get("order")
        if partial_order:
            context.user_data["original_input"] = combined
            context.user_data["pending_order_info"] = partial_order
            await update.message.reply_text(
                f"{error}\n\n"
                f"아직 추가 정보가 필요합니다.\n"
                f"누락된 정보를 입력해 주세요. (취소: '그만')"
            )
            return NL_ASK_MISSING
        await update.message.reply_text(
            f"❌ 주문을 이해하지 못했습니다.\n이유: {error}\n\n"
            f"다시 처음부터 [주문]으로 입력해 주세요."
        )
        context.user_data.clear()
        return ConversationHandler.END

    # 이후 흐름을 entry와 동일하게 진행
    order_info = parse_result["order"]
    context.user_data["pending_order_info"] = order_info

    order = nlp_order.create_order_from_parse(parse_result)
    if not order:
        await update.message.reply_text("❌ 주문 객체 생성에 실패했습니다. 다시 시도해 주세요.")
        context.user_data.clear()
        return ConversationHandler.END

    qty, err = nlp_order._resolve_quantity(order.action, order.stock_code, order.quantity, order.limit_price)
    if err:
        await update.message.reply_text(f"⚠️ 수량 계산 경고: {err}\n\n주문은 저장하지 않고 취소합니다.")
        context.user_data.clear()
        return ConversationHandler.END
    order.quantity = qty

    # 저장/실행 전 예수금/보유 수량 검증 (모의투자 포함)
    feasible, check_result = nlp_order.execute_order(order, dry_run=True)
    if not feasible:
        await update.message.reply_text(
            f"❌ 주문을 저장할 수 없습니다.\n이유: {check_result.get('error', '알 수 없는 오류')}"
        )
        context.user_data.clear()
        return ConversationHandler.END

    context.user_data["pending_order"] = order
    summary = _format_order_summary(order_info)
    await update.message.reply_text(
        f"{summary}\n\n이 주문을 저장하시겠습니까?\n(예/아니오/그만)"
    )
    return NL_CONFIRM_SAVE


async def nl_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """대화 중 '그만' 명령으로 취소."""
    await update.message.reply_text("주문 입력을 취소했습니다.")
    context.user_data.clear()
    return ConversationHandler.END


def create_nl_conv_handler() -> ConversationHandler:
    """자연어 주문 ConversationHandler를 생성합니다."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^\[주문\]") & filters.TEXT, nl_entry),
        ],
        states={
            NL_CONFIRM_SAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, nl_confirm_save)],
            NL_CONFIRM_EXECUTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, nl_confirm_execute)],
            NL_ASK_MISSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, nl_ask_missing)],
        },
        fallbacks=[
            CommandHandler("cancel", nl_cancel),
            MessageHandler(filters.Regex(r"^\[주문\]"), nl_entry),
        ],
    )
