# Fund Terminal V2 AI Copilot

适用范围：当前 watchlist app 里保留的 Copilot 后端接口、上下文来源、返回协议和 provider 边界；前端 UI 当前默认关闭，不属于已发布工作面

## 1. 定位

Copilot 不是独立产品，也不是当前已发布工作面里的聊天框。

当前定义很明确：

- 它保留为 `Watchlists` 和 `Instrument Detail` 的后端上下文助手接口
- 它优先基于系统内 read models / manual profiles / 当前页面状态回答
- 当前保持只读
- API 契约优先稳定，provider 可以替换
- 在真实 provider 和正式 UX 准备好之前，前端入口默认保持关闭

## 2. 当前保留入口

### 2.1 Watchlist Copilot

后端接口：

- `POST /api/copilot/watchlists/{watchlist_id}/chat`

当前上下文来源：

- 当前 watchlist
- 当前 view
- 当前表格行
- 当前排序 / 分组 / 筛选后的结果
- watchlist row read model

### 2.2 Instrument Copilot

后端接口：

- `POST /api/copilot/instruments/{instrument_id}/chat`

当前上下文来源：

- summary
- quote / chart
- performance
- risk
- exposure
- people
- strategy
- documents
- research

当前 detail overlay 以 fund 为主，所以这部分上下文当前最完整的仍是 fund。

## 3. 当前 provider 策略

当前默认配置：

- provider: `stub`
- model label: `gpt-5.4-ready-stub`

环境变量：

- `PORTFOLIO_OPS_WATCHLIST_COPILOT_PROVIDER`
- `PORTFOLIO_OPS_WATCHLIST_COPILOT_OPENAI_MODEL`
- `PORTFOLIO_OPS_WATCHLIST_COPILOT_OPENAI_API_KEY`

当前默认模型名配置仍然是：

- `gpt-5.4`

但只有在 provider 切到 `openai` 且提供 API key 后才会真正启用。

## 4. 返回协议

当前两个 Copilot 接口统一返回：

- `provider`
- `model`
- `mode`
- `answer`
- `suggestions`
- `citations`
- `context_summary`
- `generated_at`

这份协议应视为稳定接口，后续替换 provider 时尽量不改消费者结构。

## 5. 当前边界

当前 Copilot 不直接写：

- canonical facts
- NAV rows
- automatic investment ratings
- research notes
- manual profiles
- product framework labels

它当前只做：

- explain
- summarize
- suggest
- draft

## 6. OpenAI 接入原则

后续重新开放前端并接入 OpenAI 时，按下面顺序推进：

1. 保持 `/api/copilot/.../chat` 路径和返回结构不变
2. 继续优先使用系统内上下文，不把 Copilot 变成开放搜索框
3. 只在 provider 层替换模型调用，不回退到旧接口别名
