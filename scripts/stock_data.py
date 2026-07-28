"""Stock data - lazy edition."""
import argparse
import akshare as ak
import pandas as pd


def fetch(symbol: str, start: str, end: str) -> pd.DataFrame:
    """A-share daily (adjusted)."""
    df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq")
    df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
                        "最低": "low", "成交量": "volume", "成交额": "amount",
                        "涨跌幅": "pct_chg", "换手率": "turnover"}, inplace=True)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df.sort_index()


def spot() -> pd.DataFrame:
    """Real-time snapshot (Sina source, works behind proxy)."""
    return ak.stock_zh_a_spot()


def show(df: pd.DataFrame, title: str = "") -> None:
    if title:
        print(title)
    print(df.head(10).round(2).to_string())
    print(f"\n--- {len(df)} rows ---")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="spot", help="spot | SYMBOL (e.g. 000001)")
    ap.add_argument("--start", default="20250102")
    ap.add_argument("--end", default="20260722")
    args = ap.parse_args()
    if args.cmd == "spot":
        show(spot(), "=== A-share snapshot ===")
    else:
        show(fetch(args.cmd, args.start, args.end))
