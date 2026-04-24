# Trading System B

这是算法交易项目里 B 部分的最小可用后端实现，目标是把 A 部分产出的信号接成一套可执行、可存储、可给前端读取的基础设施。

## 包含内容

- `database/schema.sql`
  - SQLite 表结构，覆盖行情、信号、订单、成交、持仓、账户净值、系统日志
- `trading_system_b/data_import.py`
  - 导入你们压缩包里的 `*_timing_result.csv`
- `trading_system_b/a_strategy_bridge.py`
  - 把 A 部分 `QuietBigBuyPlusFollowedBigSell30s` 的 Excel 回测结果桥接到后端
- `trading_system_b/execution.py`
  - 模拟下单、撤单、成交、持仓更新、净值更新
- `scripts/init_db.py`
  - 初始化数据库
- `scripts/import_project_data.py`
  - 导入现有多维择时结果
- `scripts/import_a_strategy.py`
  - 导入 A 部分策略回测结果
- `scripts/run_a_strategy_flow.py`
  - 导入 A 策略信号并按最新信号自动下单
- `scripts/run_demo.py`
  - 跑一遍初始化 + 导入 + 模拟成交示例

## 目录约定

- 原始数据目录：`data/raw/multi_timing_project`
- SQLite 数据库：`data/processed/trading_system_b.sqlite3`

## 快速开始

```bash
cd "/Users/wangty/算法交易/trading_system_b"
python3 scripts/init_db.py
python3 scripts/import_project_data.py
python3 scripts/run_demo.py
```

如果你要跑 A -> B 的完整链路：

```bash
cd "/Users/wangty/算法交易/trading_system_b"
python3 scripts/import_a_strategy.py
python3 scripts/run_a_strategy_flow.py
```

## 当前实现假设

- 交易标的默认统一映射到 `000985.SH`，便于先把整套链路跑通
- 行情优先使用各策略结果里的 `close/open` 列
- 执行引擎是模拟盘，不接实盘 API
- 成本模型包含手续费和滑点
- A 部分策略当前桥接为一个可交易的“多空组合资产” `QBPFBS30S_LS`

## 建议你在答辩时这样描述 B 部分

- 我们先用 SQLite 做轻量级落库，便于演示和前后端联调
- 把各子策略产出的日频信号统一导入 `strategy_signal`
- 用模拟执行引擎承接策略信号，生成订单、成交、持仓和净值
- GUI 端后续直接读 `orders / positions / account_nav / system_log`
