#!/usr/bin/env python3
"""
A股5日波段策略系统
==================
持仓周期: 5日
筛选因子: 避雷 → 量能翻倍 → MA5>MA10上升 → 板块共振 → 资金加分
回测: 每日收盘选股 → 次日入场 → 5日收益 → K线形态胜率统计
"""

import csv, os, sys, time
from collections import defaultdict
from datetime import datetime, timedelta
import akshare as ak
import pandas as pd
import requests

# ========== 配置 ==========
HOLD_DAYS = 5
VOL_LOOKBACK, VOL_RATIO, VOL_DAYS = 20, 2.0, 3

INDUSTRY_CACHE = {}
THS_INDUSTRIES = None
_LHB_CACHE = None
_LAST_LHB_DATE = None
MA_FAST, MA_SLOW = 5, 10
BT_START, BT_END = "20260401", "20260721"
OUT_DIR = os.path.dirname(os.path.abspath(__file__)) or "."

# ========== EM API ==========
EM = requests.Session()
EM.trust_env = False

def em_get(url, params, timeout=15):
    for attempt in range(5):
        try:
            r = EM.get(url, params=params, timeout=timeout)
            return r.json()["data"]
        except Exception:
            if attempt < 4:
                time.sleep(3 + attempt * 2)
    raise

def em_clist_all(fs, fields, fid="f3", sort_desc=True, extra=None, max_pages=10):
    items = []
    for pn in range(1, max_pages + 1):
        params = {"pn": pn, "pz": 100, "po": int(sort_desc), "np": 1,
                  "fltt": 2, "invt": 2, "fid": fid, "fs": fs, "fields": fields}
        if extra: params.update(extra)
        data = em_get("https://push2.eastmoney.com/api/qt/clist/get", params)
        items += data["diff"]
        if pn * 100 >= data["total"]: break
        time.sleep(0.2)
    return items

def limit_pct(s):
    if s.startswith("8"): return 30.0
    if s.startswith(("300","301","688")): return 20.0
    return 10.0

# ========== 数据层 ==========

def fetch_stock(symbol, start, end):
    tx = "sh" + symbol if symbol.startswith(("6","9")) else "sz" + symbol
    try:
        df = ak.stock_zh_a_hist_tx(symbol=tx, start_date=start, end_date=end)
        df.rename(columns={"date":"date","open":"open","close":"close",
                           "high":"high","low":"low","amount":"amount"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df = df.sort_index()
        df["volume"] = (df["amount"] * 10000 / df["close"] / 100).astype(int)
        df["pct_chg"] = df["close"].pct_change() * 100
        return df
    except Exception:
        return None



def _get_lhb(date_str):
    global _LHB_CACHE, _LAST_LHB_DATE
    if _LHB_CACHE is not None and _LAST_LHB_DATE == date_str:
        return _LHB_CACHE
    try:
        _LHB_CACHE = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
        _LAST_LHB_DATE = date_str
        return _LHB_CACHE
    except:
        return None
def load_industry_cache(end_date="20260722"):
    global THS_INDUSTRIES, INDUSTRY_CACHE
    if THS_INDUSTRIES is not None: return
    print("  [加载] THS行业趋势...")
    try:
        ths = ak.stock_board_industry_name_ths()
        THS_INDUSTRIES = ths
        for _, row in ths.iterrows():
            name = row["name"]
            try:
                df = ak.stock_board_industry_index_ths(
                    symbol=name, start_date="20260301", end_date=end_date)
                if df is not None and len(df) >= MA_SLOW + 3:
                    df.rename(columns={"日期":"date","收盘价":"close"}, inplace=True)
                    df["date"] = pd.to_datetime(df["date"]); df.set_index("date", inplace=True)
                    df = df.sort_index()
                    ma_f = df["close"].rolling(MA_FAST).mean()
                    ma_s = df["close"].rolling(MA_SLOW).mean()
                    c = df["close"].iloc[-1]
                    INDUSTRY_CACHE[name] = (c > ma_f.iloc[-1] and ma_f.iloc[-1] > ma_s.iloc[-1])
                else:
                    INDUSTRY_CACHE[name] = False
            except: INDUSTRY_CACHE[name] = False
        up = sum(1 for v in INDUSTRY_CACHE.values() if v)
        trending_names = [k for k, v in INDUSTRY_CACHE.items() if v]
        print(f"  行业趋势: {up}/{len(INDUSTRY_CACHE)} 个上升: " + ", ".join(trending_names))
    except Exception as e:
        print(f"  [!] 行业加载失败: {e}")

# ========== 筛选引擎 ==========

def filter_st_exclude(name, code):
    """避雷: 排除ST/*ST"""
    n = name.upper()
    return "ST" not in n and "*ST" not in n and code not in n

def filter_volume_boost(df):
    if len(df) < VOL_LOOKBACK + VOL_DAYS:
        return False, 0
    baseline = df["volume"].iloc[-(VOL_LOOKBACK+VOL_DAYS):-VOL_DAYS].mean()
    if baseline <= 0: return False, 0
    for i in range(-VOL_DAYS, 0):
        if df["volume"].iloc[i] < baseline * VOL_RATIO:
            return False, 0
    avg = sum(df["volume"].iloc[i] for i in range(-VOL_DAYS, 0)) / VOL_DAYS
    return True, avg / baseline

def filter_ma_uptrend(df):
    """均线: MA5 > MA10 且双均线斜率向上"""
    if len(df) < MA_SLOW + 3: return False, 0
    ma_f = df["close"].rolling(MA_FAST).mean()
    ma_s = df["close"].rolling(MA_SLOW).mean()
    if ma_f.iloc[-1] <= ma_s.iloc[-1]: return False, 0
    slope5 = (ma_f.iloc[-1] - ma_f.iloc[-3]) / max(abs(ma_f.iloc[-3]), 0.01)
    slope10 = (ma_s.iloc[-1] - ma_s.iloc[-3]) / max(abs(ma_s.iloc[-3]), 0.01)
    if slope5 <= 0: return False, 0
    return True, slope5

def filter_sector_resonance(industry_name):
    """板块共振: 所属行业处于上升趋势"""
    return INDUSTRY_CACHE.get(industry_name, False)

def detect_pattern(df):
    """K线形态识别"""
    if len(df) < 2: return "unknown"
    r = df.iloc[-1]
    prev = df.iloc[-2]
    o, c, h, l = r["open"], r["close"], r["high"], r["low"]
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    total = h - l if h > l else 0.01
    chg = (c / prev["close"] - 1) * 100

    # 涨停板
    lim = 9.9  # approximate
    if chg >= lim * 0.9:
        return "limit_up"

    # 大阳线 (实体 > 50% range)
    if c > o and body > total * 0.5:
        if chg > 7: return "big_bull"
        if chg > 3: return "bull"
        return "small_bull"

    # 十字星 (实体 < 20% range, 上下影线都存在)
    if body < total * 0.2 and upper > body * 0.5 and lower > body * 0.5:
        return "doji"

    # 锤子线 (下影 > 2倍实体, 上影很短)
    if lower > body * 2 and upper < body * 0.5:
        return "hammer"

    # 倒锤子
    if upper > body * 2 and lower < body * 0.5:
        return "inverted_hammer"

    # 大阴线
    if c < o and body > total * 0.5:
        if chg < -7: return "big_bear"
        if chg < -3: return "bear"
        return "small_bear"

    return "neutral"

# ========== 回测引擎 ==========

def backtest_single_day(date_str):
    """单日回测: 选股 → 次日入场 → 5日收益"""
    try:
        zt = ak.stock_zt_pool_em(date=date_str)
    except:
        return []

    dt = datetime.strptime(date_str, "%Y%m%d")
    results = []

    for _, row in zt.iterrows():
        sym = row["代码"]
        name = row.get("名称", "")
        industry = row.get("所属行业", "")
        # 避雷
        if not filter_st_exclude(name, sym): continue

        df = fetch_stock(sym, "20260201", date_str)
        if df is None or len(df) < VOL_LOOKBACK + VOL_DAYS: continue

        # 量能
        ok_vol, vr = filter_volume_boost(df)
        if not ok_vol: continue

        # 均线
        ok_ma, sl = filter_ma_uptrend(df)
        if not ok_ma: continue

        # 板块共振
        sector_bonus = 1 if filter_sector_resonance(industry) else 0

        # K线形态
        pattern = detect_pattern(df)
        close_t = df.iloc[-1]["close"]

        # 获取次日开始的5日行情
        end_dt = dt + timedelta(days=10)  # generous window
        fwd = fetch_stock(sym, date_str, end_dt.strftime("%Y%m%d"))
        if fwd is None or len(fwd) <= len(df):
            # no new data, skip
            continue

        # 找到T日之后的T+1...T+5
        fwd_dates = fwd.index[fwd.index > df.index[-1]]
        if len(fwd_dates) < HOLD_DAYS: continue

        fwd_slice = fwd.loc[fwd_dates[:HOLD_DAYS]]
        if len(fwd_slice) < 1: continue

        open_t1 = fwd_slice.iloc[0]["open"]          # T+1开盘价=买入价
        close_t5 = fwd_slice.iloc[-1]["close"]       # T+5收盘价=卖出价
        high_5d = fwd_slice["high"].max()
        ret_5d = (close_t5 / open_t1 - 1) * 100

        results.append({
            "date": date_str, "symbol": sym, "name": name,
            "industry": industry, "pattern": pattern,
            "close_t": close_t, "open_t1": open_t1,
            "close_t5": close_t5, "ret_5d": round(ret_5d, 2),
            "vol_ratio": round(vr, 2), "ma_slope": round(sl, 4),
            "high_5d": round((high_5d / open_t1 - 1) * 100, 2),
            "sector_bonus": sector_bonus,
        })

    return results

def run_backtest(start_date, end_date):
    """运行完整回测"""
    print(f"\n回测区间: {start_date} → {end_date}")
    load_industry_cache(end_date)

    # 获取交易日列表
    idx = ak.stock_zh_index_daily(symbol="sh000001")
    idx["date"] = pd.to_datetime(idx["date"]); idx.set_index("date", inplace=True)
    trading_days = [d.strftime("%Y%m%d") for d in idx.index
                    if start_date <= d.strftime("%Y%m%d") <= end_date]
    print(f"交易天数: {len(trading_days)}")

    all_results = []
    for i, date_str in enumerate(trading_days):
        print(f"  [{i+1}/{len(trading_days)}] {date_str} ...", end=" ")
        try:
            day_results = backtest_single_day(date_str)
            all_results += day_results
            print(f"{len(day_results)} match")
        except Exception as e:
            print(f"err: {e}")
        time.sleep(0.3)

    return all_results

# ========== 统计 ==========

def analyze_results(results):
    """分析回测结果，按K线形态分组统计"""
    if not results:
        print("\n无回测结果")
        return

    df = pd.DataFrame(results)
    print(f"\n{'='*60}")
    print(f"回测统计")
    print(f"{'='*60}")
    print(f"总交易: {len(df)} 笔")
    print(f"总胜率: {(df['ret_5d'] > 0).sum()}/{len(df)} ({(df['ret_5d'] > 0).mean()*100:.1f}%)")
    print(f"平均收益: {df['ret_5d'].mean():.2f}%")
    print(f"中位收益: {df['ret_5d'].median():.2f}%")
    print(f"最大盈利: {df['ret_5d'].max():.2f}%")
    print(f"最大亏损: {df['ret_5d'].min():.2f}%")
    print(f"平均最大浮盈: {df['high_5d'].mean():.2f}%")

    # 按形态分组
    print(f"\n[K线形态胜率排名]")
    pattern_stats = df.groupby("pattern").agg(
        笔数=("ret_5d", "count"),
        胜率=("ret_5d", lambda x: (x > 0).mean() * 100),
        平均收益=("ret_5d", "mean"),
        最大盈利=("ret_5d", "max"),
        最大亏损=("ret_5d", "min"),
        平均浮盈=("high_5d", "mean"),
    ).round(2)
    pattern_stats = pattern_stats.sort_values("胜率", ascending=False)
    print(pattern_stats.to_string())

    # 按行业分组
    print(f"\n[行业胜率排名 (>=5笔)]")
    ind_stats = df.groupby("industry").agg(
        笔数=("ret_5d", "count"),
        胜率=("ret_5d", lambda x: (x > 0).mean() * 100),
        平均收益=("ret_5d", "mean"),
    ).round(2)
    ind_stats = ind_stats[ind_stats["笔数"] >= 5].sort_values("胜率", ascending=False)
    print(ind_stats.head(20).to_string())

    # 保存详细结果
    csv_path = os.path.join(OUT_DIR, "strategy_backtest.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"\n详细结果: {csv_path}")

    # 形态排名CSV
    pat_csv = os.path.join(OUT_DIR, "strategy_patterns.csv")
    pattern_stats.to_csv(pat_csv, encoding="utf-8-sig")
    print(f"形态排名: {pat_csv}")

    return pattern_stats

# ========== 日频选股 ==========

def daily_screen(date_str):
    """日频选股: 使用完整筛选流程，输出推荐列表"""
    print(f"\n{'='*60}")
    print(f"日频选股: {date_str}")
    print(f"{'='*60}")
    load_industry_cache(date_str)

    try:
        zt = ak.stock_zt_pool_em(date=date_str)
    except:
        print("无法获取涨停板数据")
        return []

    results = []
    for _, row in zt.iterrows():
        sym = row["代码"]
        name = row.get("名称", "")
        industry = row.get("所属行业", "")
        if not filter_st_exclude(name, sym): continue

        df = fetch_stock(sym, "20260201", date_str)
        if df is None: continue

        ok_vol, vr = filter_volume_boost(df)
        if not ok_vol: continue

        ok_ma, sl = filter_ma_uptrend(df)
        if not ok_ma: continue

        sector_bonus = 1 if filter_sector_resonance(industry) else 0

        pattern = detect_pattern(df)

        # 加分项: 龙虎榜
        lhb_bonus = False
        try:
            lhb = ak.stock_lhb_detail_em(start_date=date_str, end_date=date_str)
            lhb_codes = set(lhb["代码"].tolist())
            if lhb is not None and sym in lhb_codes:
                lhb_row = lhb[lhb["代码"] == sym]
                if len(lhb_row) > 0:
                    net_buy = lhb_row.iloc[0].get("龙虎榜净买额", 0) or 0
                    if net_buy > 0: lhb_bonus = True
        except: pass

        results.append({
            "symbol": sym, "name": name, "industry": industry,
            "close": round(df.iloc[-1]["close"], 2),
            "vol_ratio": round(vr, 2), "ma_slope": round(sl, 4),
            "pattern": pattern, "lhb_bonus": lhb_bonus,
            "sector_bonus": sector_bonus,
        })

    results.sort(key=lambda x: x["vol_ratio"] + x.get("sector_bonus",0)*2 + int(x.get("lhb_bonus",False))*3, reverse=True)

    print(f"\n匹配: {len(results)} 只")
    if results:
        h1 = "  {:<8} {:<8} {:<10} {:>7} {:>6} {:<18} {:>4} {:>4}".format("代码","名称","行业","收盘","量比","形态","板块","龙虎")
        h2 = "  {:<8} {:<8} {:<10} {:>7} {:>6} {:<18} {:>4} {:>4}".format("-"*8,"-"*8,"-"*10,"-"*7,"-"*6,"-"*18,"-"*4,"-"*4)
        print(h1 + "\n" + h2)
        print(h2)
        for r in results[:30]:
            sec = "+" if r.get("sector_bonus") else ""
            lbh = "+" if r.get("lhb_bonus") else ""
            print(f"  {r["symbol"]:<8} {r["name"]:<8} {r["industry"]:<10} "
                  f"{r["close"]:>7.2f} {r["vol_ratio"]:>5.2f}x "
                  f"{r["pattern"]:<18} {sec:>3} {lbh:>3}")

        csv_path = os.path.join(OUT_DIR, f"strategy_picks_{date_str}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader(); w.writerows(results)
        print(f"\n→ {csv_path}")

    return results

# ========== MAIN ==========

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="A股5日波段策略")
    ap.add_argument("mode", nargs="?", default="screen",
                    help="screen(选股) | backtest(回测)")
    ap.add_argument("--date", default="20260721", help="选股日期 YYYYMMDD")
    ap.add_argument("--start", default=BT_START, help="回测起始日")
    ap.add_argument("--end", default=BT_END, help="回测结束日")
    args = ap.parse_args()

    if args.mode == "backtest":
        results = run_backtest(args.start, args.end)
        analyze_results(results)
    else:
        daily_screen(args.date)
