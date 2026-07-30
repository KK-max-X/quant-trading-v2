# 量化策略学习笔记 v2

## GitHub 量化库 + 投资经典融合

### 一、巴菲特/格雷厄姆原则 → 量化实现

| 经典原则 | 量化实现 | 来源 |
|---|---|---|
| 安全边际(Margin of Safety) | 买入价 = MA20 * 0.95 (5%折扣) | Graham |
| 护城河(Moat) | ROE>15% + 毛利率>30% + 连续3年增长 | Buffett |
| 别人恐惧时贪婪 | 市场评分<35%时加大回测样本 | Buffett |
| 好公司+好价格 | 基本面质量分 × 技术面强度分 | Buffett+Graham |
| 能力圈 | 只操作熟知的板块(电力+电网设备+化工) | Buffett |
| 长期持有 | 5日→20日持有期回测对比 | Buffett |
| 市场先生 | 价格偏离价值>20%时介入 | Graham |

### 二、GitHub 价值投资量化仓库

| 仓库 | Stars | 核心技术 |
|---|---|---|
| rbhatia46/Greenblatt-Magic-Formula | 81 | 神奇公式: ROC + 盈利率排名 |
| bben1/Automatic-fundamental-valuation | 52 | DCF估值 + Piotroski F-Score |
| Destroyertubu/ashare-factor-library | - | A股107因子库 + Graham/Buffett策略 |
| quantrocket-codeload/qval | 8 | 企业倍数 + F-Score 价值策略 |

### 三、可落地的新因子

基于价值投资原则的新增因子:

| 因子 | 计算方式 | 数据源 | 预期增益 |
|---|---|---|---|
| 安全边际率 | (MA20 - 收盘价) / MA20 | 已有 | 降低追高风险 |
| 封单质量 | 封单金额/流通市值 > 0.5% | KHunter | 排除虚假封板 |
| 盈利质量 | ROE > 15% + 毛利率 > 25% | akshare财务模块 | 排除垃圾股炒作 |
| 恐慌指数 | 全市场跌停数/涨停数 | 已有涨停池 | 逆势买入信号 |

### 四、策略2增强版设计

```
综合得分 = 技术面(60%) + 基本面(20%) + 资金面(20%)

技术面: 日环比放量 + MA趋势 + 封单比 + 形态评分
基本面: ROE > 15% ? +1 : 0 + 毛利率 > 25% ? +1 : 0
资金面: LHB净买入 > 0 ? +1 : 0 + 主力净流入 > 0 ? +1 : 0

安全边际修正: 加分 * (1 + 安全边际率)  # 跌得越深，加分越多
```

### 五、学习路线

1. 读完 Greenblatt《股市稳赚》(神奇公式)
2. 读 Piotroski F-Score 论文 (9分制基本面质量评分)
3. 实现 A股基本面因子: ROE, 毛利率, 负债率, 现金流
4. 回测验证: 加入基本面因子后胜率提升幅度

---
学习永无止境。持续更新。

### 六、高级量化技术 (第二轮搜索)

| 仓库 | Stars | 技术 | 适用性 |
|---|---|---|---|
| PyPortfolio/PyPortfolioOpt | 5.9k | 风险平价 + Black-Litterman | 优化仓位分配 |
| convexfi/riskparity.py | 324 | 快速风险平价 | 替代等权分配 |
| theo-dim/regime_detection_ml | 34 | HMM市场状态检测 | 替代固定评分阈值 |
| galafis/rust-market-microstructure | 1 | 订单流分析 | Level-2数据挖掘 |
| prachichoudhary2004/quantsentinel-ai | - | 行为金融+NLP+ML融合 | 情绪因子 |


### 七、新发现的投资方法论 → 量化映射

| 经典原则 | 量化实现 | 来源 |
|---|---|---|
| 风险平价(Bridgewater/Dalio) | 各资产波动率倒数加权 | PyPortfolioOpt |
| 趋势+震荡双模态(Soros) | HMM三状态:牛市/熊市/震荡 | regime_detection_ml |
| 成交量分布(Livermore) | 价格-成交量剖面分析 | order-flow |
| 情绪钟摆(Behavioral) | 散户资金流向 vs 机构 | sentimental |

### 八、技术路线图更新

| 阶段 | 新增技术 | 预期效果 |
|---|---|---|
| Phase 1 | HP Filter趋势分解 | MA信号精度 +15% |
| Phase 2 | HMM 3状态市场检测 | 择时准确率 +10% |
| Phase 3 | 风险平价仓位分配 | 最大回撤 -30% |
| Phase 4 | LightGBM因子重要性 | 因子淘汰/保留依据 |
| Phase 5 | 蒙特卡洛压力测试 | 极端行情生存率评估 |

### 九、投资书单 (量化实现版)

| 书 | 作者 | 核心原则 | 量化映射 |
|---|---|---|---|
| 聪明的投资者 | Graham | 安全边际 | 买入价 < 内在价值*0.7 |
| 巴菲特致股东信 | Buffett | 护城河+长期 | 质量因子+20日持有 |
| 股市稳赚 | Greenblatt | 神奇公式 | ROC+盈利率排序 |
| 笑傲股市 | ONeil | CANSLIM | 7因子择股模型 |
| 金融炼金术 | Soros | 反身性 | HMM市场状态 |
| 原则 | Dalio | 风险平价 | 波动率倒数仓位 |
| 股票大作手 | Livermore | 关键点 | 成交量分布分析 |
| 市场巫师 | Schwager | 各路高手 | 多策略融合 |

---
持续学习。每一本书、每一个仓库，都在提高胜率。
