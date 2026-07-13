# Poly Signal v2 — Polymarket 聪明钱监控+跟单

## 一、文件总览

| 文件 | 职责 |
|---|---|
| `main.py` | 入口，启动所有线程 |
| `config.py` | 环境变量→常量 |
| `.env` | 密钥+配置 |
| `poller.py` | Data API 轮询交易+新钱包检测 |
| `handler.py` | 核心判定逻辑：评分→告警→跟单→出场 |
| `relations.py` | 政策市场主题、命题序列、方向一致性与关联聚合 |
| `trader.py` | CLOB SDK 下单（买/卖） |
| `bot.py` | Telegram Bot 收发消息 |
| `settle.py` | 结算扫描（每10分钟） |
| `db.py` | SQLite 全部数据操作 |
| `order_server.py` | 独立 HTTP Server（Go可调用，当前未用） |
| `requirements.txt` | 仅两个依赖 |

---

## 二、架构图

```
main.py
  ├─ gc_loop(daemon)     每5分钟清理 seen_events 旧记录
  ├─ settlement_loop(daemon)  每10分钟扫已结算市场
  ├─ poll_bot(daemon)    Telegram长轮询
  ├─ poll_trades(主线程)  10秒轮询 Data API
  │    └─ handle_trade(trade, wallet, notional, age_hours, direction)
  │         ├─ 去重: is_seen(tx_hash)
  │         ├─ SELL → 检查是否为跟踪钱包 → sell_position → Telegram告警
  │         ├─ BUY → 评分 → save_alert → format_alert → send_message(TG)
  │         └─ BUY → copy_trade_buy(CLOB SDK FOK) → save_position
  └─ handle_bot_update    Telegram命令/callback处理
       ├─ /smart_money → 最近5条预警
       ├─ /positions → 活跃跟单仓位
       ├─ /copy_stats → 跟单统计
       ├─ /stop → 停止所有跟单
       └─ 回调: t|id(跟踪) / u|id(取消)
```

---

## 三、密钥与配置

```env
POLYMARKET_PRIVATE_KEY=<wallet-private-key>
POLYMARKET_PROXY=<polymarket-proxy-address>
POLYMARKET_DATA_API_KEY=<data-api-key>
BOT_TOKEN=<telegram-bot-token>
CHAT_ID=<telegram-chat-id>
MIN_TRADE_USDC=2000
COPY_TRADE_AMOUNT=5
COPY_TRADE_BOOST=10
RELATED_WINDOW_MINUTES=30
RELATED_MARKET_BONUS=10
RELATED_MULTI_WALLET_BONUS=20
SQLITE_PATH=data/signal.db
```

`config.py` 硬编码常量:

```python
CLOB_URL = "https://clob.polymarket.com"
CHAIN_ID = 137
```

**说明：**
- `PRIVATE_KEY` — Polymarket 钱包私钥，用于 CLOB SDK 签名
- `PROXY` — Polymarket 代理合约地址（signature_type=1 模式下 funder 参数）
- `DATA_API_KEY` — Polymarket Data API Key
- `BOT_TOKEN` / `CHAT_ID` — Telegram Bot
- `COPY_TRADE_AMOUNT=5` / `COPY_TRADE_BOOST=10` — 普通虚拟跟单 $5，24 小时内临期跟单 $10

---

## 四、各模块详细逻辑

### 1. poller.py — 数据源

```
Data API (data-api.polymarket.com)
  ├─ fetch_trades(limit=1000)    每10秒拉最近交易
  ├─ recent trade cache          成功处理/明确过滤后，内存保留最近10万个交易哈希
  ├─ check_new_wallet(addr)      查 /activity → <5笔 AND <48h → True
  ├─ test_heavy_profile(...)     识别3-50笔小额BUY测试后突然重仓
  ├─ pending confirmations       BUY候选等待45秒后重新检查钱包活动
  ├─ has_active_position(addr, condition_id)  查 /positions
  ├─ resolve_clob_tokens(condition_id)        查 CLOB /markets/{id} → {YES: token_id, NO: token_id}
  ├─ fetch_fpmm_price(token_id)              查 CLOB /markets 列表匹配价格
  ├─ fetch_best_price(token_id, BUY)         读取卖一价（虚拟买入）
  ├─ fetch_best_price(token_id, SELL)        读取买一价（虚拟卖出）
  └─ fetch_fpmm_by_condition(condition_id, outcome)  查 CLOB /markets/{id} 按outcome匹配
```

**poll_trades 主循环筛选链路：**

```python
while True:
    trades = fetch_trades()        # Data API /trades
    for t in trades:
        1. side != "BUY" → skip
        2. notional = price × size < $2000 → skip
        3. proxyWallet 为空 → skip
        4. transactionHash 为空 → skip
        5. 查询 Data API /activity（最多100条）
           ├─ 活动<5笔 AND age<48h → 新钱包通道
           └─ 否则检查小额测试重仓通道
              ├─ 当前BUY > $2000
              ├─ 排除当前交易并按 tx+market+asset 合并历史BUY
              ├─ 3 <= 历史合并BUY订单数 <= 50
              └─ 当前金额 >= 历史BUY中位数 × 10
                 → 标签“小额测试重仓”
           两个通道均不满足 → skip

交易只在成功处理或明确不符合条件后写入内存缓存。钱包 API 超时、
handler 返回失败或单笔处理抛出异常时不缓存，并在后续轮询中重试；
单笔异常不会中断同批其他交易。

BUY候选进入非阻塞待确认队列，至少等待45秒后重新查询 activity：

- 同一 condition+asset 的 SELL 数量达到确认窗口内 BUY 的90% → 快速往返，过滤；
- 否则重新判断新钱包条件；
- 不再属于新钱包时，重新计算3-50笔历史BUY及10倍中位数条件；
- 重新确认通过后才发送Telegram并执行跟单。
```

### 2. handler.py — 核心判定

```python
handle_trade(t, wallet, notional, age_hours, direction, wallet_signal_tag=""):
  # ── 去重 ──
  if is_seen(tx_hash): return
  mark_seen(tx_hash)

  # ── SELL 出场检测 ──
  if side == "SELL":
      for pos in get_active_positions():
          if wallet+market_slug+outcome 匹配:
              sell_position(clob_token_id, shares, title)
              send_message(format_exit(...))
              mark_position_closed(pos_id)
      return

  # ── BUY 评分 ──
  score = 50
  score += 6h内新钱包(+10) 或 小额测试重仓(+10)，两者不叠加
  score += outcome参考价<0.1(+10)
  # 交易金额不加分
  score += 临期加分 + 政治/政策事件加分
  score += 关联市场同向加分
  score = min(score, 100)

  # ── 告警 ──
  save_alert(...)
  format_alert(wallet, title, outcome, ...) → send_message(TG)

  # ── 跟单 ──
  reference_price = fetch_fpmm_by_condition(condition_id, outcome)
  execution_price = fetch_best_price(token_id, "BUY")  # 卖一价
  if execution_price <= 0 or execution_price >= 1: skip
  if execution_price > 0.95: skip

  trade_amount = COPY_TRADE_AMOUNT
  if 0 <= hours_to_end < 24:       # 临期加码
      trade_amount = COPY_TRADE_BOOST

  shares = trade_amount / execution_price
  if shares < 5:                   # 最低5股
      trade_amount = fpmm * 5 * 1.05
      shares = trade_amount / fpmm

  result = copy_trade_buy(clob_token_id, condition_id, outcome,
                           "", market_slug, title, trade_amount,
                           score, "smart_money", execution_price)
  if result.success:
      save_or_add_position(..., entry_price=result.fillPrice, ...)
      save_copy_trade_entry(...)
      update_alert_copy_decision(..., "copied")
```

### 2.1 relations.py — 关联政策市场

每个市场会规范化为：

```text
policy_family  政策类型，如 tariff / sanction / military
topic_key      跨 Event 主题，如 tariff:china:us
series_key     同一 Event 内去除日期后的命题序列
stance         support / oppose
```

Gamma Event 中相同命题但不同截止日期的市场共享 `series_key`。跨 Event
只有政策类型和关键实体同时匹配时才共享 `topic_key`；候选人选举等互斥
市场不会仅因属于同一个 Event 就被认定为同向。

在 `RELATED_WINDOW_MINUTES` 时间窗内，至少两个不同 condition 的交易方向
一致度达到 80% 时增加关联市场分；来自至少两个不同钱包时使用更高加分。
图谱节点写入 `market_topics`，每次信号的聚合快照写入 `related_signals`。

### 3. trader.py — CLOB 下单

```python
# 连接初始化
temp = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID)
creds = temp.create_or_derive_api_key()     # 自动派生API凭证
client = ClobClient(CLOB_URL, key=PRIVATE_KEY, chain_id=CHAIN_ID,
                    creds=creds, signature_type=1, funder=PROXY)
# signature_type=1 → EIP-712 POLY_PROXY 类型签名
# funder = 0x5444C4E4... 代理合约

# 下单（FOK = Fill-or-Kill，全成或全取消）
args = MarketOrderArgsV2(token_id=token_id, amount=amount, side='BUY')
resp = client.create_and_post_market_order(args, order_type='FOK')

# 卖仓
args = MarketOrderArgsV2(token_id=token_id, amount=shares, side='SELL')
resp = client.create_and_post_market_order(args, order_type='FOK')
```

### 4. bot.py — Telegram 交互

```
poll_bot(callback):
  while True:
    GET /getUpdates?offset={last+1}&timeout=10
    for update in result:
      callback(update)  → handle_bot_update
    sleep(2)
```

**命令列表：**

| 命令 | 别名 | 功能 |
|---|---|---|
| `/start` | - | 显示帮助 |
| `/smart_money` | `/sm` | 最近5条聪明钱预警 |
| `/positions` | `/pos` | 活跃跟单仓位列表 |
| `/copy_stats` | `/stats` | 总单数/盈亏/胜率统计 |
| `/stop` | - | 停止所有跟单（全部 mark closed） |

**按钮回调：**

| 回调数据 | 功能 |
|---|---|
| `t|{track_id}` | 跟踪钱包 → add_tracked() |
| `u|{pos_id}` | 取消跟踪 → mark_position_closed() |

**告警消息格式：**

```
🔴 聪明钱预警 — 90分

钱包: 0x1234...abcd
市场: Will United States win on 2026-06-12?
金额: $2,500  |  看多 YES
标签: 原生发现(+70) · 大额定向(+20) · 数小时内新建(+15)

[🔍 钱包] [📊 持仓]
[👁 跟踪]
```

### 5. settle.py — 结算

每10分钟扫描 `positions WHERE status='active'`：

```
CLOB market snapshot(condition_id)
  ├─ 仅 closed=true 或存在 winner=true 时允许结算
  ├─ 选中 token 最终为 1 → WIN  → pnl = shares - cost
  ├─ 选中 token 最终为 0 → LOSS → pnl = -cost
  └─ 暂停接单但未关闭 → 不结算

止损：按虚拟卖出可获得的买一价计算跌幅，触发后仍按最新买一价平仓。
```

### 6. db.py — SQLite（5张表）

```sql
-- 去重缓存，30分钟GC
seen_events (
    tx_hash TEXT PRIMARY KEY,
    seen_at TEXT DEFAULT (datetime('now'))
);

-- 预警记录
alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT NOT NULL,
    market_slug TEXT,
    market_question TEXT,
    token_id TEXT,
    outcome TEXT,
    action TEXT,
    direction TEXT,
    notional_usdc REAL,
    score INTEGER,
    source TEXT,
    tags TEXT DEFAULT '[]',
    alerted_at TEXT DEFAULT (datetime('now'))
);

-- 跟单仓位
positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT,
    token_id TEXT,
    outcome TEXT,
    entry_price REAL,
    shares REAL,
    cost REAL,
    alert_score INTEGER,
    alert_source TEXT,
    status TEXT DEFAULT 'active',   -- active | closed
    pnl REAL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

-- 手动跟踪的钱包
tracked (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet TEXT NOT NULL,
    market_slug TEXT NOT NULL,
    market_question TEXT,
    outcome TEXT,
    amount REAL,
    score INTEGER,
    status TEXT DEFAULT 'tracking',
    created_at TEXT DEFAULT (datetime('now'))
);

-- KV 存储（当前未使用）
runtime (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

### 7. order_server.py（遗留兼容工具，未在主流程中使用）

独立 HTTP Server 监听 `127.0.0.1:8765`：

```
GET /buy?token=...&amount=...    → CLOB FOK BUY
GET /sell?token=...&shares=...   → CLOB FOK SELL
GET /health                       → {"ok": true}
```

Python 主流程直接使用 `trader.py`，systemd 不启动该 HTTP Server。

---

## 五、服务器信息

```
服务器:  阿里云韩国 2核2G Ubuntu
IP:      43.108.37.104
SSH:     ssh -i ~/.ssh/id_ed25519 root@43.108.37.104
路径:    /opt/poly-signal-v2/
日志:    /opt/listener/py-signal.log
启动:    systemctl start poly-signal-v2.service
停止:    systemctl stop poly-signal-v2.service
状态:    systemctl status poly-signal-v2.service
```

---

## 六、当前限制

1. 虚拟成交使用买一/卖一，但按当前要求不计算盘口深度和大单滑点。
2. 止损仍使用统一的 `STOP_LOSS_PCT`，后续需要按市场类型回测。
3. 评分最高 100 分，高分信号仍可能饱和；建模时应使用独立特征而非只使用总分。
4. 历史有两条 `positions` 遗留仓位没有对应 `copy_trades`，模型训练应以 `copy_trades` 为准。
5. 已结算样本仍较少，暂不适合直接训练神经网络。
