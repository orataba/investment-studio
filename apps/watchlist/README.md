# Portfolio Operations Workbench Watchlist

这是当前 `Portfolio Operations Workbench / Watchlist` app 的工作区。

当前后端主语已经统一到 `instrument`，当前已发布主路径支持 `fund`、`etf` 与 `index` 三类本地 watchlist/detail 工作面。

当前 app 已经包含：

- `backend/`
  FastAPI + SQLAlchemy + Alembic 的后端实现，默认目标数据库为 PostgreSQL
- `frontend/`
  React + Vite + TypeScript 的终端前端
- `docs/`
  当前工程基线、数据模型和产品框架文档

## 当前范围

当前主路径已经落到下面这条链路上：

1. `Watchlists`
2. `Instrument Detail`
3. `Overview / Quote / Performance / Risk / People / Strategy / Documents / Research / Monitoring`
4. `Canonical quote resolution / manual profile / recalc / read model`

其中：

- `Watchlists` 与单资产详情页是当前主界面
- Watchlist 里的资产新增只允许从 `Database Dashboard` 共享库搜索并引用，不再在 Watchlist 内创建资产主档
- 当前 watchlist 可用范围是 `fund`、`etf` 与 `index`；shared registry 可以管理更广的资产类型，但其他类型不会进入 watchlist detail 主链路
- `index` 使用轻量详情工作面，当前聚焦 `Overview / Performance / Risk`，不强行复用 fund-specific 的 people、strategy、documents 或 research 录入面。`close` 只可用于 chart/trading 等允许它的 role；没有真正 total-return 序列时，收益、回撤、波动率和 Sharpe 必须显示不可用
- `Monitoring` 已经是可用工作面；`Research / Documents` 一级路由仍以轻量页为主
- Copilot 后端接口仍保留为后续扩展入口，但当前 UI 默认隐藏，不作为已发布能力

## 目录结构

```text
.
├── backend
│   ├── alembic
│   ├── watchlist_app
│   │   ├── api
│   │   ├── db
│   │   ├── repositories
│   │   └── services
│   └── tests
├── docs
└── frontend
    └── src
```

## 快速启动

以下命令默认从仓库根目录执行；如果已经在 `apps/watchlist` 目录，可相应省略路径前缀。

### 1. 后端

```bash
cd apps/watchlist/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
PORTFOLIO_OPS_MIGRATION_EXPECTED_DATABASE=portfolio_ops PYTHONPATH=. alembic upgrade head
uvicorn watchlist_app.main:app --reload --host 127.0.0.1 --port 8000
```

说明：

- 运行时数据库连接以 `PORTFOLIO_OPS_WATCHLIST_DATABASE_URL` 为准；如迁移需要单独连接，可设置 `PORTFOLIO_OPS_WATCHLIST_ALEMBIC_DATABASE_URL`
- schema 以 `PORTFOLIO_OPS_WATCHLIST_DATABASE_SCHEMA` 为准
- settings 默认值仍指向本地 PostgreSQL，但实际运行应以你当前 `.env` / shell 环境为准
- 数据库结构只通过 `alembic upgrade head` 管理；如果要清空旧数据，使用仓库根目录的 `./infra/postgres/rebuild_local_schemas.sh`
- FastAPI 根路径会重定向到前端 `watchlists`
- backend 顶层包名现在是 `watchlist_app`

### 2. 前端

```bash
cd apps/watchlist/frontend
npm install
npm run dev
```

说明：

- Vite 默认仅监听 `127.0.0.1:5173`；如需远程访问，请通过受控的反向代理显式开放
- `/api` 已代理到 `http://127.0.0.1:8000`

## 常用校验命令

```bash
npm --prefix apps/watchlist/frontend run build
(cd apps/watchlist/frontend && npx tsc --noEmit)
(cd apps/watchlist/backend && pytest)
```

## 当前行为边界

- Watchlist 主表当前按页加载，默认每页 `50` 行；页面上的 `Download` 会导出当前筛选/排序结果的全量行，而不是只导出当前页
- Watchlist 顶部 view / Data & Columns / Group By / Filter / Download / row action controls 保持本 app 自己的实现和 class，但视觉 contract 与 Portfolio toolbar controls 一致，不引入跨 app 组件依赖
- Data & Columns 中 `Name` 是默认锁定列，不作为可选字段重复展示；应用 view columns 时必须去重并保留 `instrument_name` 为第一列
- Group By 后只允许对明确可写分组拖动 instrument：fund taxonomy 和离散 custom attribute。拖放需要调用对应后端写接口同步，不对 read-model / score / bucket 等只读分组做错误兼容
- Watchlist filter 菜单会基于当前 watchlist 的全量行构建选项，不再只采样前几页；当前页执行 add / delete / move 后，filter 选项也会随之刷新
- Watchlist 的 `move` / `copy` 只允许操作 source watchlist 里已经存在的资产，不再把这两个接口当成隐式 `add`
- 自定义 view 会把展示名称映射成 path-safe 的 slug id；复制 watchlist 时也会清洗 legacy custom view id，避免把不可路由的旧 id 继续扩散
- Watchlist 和 Instrument Detail 已改成三层产品框架：`Fund Taxonomy / Research Tags / Monitoring Assessment`；详情页入口调整为 `Overview`，把基础信息和产品 taxonomy 放到第一屏
- `Peer Category` 仍然保留为外部同类比较口径；内部基金分类已经独立成 fund taxonomy tree，二者不再混用
- Peer ranking 只在目标和候选资产具备成对的 performance/risk snapshot，且 `as_of`、方法版本、解析频率与 canonical 输入水位完全对齐时发布。Cohort 成员、taxonomy assignment 和成员输入哈希共同形成 fingerprint；任一成员变化时，所有仍指向旧 fingerprint 的同类结果会在同一事务中转为 `cohort_stale` 并清空排名/分位数，直到各成员重算收敛到同一代。不允许展示半新半旧的 peer cohort。
- fund 分类不再依赖固定 `fund_category_l1/l2/l3`；当前已经落成 `fund taxonomy tree + derived taxonomy levels`，支持可变深度路径和按层级 group by
- taxonomy 不做自动推断或 migration 自动回填；默认未分类，由人在详情页 `Overview -> Fund Taxonomy` 明确选择
- Monitoring 的缺失项检查已经改成 taxonomy-aware；不同分类叶子只检查适用的 label，不再全 fund 共用一套静态 tag 清单
- 示例基金标签值不再在 migration 或 add-to-watchlist 运行时自动注入；产品框架赋值只来自显式录入和后续真实数据链路
- 后端主语已经统一到 `instrument`，当前只暴露 `/api/instruments/...` 明确接口；旧 `/api/funds/...` 兼容路由已移除
- Instrument Detail 里的 canonical quote/NAV history 现在是只读视图；导入、编辑、刷新共享行情/净值要去 `Database Dashboard`，这里只保留 benchmark / peer comparison 设置，不再允许人为选择计算 basis。派生层必须保留真实 `metric_family / quote_basis / role`，不能把 `close`、`official_nav`、`total_return_nav` 混成一个无来源的 NAV 字段。
- Instrument Detail 的 benchmark 选择在 Quote / Performance / Risk 三个工作面共用同一状态；Performance matrix 和 Risk rolling charts 使用同一 benchmark calculation series，不再维护第二套 metric benchmark。Quote / Performance 图表在比较 benchmark 时只绘制双方重叠日期窗口，并按真实日期比例投影横轴，不按样本序号拉伸。Rolling risk chart 支持 1M / 3M / 6M / 12M / 24M / 36M 窗口；benchmark 曲线只在存在重叠 calculation series 时展示，不补齐缺失序列。
- Watchlist 的 Quote / Performance / Risk 计算只调用当前 SQLAlchemy Session 内的 canonical resolver；不解析共享 registry JSON，也没有本地序列 fallback。重复的本地 `nav_fact` 表、仓储和 `/api/facts/.../nav` API 已完整移除。Chart 与 total-return 分别解析各自 role，二者不得互相替代。
- Read model 与 performance/risk snapshot 使用统一、可空的 `market_data_input_watermark_at`，其值只来自同一 SQLAlchemy Session 中 canonical instrument 的 `market_data_updated_at`，表示本次计算开始时可读的行情输入水位。缺失或无法解析时明确返回 `unknown`，绝不回退成当前时间；精确 revision/input hash/dependency fingerprint 才是计算权威。`last_recalculated_at` 是尝试时间，`calculated_at` 是快照成功时间，三者不能混用。
- 所有公开计算入口都使用明确的 `valuation_date`。Watchlist consumer policy 只按 canonical `instrument_type` 分类：fund 允许最多 45 个日历日的周期性净值 carry-forward，其他类型允许最多 5 个日历日；不从名称、taxonomy、identifier 或 quote shape 猜类型。Chart 与 total-return 分别应用同一 profile，但各自独立解析 role。
- Chart read model 只用 `chart` role 的 endpoint 和 revision lineage 判定自身新鲜度；不使用 total-return summary 代判。最新点只能在 chart endpoint 已解析且 observation date 精确一致时覆盖，total-return、stale 或错日点都不能冒充 chart current endpoint。
- 超出 freshness 阈值不会删除 last-good performance/risk snapshot。系统保留历史分析及原 snapshot id/input hash/calculated_at，同时把 current/trailing 指标置空并用稳定 reason codes 标记 `stale`；真正 missing/ambiguous 的 current endpoint 为 `unavailable`，不会伪装成 stale。只有 performance/risk 快照成对存在时，`last_successful_snapshot_at` 才取两类成功时间中的较早者；单边损坏状态返回空，也不使用重算尝试时间。
- migration `20260713_0028` 对旧水位列做逐值无损语义改名；`20260713_0029` 只允许在重复 `nav_fact` 表为零行时删除它；`20260713_0030` 只在 `watchlist_row_read_model.aum` 全为空时删除这个没有 currency / as-of / source lineage 的金额字段，同时清理 field registry 和持久化 view 引用。三者都不通过 downgrade 复活已废弃边界；回滚必须恢复 migration 前备份。
- Watchlist 当前不发布 AUM。未来若接入，必须新建显式的 amount、currency、as-of date、source/revision lineage 事实模型，并定义 share-class / fund / manager 的统计 grain；不能恢复一个裸数值列，也不能由前端假设 USD。
- `partial / rejected / withdrawn` observation 会由只读 `nav-series` API 连同 observation/revision/payload/source 血缘完整返回并进入 dependency fingerprint，但绝不进入收益或风险计算。summary/chart/performance/risk read model 只保存 series/policy、质量状态、reason codes、首末日期、计数和 fingerprint 等有界摘要，不复制 observations、points 或逐 revision id/hash 数组；新鲜度依靠该权威 fingerprint 判断，不通过 `max(ingested_at)` 之类的时间水位猜测。
- `return_ytd / return_mtd / return_1w / return_1m / return_1y / annualized_return / return_3y / return_5y / max_drawdown / current_drawdown / volatility / sharpe_ratio` 当前对 fund、ETF 与 index 都可见，但只有 canonical `total_return` role 成功解析时才有数值；`close` 或 `official_nav` 不能冒充 total return。read model 会记录实际选中的 quote basis、质量状态与计算依赖。
- 基金研究评级是 `Research` 中的人工、版本化判断。评级保存要求依据、分析人、置信度、生效日和复核日；每次保存追加 revision，Research 页同时展示 current rating 和完整修订历史。行情、业绩和风险重算不会生成或改写评级。量化同类比较只作为证据展示。

## 当前文档

- [../../docs/README.md](../../docs/README.md)
  仓库级文档入口，包含数据库工作流和平台边界说明。
- [../../docs/FRONTEND_DESIGN_BASELINE.md](../../docs/FRONTEND_DESIGN_BASELINE.md)
  当前前端视觉基线，约束白底数据终端、tabs 节奏和 Watchlist / Portfolio 的内容区一致性。
- [docs/INDEX.md](./docs/INDEX.md)
- [docs/CURRENT_SYSTEM_BASELINE.md](./docs/CURRENT_SYSTEM_BASELINE.md)
- [docs/FUND_PRODUCT_FRAMEWORK.md](./docs/FUND_PRODUCT_FRAMEWORK.md)
- [docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md](./docs/FUND_TERMINAL_V2_DATA_MODEL_AND_API.md)
- [docs/FUND_TERMINAL_V2_AI_COPILOT.md](./docs/FUND_TERMINAL_V2_AI_COPILOT.md)

## 继续推进时的原则

- 以当前仓库实现为准，不保留旧方案讨论稿
- 文档优先描述已经存在的结构、接口和下一步真实缺口
- 新需求先落到当前系统基线，再决定是否扩展数据模型或页面骨架
