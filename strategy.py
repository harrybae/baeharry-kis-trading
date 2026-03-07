import config


def calc_ma(prices, period):
    """이동평균 계산"""
    if len(prices) < period:
        return None
    return sum(prices[:period]) / period


def get_signal(prices):
    """
    골든크로스 / 데드크로스 신호 반환
    prices: 최신순 정렬된 종가 리스트

    반환값:
        "BUY"  - 골든크로스 (단기 MA가 장기 MA를 상향 돌파)
        "SELL" - 데드크로스 (단기 MA가 장기 MA를 하향 돌파)
        None   - 신호 없음
    """
    short = config.SHORT_MA
    long_ = config.LONG_MA

    # 현재 MA
    ma_short_now = calc_ma(prices, short)
    ma_long_now = calc_ma(prices, long_)

    # 1봉 전 MA (prices[1:] 로 한 칸 이전 데이터)
    ma_short_prev = calc_ma(prices[1:], short)
    ma_long_prev = calc_ma(prices[1:], long_)

    if None in (ma_short_now, ma_long_now, ma_short_prev, ma_long_prev):
        return None

    # 골든크로스: 이전엔 단기 < 장기, 현재는 단기 > 장기
    if ma_short_prev <= ma_long_prev and ma_short_now > ma_long_now:
        return "BUY"

    # 데드크로스: 이전엔 단기 > 장기, 현재는 단기 < 장기
    if ma_short_prev >= ma_long_prev and ma_short_now < ma_long_now:
        return "SELL"

    return None


def check_stop_loss(buy_price, current_price):
    """손절 조건 확인"""
    if buy_price <= 0:
        return False
    loss_rate = (current_price - buy_price) / buy_price
    return loss_rate <= -config.RISK_TOLERANCE


def check_take_profit(buy_price, current_price):
    """익절 조건 확인"""
    if buy_price <= 0:
        return False
    profit_rate = (current_price - buy_price) / buy_price
    return profit_rate >= config.TAKE_PROFIT


def get_ma_status(prices):
    """현재 MA 상태 문자열 반환 (메뉴바 표시용)"""
    ma_short = calc_ma(prices, config.SHORT_MA)
    ma_long = calc_ma(prices, config.LONG_MA)
    if ma_short is None or ma_long is None:
        return "데이터 부족"
    trend = "▲ 상승" if ma_short > ma_long else "▼ 하락"
    return f"MA{config.SHORT_MA}:{ma_short:,.0f} MA{config.LONG_MA}:{ma_long:,.0f} {trend}"
