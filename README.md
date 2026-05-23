# PolySignal — 新闻驱动的 Polymarket 交易信号

Python 异步管线：实时新闻 → Claude 方向分类 → 偏差检测 → 自动下单。

跟 [Go 版链上监听器](https://github.com/chengchengliai-hue/polygon-smart-money-listener) 互补——那条管线听链上地址，这条管线听新闻。

## 架构

```
News Stream (Twitter/Telegram/RSS)
  ↓ <1秒
Market Matcher (关键词匹配冷门市场)
  ↓ <2秒
Claude Classifier (bullish/bearish/neutral + 置信度)
  ↓ <1秒
Edge Detector (Claude vs 市场当前价格 + Kelly 仓位计算)
  ↓
Executor (Polymarket CLOB + Telegram)
```

延迟: 5-7秒从新闻出现到交易执行。

## 安装

```bash
git clone <this-repo>
cd poly-signal
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填 API keys
```

## 运行

```bash
# 模拟盘
python -m src.cli

# 实盘
python -m src.cli --live
```

## 文件

```
src/
├── cli.py          # CLI 入口
├── config.py       # 配置
├── pipeline.py     # 主管线
├── news_stream.py  # Twitter/Telegram/RSS
├── markets.py      # Polymarket 市场获取
├── classifier.py   # Claude 分类
├── edge.py         # 偏差检测 + Kelly
└── executor.py     # CLOB 交易 + Telegram + SQLite
```

## 策略

- 只盯流动性 $1K-$500K 的冷门市场（大市场有毫秒机器人打不过）
- Claude 做方向分类（bullish/neutral），不估计概率
- Materiality >= 0.6 才触发
- Edge >= 0.10 才下单
- Quarter-Kelly 仓位（安全）
- 每日亏损上限 $100
