"""扩展因子库 —— 整合 QUANTAXIS 因子计算
========================================
来源: QUANTAXIS/QUANTAXIS 因子库设计
"""

import pandas as pd
import numpy as np


def calc_rsi(df: pd.DataFrame, period: int = 14, col: str = "close") -> pd.Series:
    """RSI 相对强弱指标 (Wilder's smoothing)"""
    delta = df[col].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9,
              col: str = "close") -> dict:
    """MACD 指标

    Returns:
        {"dif": Series, "dea": Series, "hist": Series}
    """
    ema_fast = df[col].ewm(span=fast, adjust=False).mean()
    ema_slow = df[col].ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2  # 柱状线 (A股惯例乘2)
    return {"dif": dif, "dea": dea, "hist": hist}


def calc_bollinger(df: pd.DataFrame, period: int = 20, std: int = 2,
                   col: str = "close") -> dict:
    """布林带

    Returns:
        {"upper": Series, "middle": Series, "lower": Series, "width": Series, "pct_b": Series}
    """
    middle = df[col].rolling(period).mean()
    std_dev = df[col].rolling(period).std()
    upper = middle + std * std_dev
    lower = middle - std * std_dev
    width = (upper - lower) / middle * 100  # 带宽百分比
    pct_b = (df[col] - lower) / (upper - lower)  # %b: 价格在带中的位置
    return {"upper": upper, "middle": middle, "lower": lower,
            "width": width, "pct_b": pct_b}


def calc_turnover_anomaly(df: pd.DataFrame) -> dict:
    """换手率异常检测

    检测最近一日换手率是否异常（相对20日均值的倍数）

    Returns:
        {"latest": float, "avg_20": float, "ratio": float, "alert": bool}
    """
    if "turnover" not in df.columns:
        return {"latest": 0, "avg_20": 0, "ratio": 0, "alert": False}

    to = df["turnover"]
    latest = to.iloc[-1]
    avg_20 = to.tail(21).head(20).mean() if len(to) >= 21 else to.mean()
    ratio = latest / avg_20 if avg_20 > 0 else 0
    alert = ratio > 3.0  # 换手率超3倍均值 = 异常放量
    return {"latest": round(float(latest), 2), "avg_20": round(float(avg_20), 2),
            "ratio": round(float(ratio), 1), "alert": alert}


def calc_volume_divergence(df: pd.DataFrame) -> str:
    """量价背离检测

    比较最新价的趋势方向和量的趋势方向

    Returns:
        "bullish" (价涨量增), "bearish" (价跌量增),
        "divergence" (价涨量缩), "neutral"
    """
    if len(df) < 5:
        return "neutral"
    recent = df.tail(5)
    price_trend = 1 if recent["close"].iloc[-1] > recent["close"].iloc[0] else -1
    vol_trend = 1 if recent["volume"].iloc[-1] > recent["volume"].iloc[0] else -1

    if price_trend > 0 and vol_trend > 0:
        return "bullish"
    elif price_trend < 0 and vol_trend > 0:
        return "bearish"
    elif price_trend > 0 and vol_trend < 0:
        return "divergence"
    return "neutral"


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR 平均真实波幅 (用于止损设置)"""
    if len(df) < 2:
        return pd.Series([0] * len(df))
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()
