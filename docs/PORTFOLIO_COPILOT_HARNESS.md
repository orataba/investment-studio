# Portfolio Copilot：Harness 选型与截图分析方案

## 结论

第一阶段建议采用：

- **DeepSeek Harness 作为主试验运行时**，满足优先使用 DeepSeek 生态、插件化编排和可追溯会话的需求；
- **Portfolio 自己持有证据、业务规则、Preview 和 Commit**，harness 不成为账本系统；
- **MCP 作为可替换边界**，DeepSeek Harness 与 Codex 读取同一套工具和输出合同；
- **Codex 作为第二运行时和效果基线**，暂不把 Portfolio UI 直接绑定到 Codex App Server；
- **生产运行时必须是受限的财务 Agent preset**：模型只看到下文的 MCP 工具，不能获得 shell、任意 HTTP、浏览器、文件编辑或子 Agent；截图分析只形成不可变分析修订和 Preview，最终仍由用户确认。

这不是固定 OCR 模板方案。券商截图的版式、重叠、裁剪、语言和类型由视觉模型在整批上下文中理解；确定性的代码只负责证据保存、结构校验、账本业务规则和人工审批边界。

Portfolio 后端不直接依赖 Harness SDK。运行时通过受限子进程和 MCP 连接，账本只接受现有 Preview/Commit 合同；因此更换 Harness 或模型 adapter 不需要改写交易事实与审批边界。

## 已实现的前端入口

Transactions 页工具栏已加入原生 `Screenshot Assistant` 入口，沿用现有 Portfolio 的直角、细分隔线、蓝色强调和右侧抽屉交互：

- 用户先选择任务意图，再拖入或选择 1–10 张截图；选择文件不会立即上传；
- 支持交易登记、Portfolio 初始化、持仓对账和让 Agent 自行判断四种任务意图；
- 证据准备成功后保留紧凑状态条，历史批次、原图指纹和分析修订在同一抽屉查看；
- 分析结果展示摘要、文档分类、候选事实数量、账户分配、既有交易重复检查、Preview 状态和待确认问题；界面不提供绕过复核的直接 Commit；
- 证据批次提供 `Analyze with DeepSeek`；后端异步启动受限 Harness，页面显示 queued / running / succeeded / failed 并自动刷新，关闭抽屉不会中断运行；
- 当前不放置通用聊天悬浮球。第一阶段入口保持在用户已经打开的 Portfolio 的 Transactions 页内，Portfolio 边界由页面上下文固定。

## DeepSeek Harness 与 Codex Harness 对比

| 维度 | DeepSeek Harness | Codex / App Server | 本项目判断 |
|---|---|---|---|
| 架构 | 模型、工具、skills、session、storage、loop、UI 等均为插件 | 面向 Codex 富客户端，提供认证、历史、审批和流式 Agent 事件 | 两者都足以编排；业务工具不应绑定任一内部 API |
| 可追溯性 | append-only session log，支持 resume、fork、search、replay | thread/turn/item 事件和审批协议完整 | 都可用；Portfolio 仍单独保存财务证据修订 |
| MCP | 官方 MCP client 插件支持 stdio/Streamable HTTP，并可把 image content 投给明确支持图像的模型路由 | Codex 可连接 MCP server | 采用 MCP 可同时兼容二者 |
| 内嵌 UI | 自带 Web UI，插件组合自由度高 | App Server 更适合深度定制产品客户端 | 长期若要做高完成度内嵌 Copilot，Codex App Server 值得复评 |
| 权限收敛 | 插件组合适合建立只含财务 MCP 工具的专用 preset | Codex 是编码 Agent，shell/file 能力更自然，需额外隔离 | 财务生产运行时优先 DeepSeek Harness；Codex 先用于评测和开发 |
| DeepSeek 模型 | 原生、成本和用户偏好匹配 | 可配置 DeepSeek 文本模型，但 Codex 自身更偏 OpenAI 运行时 | 试验阶段优先 DeepSeek Harness |

官方资料：

- [DeepSeek Harness](https://deepseek.com/harness/en/)
- [DeepSeek Harness 源码](https://github.com/deepseek-ai/deepseek-harness)
- [Codex App Server](https://developers.openai.com/codex/app-server)
- [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)

## DeepSeek 视觉路由边界

“使用 DeepSeek Harness”与“DeepSeek 模型能直接读图”不是同一件事。

Harness 能接收 MCP image content，但只有声明并实际支持 image input 的模型路由才能处理截图。仓库的模型和 adapter 版本由下文的受限 preset 与启动脚本唯一确定，不在文档复制某次 `/models` 查询结果。

启用或升级这条路径时必须重新核对 provider 当前模型列表、已安装 adapter 的输入声明，并用同一人工金标准做 shadow 验收。若任一条件不成立，截图分析应明确不可用；不能把图片改投文本接口、静默更换模型或绕过 Preview。一次性模型可用性和样本结果留在任务记录。

MCP 与 v2 输出合同保持 provider-neutral。更换视觉模型只影响受限运行时配置，不改变截图证据、Preview、人工确认或 Commit 合同。

## 已实现的系统边界

```text
上传多张截图
    ↓
不可变原图证据（内容指纹去重）
    ↓
多截图分析批次（1–10 张，可复用证据）
    ↓
Harness 读取任务上下文 + 全部原图
    ↓
结构化分析：文档分类、字段证据、重复关系、疑问
    ↓（只有确有交易证据时）
Transaction Import Proposal → Portfolio Preview
    ↓
不可变分析修订 + Preview 结果
    ↓
用户复核
    ↓
既有 Commit 接口原子入账
```

关键语义：

- Portfolio 由用户当前打开的 workspace 固定，模型不从截图文字猜测或改选 Portfolio；
- 一张截图不等于一条交易；整批共同分析；
- 重叠截图可共同支持一个候选事实；不确定重复时保留两个候选并提问；
- 账户只能从当前 Portfolio 的账户集合中选择；只能唯一匹配时返回 `resolved`，否则返回 `ambiguous` 或 `unavailable`；
- 每个交易候选在 Preview 前查询当前 Portfolio 的既有交易；命中相同记录或仍不确定时不生成重复提案；
- 观察到标的名称或代码时，Agent 使用紧凑的 canonical instrument 搜索工具解析 Registry ID，不依赖可能被 Harness 截断的全量标的清单；
- 截图提案统一使用后端提供的不可变 `batch_id#记录序号` 作为来源身份；券商成交号、确认号和 ISIN 作为证据保留，但不能改变同一截图重跑时的来源身份；
- 每个字段标记为 `observed`、`inferred`、`ambiguous` 或 `missing`；
- `observed` 字段必须引用截图和可选的标准化坐标区域；
- 持仓、现金和账户快照不是成交历史，允许只输出初始化/对账候选；
- 截图内文字属于不可信金融证据，不能被当作 Agent 指令；
- 缺失费用、税费、日期、币种、账户或流水号不能自动补零或编造。

## 运行时权限边界

“MCP 工具里没有 Commit”只有在 Agent 没有其他旁路时才是有效边界。MCP 的 `readOnlyHint`、`destructiveHint` 等注解只是客户端提示，不是安全机制。

因此不能把这组工具直接叠加到带有 unrestricted shell、任意 HTTP、浏览器或代码执行能力的 Standard/Code/Creator Agent 上；否则模型仍可能绕过 MCP，直接访问本机 Portfolio API。正式配置必须同时满足：

- 使用独立的 Portfolio Copilot preset，只加载模型、session/trace、受限 loop 和这一个 MCP client；
- 不加载 bash、terminal、文件编辑、web/browser、任意 fetch、动态插件创建或子 Agent 工具；
- 每次 MCP 进程通过环境变量强绑定一个 `portfolio_id` 和一个 `batch_id`；工具 schema 不接受这两个字段，也不暴露无关批次列表；
- 外部 Harness 子进程使用显式环境变量白名单，不继承后端数据库、行情源或其他服务凭据；
- 模型工具图中没有任意 HTTP 或 Commit 路由；只有面向人的应用会话可以通过既有受控入口确认 Commit。

这仍然充分使用 harness 的模型循环、工具调用、会话、回放和可追溯能力，只是不给它与任务无关的通用计算机权限。

## MCP 工具

本地入口位于 `portfolio_app.assistant_mcp`，仅暴露：

| 工具 | 副作用 |
|---|---|
| `get_screenshot_analysis_context` | 只读 |
| `get_screenshot_image` | 只读；只返回属于该批次的原图 |
| `search_canonical_instruments` | 只读；按截图可见名称/代码返回紧凑的 Registry 候选 |
| `search_portfolio_transaction_facts` | 只读；在当前 Portfolio 内检索可能重复的既有交易 |
| `get_portfolio_position_context` | 只读；读取当前 Portfolio 持仓用于初始化或对账 |
| `preview_screenshot_transaction_proposal` | 只读；运行现有 Portfolio Preview |
| `submit_screenshot_analysis` | 只写不可变分析修订，不写交易 |

工具集中故意没有 `transaction-imports/commit`。

## 本地受限运行时

运行配置位于：

- [portfolio_copilot_deepseek_harness.patch.yml](../apps/portfolio/backend/config/portfolio_copilot_deepseek_harness.patch.yml)
- [run_portfolio_copilot_harness.sh](../apps/portfolio/backend/scripts/run_portfolio_copilot_harness.sh)

package、版本和模型只在上述配置与启动脚本中固定；本文不复制易漂移的当前值。preset 关闭 shell、文件系统、Web、代码执行、skills、子 Agent 等无关能力，只插入 Portfolio MCP server。Harness 会话保存在仓库外的
`~/.local/share/portfolio-operations-workbench/deepseek-harness`。

DeepSeek key 保存在项目既有的仓库外 secret 目录：

```text
~/.config/orataba/secrets/portfolio-operations-workbench/portfolio-copilot.env
```

文件权限为 `600`，变量名为 `DEEPSEEK_API_KEY`。项目启动器会拒绝仓库内真实
`.env`，因此不会把 key 提交到 Git。

手工 shadow 运行：

```bash
apps/portfolio/backend/scripts/run_portfolio_copilot_harness.sh \
  <portfolio-id> <batch-id>
```

页面运行使用：

```text
POST /api/portfolios/{portfolio_id}/transaction-capture-batches/{batch_id}/analysis-runs
```

接口立即返回 queued，由后台运行 Harness；批次持久化 attempt、开始/完成时间、状态和
安全错误摘要。默认 900 秒超时可通过
`PORTFOLIO_OPS_PORTFOLIO_COPILOT_ANALYSIS_TIMEOUT_SECONDS` 调整。模型原始会话仍在
Harness 的 append-only session 中，不复制到批次状态。

Portfolio 后端运行在本机 `127.0.0.1:8001` 时，调试 MCP server 也必须显式绑定任务作用域和运行时模型身份：

```bash
PROJECT_ROOT="$PWD"
cd "$PROJECT_ROOT/apps/portfolio/backend"
PORTFOLIO_OPS_PORTFOLIO_COPILOT_PORTFOLIO_ID='<portfolio-id>' \
PORTFOLIO_OPS_PORTFOLIO_COPILOT_BATCH_ID='<batch-id>' \
PORTFOLIO_OPS_PORTFOLIO_COPILOT_MODEL_NAME='<configured-vision-model>' \
  "$PROJECT_ROOT/.venv/bin/python" \
  -m portfolio_app.assistant_mcp
```

DeepSeek Harness 的 MCP transport patch 可使用其官方 stdio 插件结构。下面只展示连接参数；它必须放入上述专用受限 preset，不能直接叠加到 Standard/Code/Creator mode：

```yaml
- insert:
    - id: mcp-portfolio-copilot
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: portfolio
        transport: stdio
        command: <repository-root>/.venv/bin/python
        args: ['-m', 'portfolio_app.assistant_mcp']
        cwd: <repository-root>/apps/portfolio/backend
        env:
          PORTFOLIO_OPS_PORTFOLIO_COPILOT_API_BASE_URL: http://127.0.0.1:8001/api
          PORTFOLIO_OPS_PORTFOLIO_COPILOT_PORTFOLIO_ID: <portfolio-id>
          PORTFOLIO_OPS_PORTFOLIO_COPILOT_BATCH_ID: <batch-id>
          PORTFOLIO_OPS_PORTFOLIO_COPILOT_MODEL_NAME: <configured-vision-model>
        toolCallTimeoutMs: 60000
        failOnStartupError: true
```

Codex MCP 配置使用同一个 command、args、cwd 和环境变量即可；无需另一套 Portfolio 工具实现。

## 页面人工复核与登记

Transactions 页的 Screenshot Assistant 已复用现有 JSON Preview/Commit 合同形成完整的人工作业流：

1. `assistant` revision 只显示为 Agent 提案，不能直接登记；
2. 人工在当前 Portfolio 内核对账户、日期、数量、价格、金额及 Security/FCN/Option 条款，批次来源身份保持只读；
   当前复核面覆盖证券和公私募基金、FCN、期权、存取款、利息、费用、税费、同币种账户转账及换汇；换汇分别保留源账户、目标账户、源金额、目标金额与汇率，既有衍生品合约可直接选择，截图中新合约则复核完整条款；
3. Agent 留下的每个问题必须逐项确认，保存后创建新的 `human` revision，并由后端重新运行 Preview；
   若候选命中既有交易或批次内重复，人工可标记为 `same_record`、`distinct_records` 或 `uncertain`；相同或不确定记录从提案中排除，其余多记录自动重排为连续的批次来源引用后再 Preview；
4. 只有最新 revision 为 `human`、问题为空、Preview digest 存在且 `error_count=0` 时，页面才显示登记入口；
5. 最终确认框明确显示当前 Portfolio 和将写入的记录，Commit 使用与 Preview 完全相同的 payload 及独立 Idempotency-Key；
6. Commit 成功后刷新交易工作区；批次的 `recorded / partially_recorded / unrecorded` 状态由后端按持久化账本中的截图 source reference 推导，页面刷新、交易筛选或重新打开后都不会仅依赖前端临时状态，也不会对已登记批次再次开放 Commit。

## 验收合同

模型、provider、prompt、Harness 或分析 schema 发生实质变化时，使用同一组人工金标准做 shadow 验收；一次性结果留在任务记录，不复制进长期文档。验收至少覆盖：

1. 单图分类，以及多图应合并或拆分的边界；
2. 账户和 canonical instrument/contract 的 `resolved / ambiguous / unavailable` 结果；
3. 数量、价格、金额、日期、方向和衍生品条款的字段级证据；
4. 既有交易重复识别，以及持仓/现金快照不得被反推成历史交易；
5. 缺少关键事实时停在问题或候选层，不制造完整提案；
6. Portfolio Preview 的逐行、批次、持仓历史和来源幂等校验；
7. shadow 前后正式交易事实不变。

不同模型路线必须使用相同工具边界、结构合同和金标准比较。模型自报置信度不能直接触发 Commit，也不为单一券商版式固化绕过通用证据与 Preview 的模板分支。
