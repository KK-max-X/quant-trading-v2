import requests, akshare as ak, pandas as pd, time, sys, csv, os

VOL_LOOKBACK, VOL_RATIO, VOL_DAYS = 20, 2.0, 3
MA_FAST, MA_SLOW = 5, 10
END_DATE, START_DATE = "20260722", "20260301"
OUT_CSV = os.path.join(os.path.dirname(__file__) or ".", "scan_results.csv")

S = requests.Session()
S.trust_env = False

def limit_pct(symbol):
    "Return the daily price limit percentage for a stock."
    if symbol.startswith("8"):
        return 30.0
    if symbol.startswith("300") or symbol.startswith("301") or symbol.startswith("688"):
        return 20.0
    return 10.0

def em_get(url, params, timeout=15):
    for attempt in range(5):
        try:
            r = S.get(url, params=params, timeout=timeout)
            return r.json()["data"]
        except:
            if attempt < 4:
                time.sleep(3 + attempt * 2)
    raise

def get_em_industries():
    items = []
    for pn in range(1, 11):
        data = em_get("https://push2.eastmoney.com/api/qt/clist/get",
            {"pn":pn,"pz":50,"po":1,"np":1,"fltt":2,"invt":2,"fid":"f3",
             "fs":"m:90+t:2","fields":"f12,f14"})
        items += data["diff"]
        if pn * 50 >= data["total"]:
            break
        time.sleep(0.5)
    return [(x["f12"], x["f14"]) for x in items]

def get_industry_stocks(em_code):
    items = []
    for pn in range(1, 20):
        data = em_get("https://push2.eastmoney.com/api/qt/clist/get",
            {"pn":pn,"pz":50,"po":1,"np":1,"fltt":2,"invt":2,"fid":"f3",
             "fs":f"b:{em_code}+f:!50","fields":"f12,f14"})
        items += data["diff"]
        if pn * 50 >= data["total"]:
            break
        time.sleep(0.3)
    return [(x["f12"], x["f14"]) for x in items]

def is_uptrend(df, col="close", fast=MA_FAST, slow=MA_SLOW):
    if len(df) < slow:
        return False, 0
    ma_f = df[col].rolling(fast).mean()
    ma_s = df[col].rolling(slow).mean()
    last = df.index[-1]
    slope = (ma_f.iloc[-1] - ma_f.iloc[-3]) / max(ma_f.iloc[-3], 0.01)
    return (ma_f[last] > ma_s[last]) and (df[col].iloc[-1] > ma_f[last]), slope

def volume_boost_sustained(df):
    """Each of the last VOL_DAYS must individually >= baseline * VOL_RATIO."""
    if len(df) < VOL_LOOKBACK + VOL_DAYS:
        return False, 0
    baseline = df["volume"].iloc[-(VOL_LOOKBACK+VOL_DAYS):-VOL_DAYS].mean()
    if baseline <= 0:
        return False, 0
    vol_avg = 0
    for i in range(-VOL_DAYS, 0):
        v = df["volume"].iloc[i]
        if v < baseline * VOL_RATIO:
            return False, 0
        vol_avg += v
    vol_avg /= VOL_DAYS
    return True, vol_avg / baseline

def has_limit_up(df, symbol):
    """Check if at least one day had a limit-up in the data window."""
    lim = limit_pct(symbol)
    pct = df.get("pct_chg", pd.Series(0, index=df.index))
    return (pct >= lim * 0.9).any()  # 90% of limit = give a bit of tolerance

def fetch_stock(s):
    try:
        df = ak.stock_zh_a_hist(symbol=s, period="daily",
            start_date=START_DATE, end_date=END_DATE, adjust="qfq")
        df.rename(columns={"日期":"date","开盘":"open","收盘":"close",
            "最高":"high","最低":"low","成交量":"volume","成交额":"amount",
            "涨跌幅":"pct_chg"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df.sort_index()
    except:
        return None

def fetch_ind(name):
    try:
        df = ak.stock_board_industry_index_ths(symbol=name,
            start_date=START_DATE, end_date=END_DATE)
        df.rename(columns={"日期":"date","开盘价":"open","收盘价":"close",
            "最高价":"high","最低价":"low","成交量":"volume","成交额":"amount"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df.sort_index()
    except:
        return None

print(">>> 1. Industry trends (THS)")
ths = ak.stock_board_industry_name_ths()
i_trend = {}
for _, row in ths.iterrows():
    n = row["name"]
    df = fetch_ind(n)
    i_trend[n] = is_uptrend(df)[0] if df is not None and len(df) >= MA_SLOW else False
trending = {k for k,v in i_trend.items() if v}
print(f"  {len(trending)}/{len(i_trend)} uptrend: {sorted(trending)}")

print(">>> 2. Stock-industry mapping (EM)")
em = get_em_industries()
print(f"  {len(em)} EM industries")
ths_set = set(ths["name"])
candidates = {}
for code, name in em:
    if name not in ths_set or name not in trending:
        continue
    try:
        stocks = get_industry_stocks(code)
    except:
        continue
    for s, sn in stocks:
        if s not in candidates:
            candidates[s] = (sn, name)
print(f"  {len(candidates)} stocks in trending industries")

if not candidates:
    print("No candidates.")
    sys.exit(0)

print(f">>> 3. Scanning {len(candidates)} stocks ...")
results = []
for i, (sym, (sname, ind_name)) in enumerate(candidates.items()):
    if (i+1) % 50 == 0:
        print(f"  {i+1}/{len(candidates)} ...")
    df = fetch_stock(sym)
    if df is None or len(df) < VOL_LOOKBACK + VOL_DAYS:
        continue
    ok, r = volume_boost_sustained(df)
    if not ok:
        continue
    to, sl = is_uptrend(df)
    if not to:
        continue
    if not has_limit_up(df, sym):
        continue
    results.append({
        "symbol": sym,
        "name": sname,
        "industry": ind_name,
        "close": round(df.iloc[-1]["close"], 2),
        "vol_ratio": round(r, 2),
        "pct_chg": round(df.iloc[-1].get("pct_chg", 0), 2),
        "trend_slope": round(sl, 4),
    })

print(f"\n=== {len(results)} matches ===")
if results:
    results.sort(key=lambda x: x["vol_ratio"], reverse=True)
    for r in results:
        print(f"  {r['symbol']:>8}  {r['name']:<8}  {r['industry']:<6}  "
              f"close={r['close']:>7.2f}  vol={r['vol_ratio']:>4.1f}x  "
              f"chg={r['pct_chg']:>6.2f}%  slope={r['trend_slope']:>6.4f}")

    with open(OUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["symbol","name","industry","close","vol_ratio","pct_chg","trend_slope"])
        w.writeheader()
        w.writerows(results)
    print(f"\n>>> Saved to {OUT_CSV}")
