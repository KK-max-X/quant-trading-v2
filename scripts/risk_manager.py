"""风控模块 —— 整合 freqtrade + vnpy 设计
========================================
来源:
  freqtrade/freqtrade  - 动态止损设计
  vnpy/vnpy           - A股规则 + 仓位管理
"""

import math
from dataclasses import dataclass, field
from typing import Optional


# ========== 仓位计算 ==========

def kelly_position(win_rate: float, avg_win: float, avg_loss: float,
                   kelly_fraction: float = 0.25) -> float:
    """凯利公式仓位比例"""
    if avg_loss <= 0 or win_rate <= 0:
        return 0
    b = avg_win / avg_loss
    kelly = (win_rate * b - (1 - win_rate)) / b
    return max(0, min(kelly, 1)) * kelly_fraction


def equal_risk_size(capital: float, risk_per_trade: float,
                    entry_price: float, stop_price: float,
                    min_shares: int = 100) -> int:
    """等风险仓位计算"""
    risk_amount = capital * risk_per_trade
    risk_per_share = abs(entry_price - stop_price)
    if risk_per_share <= 0:
        return 0
    shares = int(risk_amount / risk_per_share / min_shares) * min_shares
    return max(0, shares)


# ========== 止损管理 ==========

@dataclass
class StopLossManager:
    """动态止损管理器"""

    entry_price: float = 0.0
    high_since_entry: float = 0.0
    days_held: int = 0
    hard_stop_pct: float = 5.0
    trail_enabled: bool = True
    trail_pct: float = 3.0
    ma_stop_enabled: bool = True
    ma_window: int = 5
    time_stop_days: int = 5

    def enter(self, price: float):
        self.entry_price = price
        self.high_since_entry = price
        self.days_held = 0

    def update(self, current_price: float, current_ma: Optional[float] = None) -> dict:
        self.days_held += 1
        self.high_since_entry = max(self.high_since_entry, current_price)

        if current_price <= self.entry_price * (1 - self.hard_stop_pct / 100):
            return {"stop": True, "reason": f"硬止损 -{self.hard_stop_pct}%"}
        if self.trail_enabled:
            trail = self.high_since_entry * (1 - self.trail_pct / 100)
            if current_price <= trail:
                return {"stop": True, "reason": f"移动止损 -{self.trail_pct}%"}
        if self.ma_stop_enabled and current_ma and current_price < current_ma:
            return {"stop": True, "reason": f"跌破MA{self.ma_window}"}
        if self.days_held >= self.time_stop_days:
            return {"stop": True, "reason": f"持有{self.time_stop_days}天到期"}
        return {"stop": False, "reason": ""}


# ========== 市场择时 ==========

@dataclass
class MarketRegimeFilter:
    """市场环境过滤器"""
    score: int = 0
    max_score: int = 19

    def multiplier(self) -> float:
        pct = self.score / max(self.max_score, 1) * 100
        if pct >= 75: return 1.0
        elif pct >= 55: return 0.7
        elif pct >= 35: return 0.35
        return 0.15

    def should_trade(self) -> bool:
        return self.score >= 6

    def advice(self) -> str:
        pct = self.score / max(self.max_score, 1) * 100
        m = self.multiplier()
        if pct >= 75:
            return f"看多 | 建议仓位 {m*100:.0f}% | 积极操作, 单笔止损 5%"
        elif pct >= 55:
            return f"震荡偏多 | 建议仓位 {m*100:.0f}% | 精选个股, 快进快出"
        elif pct >= 35:
            return f"震荡偏弱 | 建议仓位 {m*100:.0f}% | 控制在2-3只, 严格止损"
        return f"偏空 | 建议仓位 {m*100:.0f}% | 防守为主, 空仓或轻仓博弈"


# ========== A股费用 ==========

def calc_commission(amount: float, is_buy: bool = True) -> float:
    c = max(amount * 0.00025, 5)
    if not is_buy:
        c += amount * 0.001
    return round(c, 2)


# ========== 资金曲线 ==========

@dataclass
class EquityTracker:
    initial: float = 100000.0
    current: float = 100000.0
    peak: float = 100000.0
    max_dd: float = 0.0
    trades: list = field(default_factory=list)

    def update(self, v: float):
        self.current = v; self.peak = max(self.peak, v)
        dd = (self.peak - v) / self.peak * 100
        self.max_dd = max(self.max_dd, dd)

    def record(self, pnl: float, reason: str):
        self.trades.append({"pnl": pnl, "reason": reason})

    def summary(self) -> dict:
        r = (self.current / self.initial - 1) * 100
        return {"capital": self.current, "return_pct": r,
                "max_dd_pct": self.max_dd, "trades": len(self.trades)}
