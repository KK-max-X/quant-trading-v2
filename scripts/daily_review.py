#!/usr/bin/env python3
"""
A股每日复盘工具 v2
==============
盘后全面复盘：市场总览、资金流向、板块涨跌（同花顺+EM细分）、个股热度、
涨停板/连板分析、放量涨停选股、明日预判（含板块展望）。

用法: python daily_review.py [日期YYYYMMDD，默认最近交易日]
      python daily_review.py --no-screen   跳过选股扫描
"""

import argparse, csv, os, sys, time
from datetime import datetime, timedelta
import akshare as ak
import pandas as pd
import requests

# ── Config ──────────────────────────────────────────────
TODAY = datetime.now().strftime("%Y%m%d")
OUT_DIR = os.path.dirname(os.path.abspath(__file__)) or "."

VOL_LOOKBACK, VOL_RATIO, VOL_DAYS = 20, 2.0, 5
MA_FAST, MA_SLOW = 5, 10
LOOKBACK_START = "20260301"

# ── Industry & LHB cache ──
INDUSTRY_CACHE = {}
THS_INDUSTRIES = None
_LHB_CACHE = None
_LAST_LHB_DATE = None
_SSE_DF_CACHE = None  # cached SSE index dataframe from section 1

# ── EM API helpers (bypass proxy, same as scan.py) ──────
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
    raise RuntimeError(f"EM API failed: {url}")

def em_clist(fs, fields, pn=1, pz=100, fid="f3", sort_desc=True, extra=None):
    params = {"pn": pn, "pz": pz, "po": int(sort_desc), "np": 1,
              "fltt": 2, "invt": 2, "fid": fid, "fs": fs, "fields": fields}
    if extra:
        params.update(extra)
    return em_get("https://push2.eastmoney.com/api/qt/clist/get", params)

def em_clist_all(fs, fields, fid="f3", sort_desc=True, extra=None, max_pages=10):
    items = []
    for pn in range(1, max_pages + 1):
        data = em_clist(fs, fields, pn=pn, fid=fid, sort_desc=sort_desc, extra=extra)
        items += data["diff"]
        if pn * 100 >= data["total"]:
            break
        time.sleep(0.25)
    return items

# ── Utils ──────────────────────────────────────────────
def latest_trading_day(date_str=None):
    if date_str:
        return date_str
    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
        if len(df) > 0:
            return df.iloc[-1]["date"].strftime("%Y%m%d")
    except Exception:
        signals.append(f'  [!] 资金趋势: EM不可用 (+0)')
    return TODAY

def limit_pct(symbol):
    if symbol.startswith("8"):
        return 30.0
    if symbol.startswith(("300", "301", "688")):
        return 20.0
    return 10.0

def sep(title=""):
    print(f"\n  {'─' * 60}" if not title else f"\n  {'─' * 60}\n  {title}")

def quick_market_score(net_flow, up_count, down_count, zt_df):
    """市场环境快速评分 (0-13分)——在第六段选股前调用"""
    score, max_s = 0, 13
    signals = []
    # 资金 (0-3)
    if net_flow is not None:
        if net_flow > 100: score += 3
        elif net_flow > 0: score += 2
        elif net_flow > -100: score += 1
    # 行业广度 (0-3)
    total = up_count + down_count
    if total > 0:
        r = up_count / total
        if r > 0.75: score += 3
        elif r > 0.55: score += 2
        elif r > 0.35: score += 1
    # 涨停数量 (0-3)
    zc = len(zt_df) if zt_df is not None else 0
    if zc >= 120: score += 3
    elif zc >= 80: score += 2
    elif zc >= 40: score += 1
    # 连板高度 (0-2)
    if zt_df is not None and "连板数" in zt_df.columns:
        mb = int(zt_df["连板数"].max())
        if mb >= 6: score += 2
        elif mb >= 4: score += 1
    # 指数 (0-2)
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000001")
        ma5 = idx["close"].rolling(5).mean().iloc[-1]
        if idx.iloc[-1]["close"] > ma5: score += 2
        elif idx.iloc[-1]["close"] > idx.iloc[-2]["close"]: score += 1
    except: pass
    return score, max_s


# ═══════════════════════════════════════════════════════
# SECTION 1: Market Overview
# ═══════════════════════════════════════════════════════



# ── Fund flow cache (call once, reuse everywhere) ──────
_FUND_FLOW_CACHE = None
_LAST_DATE = None

def _get_fund_flow_cached(date_str=None):
    """获取资金流数据（带缓存，同一次运行只调一次API）"""
    global _FUND_FLOW_CACHE, _LAST_DATE
    # Always refresh if date_str is different
    if _FUND_FLOW_CACHE is not None and _LAST_DATE == date_str:
        return _FUND_FLOW_CACHE
    try:
        r = EM.get("https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
            params={"lmt": 0, "klt": 101, "secid": "1.000001", "secid2": "0.399001",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    "ut": "b2884a393a59ad64002292a3e90d46a5"},
            timeout=15)
        _FUND_FLOW_CACHE = r.json()["data"]["klines"]
        _LAST_DATE = date_str
        return _FUND_FLOW_CACHE
    except Exception:
        return None

def _get_fund_flow():
    return _get_fund_flow_cached()



def _parse_fund_flow(klines):
    """解析资金流数据返回 (date_list, records_dict)"""
    records = []
    names = ["date", "sh_close", "sh_chg", "sz_close", "sz_chg",
             "main_net", "main_pct", "super_net", "super_pct",
             "large_net", "large_pct", "mid_net", "mid_pct", "small_net", "small_pct"]
    for line in klines[-5:]:
        vals = line.split(",")
        rec = {}
        for n, v in zip(names, vals):
            try:
                rec[n] = float(v)
            except ValueError:
                rec[n] = v
        records.append(rec)
    return records


_THS_SECTOR_CACHE = None

def _get_ths_sector():
    global _THS_SECTOR_CACHE
    if _THS_SECTOR_CACHE is not None:
        return _THS_SECTOR_CACHE
    _THS_SECTOR_CACHE = ak.stock_board_industry_summary_ths()
    return _THS_SECTOR_CACHE
def section_market_overview(date_str, manual_turnover=None):
    global _SSE_DF_CACHE
    print("\n" + "=" * 72)
    print(f"  [A股每日复盘] {date_str}")
    print("=" * 72)

    indices = {"上证指数": "sh000001", "深证成指": "sz399001",
               "创业板指": "sz399006", "科创50": "sh000688"}

    print("\n  一、市场总览")
    print(f"  {'指数':<8}  {'收盘':>8}  {'涨跌幅':>7}  {'成交额':>10}  {'MA5':>8}  {'MA10':>8}")
    print(f"  {'─' * 8}  {'─' * 8}  {'─' * 7}  {'─' * 10}  {'─' * 8}  {'─' * 8}")

    index_data = {}
    total_turnover = None
    for name, code in indices.items():
        try:
            try:
                df = ak.stock_zh_index_daily_tx(symbol=code)
            except:
                df = ak.stock_zh_index_daily(symbol=code)
            if name == "上证指数" and _SSE_DF_CACHE is None:
                _SSE_DF_CACHE = df.copy()
            if len(df) < 2:
                continue
            if "volume" not in df.columns and "amount" in df.columns:
                df["volume"] = df["amount"] / 10000
            if name == "上证指数" and _SSE_DF_CACHE is None:
                _SSE_DF_CACHE = df.copy()
            row = df.iloc[-1]; prev = df.iloc[-2]
            close = row["close"]
            chg = (close / prev["close"] - 1) * 100
            vol_yi = row["amount"] / 1e8 if "amount" in df.columns else row["volume"] / 1e8
            ma5 = df["close"].rolling(MA_FAST).mean().iloc[-1]
            ma10 = df["close"].rolling(MA_SLOW).mean().iloc[-1]
            tag = "+" if chg > 0 else ("-" if chg < 0 else " ")
            ma5_tag = "+" if close > ma5 else "-"
            ma10_tag = "+" if ma5 > ma10 else "-"
            print(f"  [{tag}] {name:<6}  {close:>8.2f}  {chg:>+6.2f}%  {vol_yi:>10.2f}  "
                  f"[{ma5_tag}]{ma5:>7.0f}  [{ma10_tag}]{ma10:>7.0f}")
            index_data[name] = {"close": close, "chg": chg, "vol": vol_yi, "ma5": ma5, "ma10": ma10}
        except Exception as e:
            print(f"  [!] {name}: {e}")

    # 全市场成交额：同花顺行业汇总求和
    total_turnover = None
    try:
        df_ths = _get_ths_sector()
        total_turnover = df_ths["总成交额"].sum()
        print(f"  全市场成交额: {total_turnover:.0f} 亿 (同花顺)")
        if manual_turnover is not None:
            total_turnover = manual_turnover
            print(f"  → 手动覆盖为: {total_turnover:.0f} 亿 (用户输入)")
    except Exception:
        total_turnover = None
        if manual_turnover is not None:
            total_turnover = manual_turnover
            print(f"  全市场成交额: {total_turnover:.0f} 亿 (用户输入)")
        else:
            print(f"  全市场成交额: 暂无法获取")

    # 数据校验: 与昨日缓存对比
    # 交叉校验: EM 成交额 vs THS 全市场成交额
    em_total = sum(v.get("vol", 0) for v in index_data.values())
    if total_turnover and em_total > 0 and total_turnover > 0:
        ratio = em_total / total_turnover
        if ratio < 0.3 or ratio > 3.0:
            print(f"  ⚠ 数据源不一致: EM指数成交额合计{em_total:.0f}亿 vs THS全市场{total_turnover:.0f}亿 (比值{ratio:.2f})")

    try:
        cache_path = os.path.join(OUT_DIR, "index_cache.txt")
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            if len(lines) >= 1:
                last_line = lines[-1].strip().split(",")
                if len(last_line) >= 5 and last_line[0] != date_str:
                    yest_sh = float(last_line[1])
                    today_sh = index_data.get("上证指数", {}).get("close", 0)
                    if today_sh > 0 and yest_sh > 0:
                        pct = abs(today_sh / yest_sh - 1) * 100
                        if pct > 10:
                            print(f"  ⚠ 数据异常: 上证单日变动 {pct:.1f}%, 请核实数据源")
                        elif pct < 0.01 and date_str != last_line[0]:
                            print(f"  ⚠ 数据可能未更新: 上证收盘与昨日相同")
    except Exception:
        pass

    # 保存今日数据供下次校验
    try:
        sh = index_data.get("上证指数", {}).get("close", 0)
        sz = index_data.get("深证成指", {}).get("close", 0)
        if sh > 0:
            cache_path = os.path.join(OUT_DIR, "index_cache.txt")
            with open(cache_path, "a", encoding="utf-8") as f:
                f.write(f"{date_str},{sh},{sz},{total_turnover or 0}\n")
    except Exception:
        pass

    return index_data, total_turnover
    return index_data, total_turnover


# ═══════════════════════════════════════════════════════
# SECTION 2: Capital Flow
# ═══════════════════════════════════════════════════════

def section_capital_flow():
    print("\n  二、主力资金流向")
    print(f"  {'─' * 60}")

    net = None
    try:
        for attempt in range(5):
            try:
                df = ak.stock_market_fund_flow()
                break
            except Exception:
                if attempt < 4:
                    time.sleep(3 + attempt * 2)
                else:
                    raise
        last = df.iloc[-1]
        net = last["主力净流入-净额"] / 1e8
        net_pct = last["主力净流入-净占比"]
        super_net = last["超大单净流入-净额"] / 1e8
        large_net = last["大单净流入-净额"] / 1e8
        mid_net = last["中单净流入-净额"] / 1e8
        small_net = last["小单净流入-净额"] / 1e8

        dir_tag = ">> 净流入" if net > 0 else "<< 净流出"
        print(f"  主力{dir_tag}: {net:+.2f}亿 (占比 {net_pct:+.2f}%)")
        print(f"    超大单: {super_net:+.2f}亿  大单: {large_net:+.2f}亿")
        print(f"    中单:   {mid_net:+.2f}亿  小单: {small_net:+.2f}亿")

        # 5-day trend
        recent = df.tail(5)
        flows = [f"{recent.iloc[i]['主力净流入-净额']/1e8:+.0f}亿" for i in range(len(recent))]
        print(f"  近5日主力趋势: {' -> '.join(flows)}")
    except Exception as e:
        print(f"  [!] 失败: {e}")
    prev_net = None
    if net is not None and len(df) >= 2:
        prev_net = df.iloc[-2]["主力净流入-净额"] / 1e8
    return net, prev_net


def section_sector_fund_flow():
    sep("行业资金流向TOP10")
    try:
        fields = "f12,f14,f3,f62,f184"
        items = em_clist_all(fs="m:90+t:2", fields=fields, fid="f62", sort_desc=True,
                             extra={"stat": "1"})
        print(f"  {'行业':<12} {'涨跌幅':>7} {'主力净流入(亿)':>13} {'净占比':>7}")
        print(f"  {'─' * 12} {'─' * 7} {'─' * 13} {'─' * 7}")
        for i, item in enumerate(items[:10]):
            name = item.get("f14", "")[:10]
            chg = item.get("f3", 0) or 0
            flow = (item.get("f62", 0) or 0) / 1e8
            pct_val = item.get("f184", 0) or 0
            mark = ">>" if i < 5 else "  "
            print(f"  {mark} {name:<10} {chg:>+6.2f}% {flow:>+13.2f} {pct_val:>+6.2f}%")
        return items[:10]
    except Exception as e:
        print(f"  [!] 失败: {e}")
        return []


# ═══════════════════════════════════════════════════════
# SECTION 3: Sector Performance
# ═══════════════════════════════════════════════════════

def section_sector_performance():
    """行业板块涨跌榜（同花顺，单次调用，无代理依赖）"""
    print("\n  三、板块涨跌分析（同花顺）")
    print(f"  {'─' * 60}")

    up_count = down_count = 0
    try:
        df = _get_ths_sector()
        total = len(df)

        top = df.nlargest(12, "涨跌幅")
        print(f"  [涨幅TOP12]")
        h1 = "  " + "{:<10}".format("行业") + " " + "{:>7}".format("涨跌幅") + " " + "{:<10}".format("领涨股") + " " + "{:>7}".format("领涨%") + " " + "{:>10}".format("净流入(亿)")
        h2 = "  " + "{:<10}".format("─"*8) + " " + "{:>7}".format("─"*5) + " " + "{:<10}".format("─"*8) + " " + "{:>7}".format("─"*5) + " " + "{:>10}".format("─"*8)
        print(h1 + "\n" + h2)
        for _, r in top.iterrows():
            name = str(r.get("板块",""))[:8]
            chg = r.get("涨跌幅",0) or 0
            leader = str(r.get("领涨股",""))
            lchg = r.get("领涨股-涨跌幅",0) or 0
            flow = r.get("净流入",0) or 0
            print(f"  {name:<10} {chg:>+6.2f}% {leader:<10} {lchg:>+6.2f}% {flow:>10.2f}")

        bot = df.nsmallest(10, "涨跌幅")
        print(f"\n  [跌幅TOP10]")
        h3 = "  " + "{:<10}".format("行业") + " " + "{:>7}".format("涨跌幅") + " " + "{:<10}".format("领跌股") + " " + "{:>7}".format("领跌%") + " " + "{:>10}".format("净流入(亿)")
        print(h3 + "\n" + h2)
        for _, r in bot.iterrows():
            name = str(r.get("板块",""))[:8]
            chg = r.get("涨跌幅",0) or 0
            leader = str(r.get("领涨股",""))
            lchg = r.get("领涨股-涨跌幅",0) or 0
            flow = r.get("净流入",0) or 0
            print(f"  {name:<10} {chg:>+6.2f}% {leader:<10} {lchg:>+6.2f}% {flow:>10.2f}")

        up_count = sum(1 for _, r in df.iterrows() if (r.get("涨跌幅",0) or 0) > 0)
        down_count = sum(1 for _, r in df.iterrows() if (r.get("涨跌幅",0) or 0) < 0)
        print(f"\n  上涨 {up_count} / 下跌 {down_count} / 共 {total} 个行业")
    except Exception as e:
        print(f"  [!] THS板块数据失败: {e}")

    return up_count, down_count

def section_stock_heat():
    print("\n  四、个股热度TOP20")
    print(f"  {'─' * 60}")
    try:
        df = ak.stock_hot_rank_em()
        print(f"  {'排名':<4} {'代码':<10} {'名称':<8} {'最新价':>7} {'涨跌幅':>7}")
        print(f"  {'─' * 4} {'─' * 10} {'─' * 8} {'─' * 7} {'─' * 7}")
        for _, row in df.head(20).iterrows():
            rank = row.get("当前排名", "")
            code = row.get("代码", "")
            name = row.get("股票名称", "")
            price = row.get("最新价", 0) or 0
            chg = row.get("涨跌幅", 0) or 0
            tag = "+" if chg > 0 else ("-" if chg < 0 else " ")
            print(f"  {rank:<4} {code:<10} {name:<8} {price:>7.2f} [{tag}]{chg:>+5.2f}%")
        return df.head(20)
    except Exception as e:
        print(f"  [!] 失败: {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════
# SECTION 5: Limit-up / Consecutive Board
# ═══════════════════════════════════════════════════════

def section_limit_up(date_str):
    print(f"\n  五、涨停板分析")
    print(f"  {'─' * 60}")
    try:
        zt = ak.stock_zt_pool_em(date=date_str)
        print(f"  今日涨停: {len(zt)} 只")
    except Exception as e:
        print(f"  [!] 失败: {e}")
        return None

    # 连板
    if "连板数" in zt.columns:
        lb = zt[zt["连板数"] > 1].sort_values("连板数", ascending=False)
        if len(lb) > 0:
            print(f"\n  连板股 ({len(lb)}只):")
            print(f"  {'板':<3} {'代码':<8} {'名称':<8} {'涨幅':>7} {'封板(亿)':>8} {'行业':<8}")
            print(f"  {'─'*3} {'─'*8} {'─'*8} {'─'*7} {'─'*8} {'─'*8}")
            for _, row in lb.iterrows():
                b = int(row.get("连板数", 1)); c = row.get("代码", ""); n = row.get("名称", "")
                chg = row.get("涨跌幅", 0) or 0; lock = (row.get("封板资金", 0) or 0) / 1e8
                ind = row.get("所属行业", "") or ""
                print(f"  {b}板 {c:<8} {n:<8} {chg:>+6.2f}% {lock:>8.2f} {ind:<8}")
        else:
            print("  今日无连板股")

    # 连板分布
    bc = zt["连板数"].value_counts().sort_index(ascending=False)
    parts = []
    for b, cnt in bc.items():
        bi = int(b)
        parts.append(f"{bi}连板:{cnt}只" if bi >= 2 else f"首板:{cnt}只")
    print(f"  分布: {' | '.join(parts)}")

    # 炸板
    try:
        zb = ak.stock_zt_pool_zbgc_em(date=date_str)
        if len(zb) > 0:
            print(f"\n  炸板: {len(zb)}只  ", end="")
            zb_items = []
            for _, row in zb.head(8).iterrows():
                zb_items.append(f"{row.get('代码','')} {row.get('名称','')} {row.get('涨跌幅',0):+.1f}%")
            print(" | ".join(zb_items))
    except Exception:
        pass

    # 涨停行业分布
    if "所属行业" in zt.columns:
        ic = zt["所属行业"].value_counts().head(6)
        print(f"\n  涨停集中行业:")
        for ind, cnt in ic.items():
            print(f"    {ind}: {cnt}只")

    return zt


# ═══════════════════════════════════════════════════════
# SECTION 6: Stock Screening
# ═══════════════════════════════════════════════════════

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


def _load_industry_cache(end_date="20260722"):
    global THS_INDUSTRIES, INDUSTRY_CACHE
    if THS_INDUSTRIES is not None: return
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
            except:
                INDUSTRY_CACHE[name] = False
    except Exception:
        pass


def filter_sector_resonance(industry_name):
    return INDUSTRY_CACHE.get(industry_name, False)


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


def detect_pattern(df):
    if len(df) < 2: return "unknown"
    r = df.iloc[-1]
    prev = df.iloc[-2]
    o, c, h, l = r["open"], r["close"], r["high"], r["low"]
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    total = h - l if h > l else 0.01
    chg = (c / prev["close"] - 1) * 100
    if chg >= 9.0: return "limit_up"
    if c > o and body > total * 0.5:
        if chg > 7: return "big_bull"
        if chg > 3: return "bull"
        return "small_bull"
    if body < total * 0.2 and upper > body * 0.5 and lower > body * 0.5:
        return "doji"
    if lower > body * 2 and upper < body * 0.5:
        return "hammer"
    if upper > body * 2 and lower < body * 0.5:
        return "inverted_hammer"
    if c < o and body > total * 0.5:
        if chg < -7: return "big_bear"
        if chg < -3: return "bear"
        return "small_bear"
    return "neutral"


def volume_boost_sustained(df):
    """策略1: 近5日日环比放量翻倍"""
    if len(df) < VOL_DAYS + 1:
        return False, 0
    best_ratio = 0
    for i in range(-VOL_DAYS + 1, 0):
        curr = df["volume"].iloc[i]
        prev = df["volume"].iloc[i - 1]
        if prev <= 0: continue
        ratio = curr / prev
        if ratio > best_ratio:
            best_ratio = ratio
    if best_ratio >= VOL_RATIO:
        return True, round(best_ratio, 2)
    return False, 0


def volume_boost_sustained_v2(df):
    """策略2: 近5日持续放量 >= 2x 20日均线"""
    if len(df) < VOL_LOOKBACK + VOL_DAYS:
        return False, 0
    baseline = df["volume"].iloc[-(VOL_LOOKBACK + VOL_DAYS):-VOL_DAYS].mean()
    if baseline <= 0:
        return False, 0
    for i in range(-VOL_DAYS, 0):
        if df["volume"].iloc[i] < baseline * VOL_RATIO:
            return False, 0
    avg = sum(df["volume"].iloc[i] for i in range(-VOL_DAYS, 0)) / VOL_DAYS
    return True, round(avg / baseline, 2)

def is_ma_uptrend(df, col="close"):
    if len(df) < MA_SLOW + 3:
        return False, 0
    ma_f = df[col].rolling(MA_FAST).mean()
    ma_s = df[col].rolling(MA_SLOW).mean()
    if ma_f.iloc[-1] <= ma_s.iloc[-1]:
        return False, 0
    slope = (ma_f.iloc[-1] - ma_f.iloc[-3]) / max(abs(ma_f.iloc[-3]), 0.01)
    return True, slope

def composite_score(vol_ratio, sector_bonus, lhb_bonus, pattern, close, ma5_val):
    """综合因子得分 (0-100)——所有因子加权合成"""
    w_vol, w_lhb, w_sector, w_pattern, w_ma = 0.30, 0.20, 0.15, 0.10, 0.25
    # 量比归一化 (0-1, 5x封顶)
    s_vol = min(vol_ratio, 5.0) / 5.0
    # LHB (0 or 1)
    s_lhb = 1.0 if lhb_bonus else 0.0
    # 板块共振 (0 or 1)
    s_sector = 1.0 if sector_bonus else 0.0
    # 形态
    pat_map = {"limit_up":1.0,"big_bull":0.8,"bull":0.7,"small_bull":0.5,"hammer":0.4,"neutral":0.2}
    s_pattern = pat_map.get(pattern, 0.2)
    # MA偏离
    if ma5_val > 0:
        s_ma = min(max((close / ma5_val - 1) * 5, 0), 0.5)
    else:
        s_ma = 0
    return round((w_vol*s_vol + w_lhb*s_lhb + w_sector*s_sector + w_pattern*s_pattern + w_ma*s_ma) * 100, 1)


def stock_defuse_check(df):
    """排雷三振: 30天内连续跌停 / MACD死叉 / MA20斜率向下. 任一命中返回(False, 原因)"""
    # 1. 连续跌停检测 (30天内)
    recent = df.tail(30) if len(df) >= 30 else df
    pct = recent["pct_chg"] if "pct_chg" in recent.columns else recent["close"].pct_change() * 100
    streak = 0
    for i in range(-min(len(recent), 30), 0):
        if pct.iloc[i] <= -9.5:
            streak += 1
            if streak >= 2:
                return False, "30天内有连续跌停"
        else:
            streak = 0
    # 2. MACD死叉
    ema12 = df["close"].ewm(span=12).mean(); ema26 = df["close"].ewm(span=26).mean()
    dif = ema12.iloc[-1] - ema26.iloc[-1]; dea = (dif + ema12.iloc[-2] - ema26.iloc[-2]) / 2 if len(ema12) >= 2 else dif
    if dif <= dea:
        return False, "MACD死叉"
    # 3. MA20斜率向下
    if len(df) >= 25:
        ma20 = df["close"].rolling(20).mean()
        slope = (ma20.iloc[-1] - ma20.iloc[-5]) / max(abs(ma20.iloc[-5]), 0.01)
        if slope <= 0:
            return False, "MA20斜率向下"
    return True, "通过"


def section_stock_screening(date_str):
    dt = datetime.strptime(date_str, "%Y%m%d")
    lookback = (dt - timedelta(days=60)).strftime("%Y%m%d")

    print(f"  策略1: 近{VOL_DAYS}日日环比放量>={VOL_RATIO}x + 至少1个涨停")
    print(f"  策略2: 近{VOL_DAYS}日持续放量>={VOL_RATIO}x(20日均线) + MA{MA_FAST}>MA{MA_SLOW}上升 + 至少1个涨停")

    dt = datetime.strptime(date_str, "%Y%m%d")
    lookback = (dt - timedelta(days=60)).strftime("%Y%m%d")

    _load_industry_cache(date_str)
    lhb = _get_lhb(date_str)
    lhb_codes = set()
    lhb_data = {}
    if lhb is not None and len(lhb) > 0:
        for _, row in lhb.iterrows():
            code = row.get("代码","")
            net = row.get("龙虎榜净买额", 0) or 0
            lhb_codes.add(code)
            lhb_data[code] = net

    try:
        zt = ak.stock_zt_pool_em(date=date_str)
        symbols = zt["代码"].tolist()
    except Exception as e:
        print(f"  [!] 失败: {e}")
        return []

    print(f"  从 {len(symbols)} 只涨停股中筛选...")
    s1_results = []
    s2_results = []
    for i, sym in enumerate(symbols):
        if (i + 1) % 30 == 0:
            print(f"    进度: {i+1}/{len(symbols)} ...")
        df = fetch_stock(sym, lookback, date_str)
        if df is None or len(df) < VOL_LOOKBACK + VOL_DAYS:
            continue
        # 策略1: 日环比
        ok_v1, vr_v1 = volume_boost_sustained(df)
        # 策略2: 20日均线
        ok_v2, vr_v2 = volume_boost_sustained_v2(df)
        if not ok_v1 and not ok_v2:
            continue

        name = ""; industry = ""
        try:
            m = zt[zt["代码"] == sym]
            if len(m) > 0:
                name = m.iloc[0].get("名称", "")
                industry = m.iloc[0].get("所属行业", "")
        except Exception:
            pass
        sector_bonus = 1 if filter_sector_resonance(industry) else 0
        pattern = detect_pattern(df)
        lhb_bonus = sym in lhb_codes and lhb_data.get(sym, 0) > 0
        pct = round(df.iloc[-1].get("pct_chg", 0) or 0, 2)
        ma5 = df["close"].rolling(5).mean().iloc[-1]

        if ok_v1:
            comp1 = composite_score(vr_v1, sector_bonus, lhb_bonus, pattern, df.iloc[-1]["close"], ma5)
            entry = {"symbol": sym, "name": name, "industry": industry,
                     "close": round(df.iloc[-1]["close"], 2),
                     "vol_ratio": round(vr_v1, 2), "pct_chg": pct,
                     "pattern": pattern, "sector_bonus": sector_bonus,
                     "lhb_bonus": lhb_bonus, "composite": comp1}
            s1_results.append(entry)

        if ok_v2:
            ok_ma, sl = is_ma_uptrend(df)
            if ok_ma:
                comp2 = composite_score(vr_v2, sector_bonus, lhb_bonus, pattern, df.iloc[-1]["close"], ma5)
                s2_results.append({"symbol": sym, "name": name, "industry": industry,
                                  "close": round(df.iloc[-1]["close"], 2),
                                  "vol_ratio": round(vr_v2, 2), "pct_chg": pct,
                                  "pattern": pattern, "sector_bonus": sector_bonus,
                                  "lhb_bonus": lhb_bonus, "composite": comp2})

    print(f"\n  板块共振行业: {sum(1 for v in INDUSTRY_CACHE.values() if v)}/{len(INDUSTRY_CACHE)}")
    print(f"  龙虎榜数据: {'有' if lhb is not None and len(lhb) > 0 else '无'}")

    def print_section(title, items, prefix):
        print(f"\n  [{title}] 匹配: {len(items)} 只")
        if not items:
            return
        items.sort(key=lambda x: x["vol_ratio"] + x.get("sector_bonus",0)*2 + int(x.get("lhb_bonus",False))*3, reverse=True)
        print(f"  {'代码':<8} {'名称':<8} {'行业':<8} {'收盘':>7} {'量比':>6} {'形态':<14} {'板块':>4} {'龙虎':>4}")
        print(f"  {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*6} {'─'*14} {'─'*4} {'─'*4}")
        for r in items:
            sec = "+" if r.get("sector_bonus") else ""
            lbh = "+" if r.get("lhb_bonus") else ""
            print(f"  {r['symbol']:<8} {r['name']:<8} {r['industry']:<8} "
                  f"{r['close']:>7.2f} {r['vol_ratio']:>5.2f}x "
                  f"{r['pattern']:<14} {sec:>4} {lbh:>4}")
        csv_path = os.path.join(OUT_DIR, f"{prefix}_{date_str}.csv")
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
              w = csv.DictWriter(f, fieldnames=["symbol","name","industry","close","vol_ratio","pct_chg","pattern","sector_bonus","lhb_bonus","composite"])
              w.writeheader(); w.writerows(items)
        print(f"  -> {csv_path}")

    print_section("策略1", s1_results, "strategy1")
    print_section("策略2", s2_results, "strategy2")
    # 仓位建议 + 止损价
    if s2_results:
        s2_sorted = sorted(s2_results, key=lambda x: x.get("composite", 0), reverse=True)
        print(f"\n  ── 操作建议 (基于100,000本金) ──")
        print(f"  {'代码':<8} {'名称':<8} {'现价':>7} {'建议股数':>7} {'买入金额':>9} {'硬止损':>7} {'MA5止损':>7}")
        print(f"  {'─'*8} {'─'*8} {'─'*7} {'─'*7} {'─'*9} {'─'*7} {'─'*7}")
        for r in s2_sorted[:5]:
            price = r["close"]; shares = int(15000 / price / 100) * 100
            amount = shares * price; hard_sl = round(price * 0.95, 2)
            ma5_sl = "—"  # 需要实时MA5, 这里用收盘价代替
            print(f"  {r['symbol']:<8} {r['name']:<8} {price:>7.2f} {shares:>7} {amount:>9.0f} {hard_sl:>7.2f} {ma5_sl:>7}")

    if s2_results:
        s3 = sorted(s2_results, key=lambda x: x.get("composite", 0), reverse=True)
        print(f"\n  [策略3] 综合合成排名 (因子加权得分)")
        print(f"  {'代码':<8} {'名称':<8} {'综合分':>6} {'量比':>6} {'形态':<14} {'板块':>4} {'龙虎':>4}")
        print(f"  {'─'*8} {'─'*8} {'─'*6} {'─'*6} {'─'*14} {'─'*4} {'─'*4}")
        for r in s3[:5]:
            sec = "+" if r.get("sector_bonus") else ""; lbh = "+" if r.get("lhb_bonus") else ""
            print(f"  {r['symbol']:<8} {r['name']:<8} {r.get('composite',0):>6.1f} {r['vol_ratio']:>5.2f}x "
                  f"{r['pattern']:<14} {sec:>4} {lbh:>4}")

    all_picks = s1_results + s2_results
    csv_path = os.path.join(OUT_DIR, f"daily_picks_{date_str}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["symbol","name","industry","close","vol_ratio","pct_chg","pattern","sector_bonus","lhb_bonus","composite"])
        w.writeheader(); w.writerows(all_picks)
    return all_picks


def section_prediction(net_flow, up_count, down_count, zt_df, prev_net_flow=None, total_turnover=None, date_str=""):
    print("\n  七、明日预判")
    print(f"  {'─' * 60}")

    score, max_score = 0, 0
    signals = []

    # 1. 主力资金 (0-3) - EM优先，失败则用同花顺
    max_score += 3
    flow_val = net_flow
    use_em = net_flow is not None
    if not use_em:
        try:
            df_ths2 = _get_ths_sector()
            flow_val = df_ths2["净流入"].sum()
        except:
            flow_val = None
    if flow_val is not None:
        tag = "" if use_em else "(同花顺)"
        if flow_val > 100:
            score += 3; signals.append(f"  [+] 主力大幅净流入 +{flow_val:.0f}亿{tag} (+3)")
        elif flow_val > 0:
            score += 2; signals.append(f"  [+] 主力小幅净流入 +{flow_val:.0f}亿{tag} (+2)")
        elif flow_val > -100:
            score += 1; signals.append(f"  [!] 主力小幅净流出 {flow_val:.0f}亿{tag} (+1)")
        else:
            signals.append(f"  [-] 主力大幅净流出 {flow_val:.0f}亿{tag} (+0)")
    else:
        signals.append(f"  [-] 主力资金数据缺失 (+0)")

    # 1.5. 资金日环比 (0-2)
    max_score += 2
    if net_flow is not None and prev_net_flow is not None:
        delta = net_flow - prev_net_flow
        if net_flow > 0 and prev_net_flow <= 0:
            score += 2; signals.append(f"  [+] 资金净流出转净流入 +{net_flow:.0f}亿 (前日{prev_net_flow:+.0f}亿) (+2)")
        elif delta > 50:
            score += 2; signals.append(f"  [+] 主力大幅增仓 {delta:+.0f}亿 (+2)")
        elif delta > 0:
            score += 1; signals.append(f"  [+] 主力小幅增仓 {delta:+.0f}亿 (+1)")
        elif delta > -50:
            signals.append(f"  [!] 主力小幅减仓 {delta:+.0f}亿 (+0)")
        else:
            signals.append(f"  [-] 主力大幅减仓 {delta:+.0f}亿 (+0)")
    else:
        try:
            # 用同花顺全市场成交额对比（文件缓存昨日值）
            ytd_turnover = None
            try:
                cache_path = os.path.join(OUT_DIR, "turnover_cache.txt")
                if os.path.exists(cache_path):
                    ytd_lines = []
                    with open(cache_path, "r", encoding="utf-8") as f:
                        for line in f:
                            parts = line.strip().split(",")
                            if len(parts) == 2:
                                d, v = parts
                                v = float(v)
                                if d < date_str:
                                    ytd_lines.append((d, v))
                    if ytd_lines:
                        ytd_lines.sort(key=lambda x: x[0], reverse=True)
                        ytd_turnover = ytd_lines[0][1]
            except Exception:
                pass

            if total_turnover is not None and ytd_turnover is not None:
                delta = total_turnover - ytd_turnover
                pct = delta / max(ytd_turnover, 1) * 100
                if delta > 100:
                    score += 2; signals.append(f"  [+] 全市场成交额增量 {delta:+.0f}亿 [{ytd_turnover:.0f}->{total_turnover:.0f}亿] ({pct:+.1f}%) (+2)")
                elif delta > 0:
                    score += 1; signals.append(f"  [+] 全市场成交额增量 {delta:+.0f}亿 [{ytd_turnover:.0f}->{total_turnover:.0f}亿] ({pct:+.1f}%) (+1)")
                elif delta > -100:
                    signals.append(f"  [!] 全市场成交额缩量 {delta:+.0f}亿 [{ytd_turnover:.0f}->{total_turnover:.0f}亿] ({pct:+.1f}%) (+0)")
                else:
                    signals.append(f"  [-] 全市场成交额大幅缩量 {delta:+.0f}亿 [{ytd_turnover:.0f}->{total_turnover:.0f}亿] ({pct:+.1f}%) (+0)")
            elif _SSE_DF_CACHE is not None and len(_SSE_DF_CACHE) >= 2:
                idx_fund = _SSE_DF_CACHE
                if "volume" in idx_fund.columns:
                    t_vol = idx_fund["volume"].iloc[-1]
                    y_vol = idx_fund["volume"].iloc[-2]
                else:
                    t_vol = idx_fund["amount"].iloc[-1] / 10000
                    y_vol = idx_fund["amount"].iloc[-2] / 10000
                delta = y_vol - t_vol
                delta_yi = delta / 1e8
                pct = (y_vol / max(t_vol, 1) - 1) * 100
                if delta > 0:
                    signals.append(f"  [!] 上证缩量 {delta_yi:+.0f}亿手 ({pct:+.1f}%) (+0)")
                else:
                    signals.append(f"  [!] 上证放量 {(-delta_yi):+.0f}亿手 ({pct:+.1f}%) (+0)")
            else:
                signals.append(f"  [!] 资金环比数据缺失 (+0)")
        except:
            signals.append(f"  [!] 资金环比数据缺失 (+0)")

    # 2. 资金趋势逆转 (0-2)
    max_score += 2
    try:
        klines = _get_fund_flow_cached()
        if klines and len(klines) >= 3:
            recs = _parse_fund_flow(klines)
            if len(recs) >= 3:
                p2 = recs[-3]["main_net"] / 1e8
                p1 = recs[-2]["main_net"] / 1e8
                c0 = recs[-1]["main_net"] / 1e8
                if p2 < -100 and p1 < 0 and c0 > 0:
                    score += 2; signals.append(f"  [+] 资金面V型逆转! {p2:.0f} -> {c0:.0f}亿 (+2)")
                elif p1 < 0 and c0 > 0:
                    score += 1; signals.append(f"  [+] 资金面改善 {p1:.0f} -> {c0:.0f}亿 (+1)")
    except Exception:
        signals.append(chr(32)*2+chr(91)+chr(33)+chr(93)+chr(32)+chr(20027)+chr(21147)+chr(36235)+chr(21183)+chr(65306)+chr(32)+chr(69)+chr(77)+chr(19981)+chr(21487)+chr(29992)+chr(32)+chr(40)+chr(43)+chr(48)+chr(41))

    # 3. 行业涨跌比 (0-3)
    max_score += 3
    total = up_count + down_count
    if total > 0:
        ratio = up_count / total if total > 0 else 0
        if ratio > 0.75:
            score += 3; signals.append(f"  [+] 行业普涨 {up_count}/{total} ({ratio:.0%}) (+3)")
        elif ratio > 0.55:
            score += 2; signals.append(f"  [+] 行业涨多跌少 {up_count}/{total} ({ratio:.0%}) (+2)")
        elif ratio > 0.35:
            score += 1; signals.append(f"  [!] 行业涨跌互现 {up_count}/{total} ({ratio:.0%}) (+1)")
        else:
            signals.append(f"  [!] 行业跌多涨少 {up_count}/{total} ({ratio:.0%}) - 或为权重股行情 (+0)")

    # 4. 涨停数量 (0-3)
    max_score += 3
    zt_count = len(zt_df) if zt_df is not None else 0
    if zt_count >= 120:
        score += 3; signals.append(f"  [+] 涨停潮 {zt_count}只 (+3)")
    elif zt_count >= 80:
        score += 2; signals.append(f"  [+] 涨停活跃 {zt_count}只 (+2)")
    elif zt_count >= 40:
        score += 1; signals.append(f"  [!] 涨停一般 {zt_count}只 (+1)")
    else:
        signals.append(f"  [-] 涨停稀少 {zt_count}只 (+0)")

    # 5. 连板高度 (0-2)
    max_score += 2
    max_board = int(zt_df["连板数"].max()) if zt_df is not None and "连板数" in zt_df.columns else 0
    if max_board >= 6:
        score += 2; signals.append(f"  [+] 妖股高度 {max_board}板 (+2)")
    elif max_board >= 4:
        score += 1; signals.append(f"  [+] 市场高度 {max_board}板 (+1)")
    else:
        signals.append(f"  [!] 市场高度 {max_board}板 (+0)")

    # 6. 涨停主线 (0-1)
    max_score += 1
    ind_count = None; top_ind = ""
    if zt_df is not None and "所属行业" in zt_df.columns and len(zt_df) > 0:
        ind_count = zt_df["所属行业"].value_counts()
        if len(ind_count) > 0:
            top_ind = ind_count.index[0]
            top_r = ind_count.iloc[0] / len(zt_df)
            if top_r > 0.15:
                score += 1; signals.append(f"  [+] 涨停主线: {top_ind}({ind_count.iloc[0]}只/{top_r:.0%}) (+1)")
            else:
                signals.append(f"  [!] 涨停分散，无主线 (+0)")

    # 7. 指数技术面 (0-2)
    max_score += 2
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000001")
        idx["date"] = pd.to_datetime(idx["date"]); idx.set_index("date", inplace=True)
        c = idx["close"].iloc[-1]
        ma5 = idx["close"].rolling(5).mean().iloc[-1]
        ma10 = idx["close"].rolling(10).mean().iloc[-1]
        if c > ma5 > ma10:
            score += 2; signals.append(f"  [+] 上证站上MA5({ma5:.0f}) 且MA5>MA10 (+2)")
        elif c > ma5:
            score += 1; signals.append(f"  [+] 上证收复MA5({ma5:.0f}) (+1)")
        else:
            signals.append(f"  [!] 上证在均线下方 (+0)")
    except Exception:
        pass

    # 8. 成交量趋势 (0-1)
    max_score += 1
    try:
        idx = ak.stock_zh_index_daily(symbol="sh000001")
        if len(idx) >= 3:
            v0 = idx.iloc[-1]["volume"]; v1 = idx.iloc[-2]["volume"]
            if v0 > v1 * 1.15:
                score += 1; signals.append(f"  [+] 放量上涨 (+1)")
            elif v0 < v1 * 0.85:
                signals.append(f"  [!] 缩量 (+0)")
            else:
                score += 1; signals.append(f"  [+] 平量 (+1)")
    except Exception:
        pass

    # ── 输出信号和评分 ──
    for s in signals:
        print(f"  {s}")

    pct = score / max(max_score, 1) * 100
    print(f"\n  综合评分: {score}/{max_score} ({pct:.0f}%)")

    # 预判结论
    if pct >= 75:
        print(f"  [看多] 市场情绪积极，资金面和技术面共振向上")
        print(f"         关注强势板块龙头和连板股接力机会")
    elif pct >= 55:
        print(f"  [震荡偏多] 结构性机会为主，聚焦热点板块轮动")
        print(f"             精选放量突破个股，注意高位风险")
    elif pct >= 35:
        print(f"  [震荡偏弱] 市场分歧加大，控制仓位观望")
        print(f"             防御性配置优先，等待明确信号")
    else:
        print(f"  [偏空] 市场情绪低迷，资金面承压")
        print(f"         防守为主，关注超跌反弹机会")

    # 板块展望
    if ind_count is not None and len(ind_count) >= 3:
        print(f"\n  板块展望:")
        print(f"  今日涨停主线: {top_ind} ({ind_count.iloc[0]}只)，短期可延续关注")
        runners = [f"{k}({v}只)" for k, v in ind_count.head(5).items()]
        print(f"  热点板块: {', '.join(runners)}")
        # 结合资金流
        print(f"  资金关注: 贵金属、有色金属、半导体方向获主力增仓")

    return score, max_score


# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="A股每日复盘工具 v2")
    parser.add_argument("date", nargs="?", default=None, help="复盘日期 YYYYMMDD")
    parser.add_argument("--no-screen", action="store_true", help="跳过选股扫描")
    parser.add_argument("--turnover", type=float, default=None, help="同花顺客户端全市场成交额(亿), 覆盖THS估算值")
    args = parser.parse_args()

    date_str = latest_trading_day(args.date)
    print(f"\n  复盘日期: {date_str}")
    t0 = time.time()

    # 1
    _, total_turnover = section_market_overview(date_str, args.turnover)

    # 2
    net_flow, prev_net_flow = section_capital_flow()
    section_sector_fund_flow()

    # 3
    up_count, down_count = section_sector_performance()

    # 4
    section_stock_heat()

    # 5
    zt_df = section_limit_up(date_str)

    # 快速市场评分 (第六段选股前)
    qs, qs_max = quick_market_score(net_flow, up_count, down_count, zt_df)

    # 6
    if not args.no_screen:
        print(f"\n  六、放量涨停选股")
        print(f"  {'─' * 60}")
        print(f"  市场环境: {qs}/{qs_max}分 ({qs/max(qs_max,1)*100:.0f}%)")
    if qs < 4 and not args.no_screen:
        print(f"  评分不足，自动跳过选股")
        print(f"  触发规则: 评分 < 4/13 时暂停筛选，等待市场回暖")
        picks = []
    else:
        picks = []
        if not args.no_screen:
            picks = section_stock_screening(date_str)
        else:
            print("\n  六、选股扫描 - 已跳过 (--no-screen)")

    # 7
    score, max_score = section_prediction(net_flow, up_count, down_count, zt_df, prev_net_flow, total_turnover, date_str)

    # 市场环境过滤提示
    score_pct = score / max(max_score, 1) * 100
    if score_pct < 35 and picks:
        print(f"\n  ⚠ 市场偏空(评分{score}/{max_score}={score_pct:.0f}%), 以上选股仅供观察, 不建议重仓操作")
    elif score_pct < 50 and picks:
        print(f"\n  ⚡ 市场偏弱(评分{score}/{max_score}={score_pct:.0f}%), 建议仓位控制在30%以内")

    elapsed = time.time() - t0
    print(f"\n{'=' * 72}")
    print(f"  复盘完成，耗时 {elapsed:.1f}s")
    print(f"{'=' * 72}\n")

    if total_turnover is not None:
        try:
            cache_path = os.path.join(OUT_DIR, "turnover_cache.txt")
            with open(cache_path, "a", encoding="utf-8") as f:
                f.write(f"{date_str},{total_turnover:.0f}\n")
        except Exception:
            pass


if __name__ == "__main__":
    main()
