---
name: 今日复盘2.0
description: A股每日复盘工具 v2 —— 在1.0基础上新增风控模块（仓位管理+动态止损+市场择时）和扩展因子库（RSI/MACD/布林带/换手率异常/ATR），整合freqtrade+vnpy+QUANTAXIS开源库的设计思想。盘后15:00运行。
---

# 今日复盘2.0

A股每日复盘工具第二代。在1.0的7段式复盘基础上，新增第八段风控建议和扩展因子库。

## 新增功能

### 风控模块 (risk_manager.py)
- **仓位计算**: 凯利公式 + 等风险仓位法
- **动态止损**: 硬止损(5%) + 移动止损(3%) + MA动态止损 + 时间止损(5天)
- **市场择时**: 根据第七段预判评分自动调整仓位乘数
- **资金曲线**: 收益/回撤/交易次数跟踪

### 扩展因子 (factors.py)
- RSI(14) 相对强弱
- MACD(12/26/9) 金叉死叉
- 布林带(20,2) 支撑压力
- 换手率异常检测
- 量价背离
- ATR(14) 平均真实波幅

### 第八段: 风控建议
根据市场评分 + 选股风险因子自动输出:
- 仓位建议 (看多/偏空对应不同仓位)
- 每只选股的个体风险提示
- 大盘风控建议

## 与1.0的关系
1.0 和 2.0 独立运行，互不影响。1.0保留原有功能不修改。

## 用法

```bash
python scripts/daily_review.py [日期] [--no-screen]
```

## 脚本

| 文件 | 说明 |
|---|---|
| daily_review.py | 8段式每日复盘 |
| strategy.py | 5日波段策略 |
| risk_manager.py | 风控模块 |
| factors.py | 扩展因子库 |
| scan.py | 原始扫描器 |
| stock_data.py | 个股数据 |


## 参考开源项目

| 项目 | Stars | 引用技术 |
|---|---|---|
| vnpy/vnpy | 44k | 风控模块 + 事件驱动架构 |
| freqtrade/freqtrade | 53k | 动态止损 + 策略API设计 |
| QUANTAXIS/QUANTAXIS | 11k | 因子计算库 |
| microsoft/qlib | 47k | AI因子挖掘 |
| akshare/akshare | 22k | 数据源 |
| tickflow-stock-panel | 2.4k | TickFlow资金流向可视化 |
| KHunter | 336 | 涨停板策略回测框架 |
| PureSaber/a-share-multifactor | - | 多因子IC分析 + 分位数回测 |

## 开发路线图

### v2.1 (计划中)
- [ ] 多因子IC分析 (借鉴 PureSaber/a-share-multifactor)
- [ ] 涨停板专项回测 (借鉴 KHunter)
- [ ] TickFlow 资金流向可视化

### v2.2 (计划中)
- [ ] Qlib AI因子集成
- [ ] Web Dashboard