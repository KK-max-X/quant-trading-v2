# 量化交易系统改动记录

## 2026-07-27

### 数据源优化
- **指数数据源切换**: 第一段指数行情优先用 EM API 直连（`push2his.eastmoney.com`，session `trust_env=False`），绕过 akshare 的 `stock_zh_index_daily_tx` 数据延迟问题。回退仍保留 akshare 链路。
- **行业缓存日期动态化**: `_load_industry_cache()` 不再写死 `end_date="20260722"`，改为从复盘日期/回测结束日期动态传入。修复了板块共振结果随日期漂移的问题。
- **第六段选股输出因子扩展**: 新增板块共振（`sector_bonus`）、龙虎榜（`lhb_bonus`）、K 线形态（`pattern`）三列。排序改为量比 + 板块×2 + 龙虎×3 综合权重。

### 筛选逻辑统一
- **涨停日免测量能已取消**（`daily_review.py` 和 `strategy.py` 已对齐）: 近3日逐天严格检查量比>=2x，涨停日不再豁免。
- **`strategy.py` 量能过滤器同步**: `filter_volume_boost` 与 `daily_review.py` 的 `volume_boost_sustained` 逻辑一致。

### 第七段预判增强
- **第三段跌幅榜补资金列**: 跌幅 TOP10 加 `净流入(亿)` 列。
- **资金日环比三级回退**:
  1. EM 主力净流入日环比（EM API 正常时）
  2. 全市场成交额环比（读取 `turnover_cache.txt` 昨日数据，同花顺源）
  3. 上证成交量缩放（`stock_zh_index_daily` 缓存数据）
- **全市场成交额文件缓存**: `turnover_cache.txt` 存储每日 `YYYYMMDD,total_turnover`，供次日对比。
- **主力资金 THS 回退单位修正**: `/10000` 除法 bug 已修复，现在显示真实亿级数值。

### 新增模块
- **`ths_level2.py`**: 同花顺 Level-2 数据抓取框架（pywinauto），支持 `inspect`（侦查 UI 控件树）、`quote`（盘口）、`tick`（逐笔）三个子命令。需同花顺客户端运行中。

### 已知限制
- **akshare 指数数据延迟**: 周末/假期后首个交易日，`stock_zh_index_daily` 系列可能未及时更新。优先用 EM API 已缓解。
- **EM API 代理不稳定**: `stock_market_fund_flow`、`stock_hot_rank_em` 等依赖东方财富 API 的接口可能因代理问题失败，此时自动回退到同花顺数据源（第七段预判）或跳过（第四段个股热度）。
