# position_manager.py - 포지션 및 손절/익절 관리

import json
import os
from datetime import datetime
import config

POSITIONS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'positions.json')

class PositionManager:
    """포지션 관리 클래스"""
    
    def __init__(self):
        self.positions = self.load_positions()
    
    def load_positions(self):
        """저장된 포지션 로드"""
        try:
            if os.path.exists(POSITIONS_FILE):
                with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"포지션 로드 오류: {e}")
        return {"positions": []}
    
    def save_positions(self):
        """포지션 저장"""
        try:
            with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.positions, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"포지션 저장 오류: {e}")
    
    def open_position(self, stock_code, stock_name, entry_price, quantity):
        """포지션 열기 (매수)"""
        position = {
            "id": f"{stock_code}_{datetime.now().timestamp()}",
            "code": stock_code,
            "name": stock_name,
            "entry_price": entry_price,
            "quantity": quantity,
            "entry_time": datetime.now().isoformat(),
            "status": "open",
            "pnl": 0,
            "pnl_pct": 0,
        }
        self.positions["positions"].append(position)
        self.save_positions()
        return position
    
    def close_position(self, stock_code, exit_price):
        """포지션 닫기 (매도)"""
        for pos in self.positions["positions"]:
            if pos["code"] == stock_code and pos["status"] == "open":
                pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
                pnl_pct = ((exit_price - pos["entry_price"]) / pos["entry_price"]) * 100
                
                pos["exit_price"] = exit_price
                pos["exit_time"] = datetime.now().isoformat()
                pos["pnl"] = pnl
                pos["pnl_pct"] = pnl_pct
                pos["status"] = "closed"
                
                self.save_positions()
                return pos
        return None
    
    def check_stop_loss(self, stock_code, current_price):
        """손절 조건 확인"""
        for pos in self.positions["positions"]:
            if pos["code"] == stock_code and pos["status"] == "open":
                # strategy.py와 동일한 비율 단위(소수) 사용, 퍼센트 출력용 *100
                loss_rate = (current_price - pos["entry_price"]) / pos["entry_price"]
                loss_pct = loss_rate * 100
                
                # 손절 기준: 손실률이 설정값 이상
                if loss_rate <= -config.RISK_TOLERANCE:
                    return True, loss_pct
        
        return False, 0
    
    def check_take_profit(self, stock_code, current_price):
        """익절 조건 확인"""
        for pos in self.positions["positions"]:
            if pos["code"] == stock_code and pos["status"] == "open":
                # strategy.py와 동일한 비율 단위(소수) 사용, 퍼센트 출력용 *100
                profit_rate = (current_price - pos["entry_price"]) / pos["entry_price"]
                profit_pct = profit_rate * 100
                
                # 익절 기준: 수익률이 설정값 이상
                if profit_rate >= config.TAKE_PROFIT:
                    return True, profit_pct
        
        return False, 0
    
    def get_position(self, stock_code):
        """특정 종목의 포지션 조회"""
        for pos in self.positions["positions"]:
            if pos["code"] == stock_code and pos["status"] == "open":
                return pos
        return None
    
    def get_all_positions(self):
        """모든 포지션 조회"""
        return self.positions["positions"]
    
    def get_summary(self):
        """포지션 요약"""
        open_pos = [p for p in self.positions["positions"] if p["status"] == "open"]
        closed_pos = [p for p in self.positions["positions"] if p["status"] == "closed"]
        
        total_pnl = sum(p.get("pnl", 0) for p in closed_pos)
        avg_pnl_pct = (sum(p.get("pnl_pct", 0) for p in closed_pos) / len(closed_pos)) if closed_pos else 0
        win_count = len([p for p in closed_pos if p.get("pnl", 0) > 0])
        lose_count = len([p for p in closed_pos if p.get("pnl", 0) < 0])
        
        return {
            "open_positions": len(open_pos),
            "closed_positions": len(closed_pos),
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
            "win_count": win_count,
            "lose_count": lose_count,
            "win_rate": (win_count / len(closed_pos) * 100) if closed_pos else 0,
        }

