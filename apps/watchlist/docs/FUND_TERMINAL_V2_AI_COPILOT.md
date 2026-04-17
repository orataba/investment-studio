# Fund Terminal V2 AI Copilot

状态：Current baseline  
日期：2026-04-14  
适用范围：当前仓库里的 Copilot 入口、上下文来源、返回协议和 OpenAI 接入边界

## 1. 定位

Copilot 不是独立产品，也不是游离于页面之外的聊天框。

当前定义很明确：

- 它是 `Watchlists` 和 `Fund Detail` 的上下文助手
- 它优先基于系统内 read models / manual profiles / 当前页面状态回答
- 第一阶段保持只读
- API 契约优先稳定，provider 可以替换

## 2. 当前入口

## 2.1 Watchlist Copilot

后端接口：

- `POST /api/copilot/watchlists/{watchlist_id}/chat`

当前上下文来源：

- 当前 watchlist
- 当前 view
- 当前表格行
- 当前排序 / 分组 / 筛选后的结果
- watchlist row read model

当前输出重点：

- 概览当前名单
- 总结评分、回撤、freshness、分组特征
- 给出建议追问

## 2.2 Fund Copilot

后端接口：

- `POST /api/copilot/funds/{fund_id}/chat`

当前上下文来源：

- summary
- quote/chart
- performance
- risk
- portfolio
- ratings
- people
- strategy
- documents
- research

当前输出重点：

- 按 tab 做结构化总结
- 用当前上下文解释数据
- 返回可继续追问的问题

## 3. 当前 provider 策略

当前默认配置：

- provider: `stub`
- model label: `gpt-5.4-ready-stub`

环境变量：

- `FTV2_COPILOT_PROVIDER`
- `FTV2_COPILOT_OPENAI_MODEL`
- `FTV2_COPILOT_OPENAI_API_KEY`

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

这份协议应视为稳定接口，后续替换 provider 时尽量不改前端消费结构。

## 5. 当前边界

现阶段 Copilot 不直接写：

- canonical facts
- NAV rows
- ratings
- research conclusions
- manual profiles

它当前只做：

- explain
- summarize
- suggest
- draft

## 6. OpenAI 接入原则

后续接入 OpenAI 时，按下面顺序推进：

1. 保持 `/api/copilot/.../chat` 路径和返回结构不变。
2. 在 backend provider 层替换 stub，而不是重写前端入口。
3. 默认模型继续沿用 `FTV2_COPILOT_OPENAI_MODEL`。
4. 保留 citations / context summary / suggestions 结构。

## 7. 下一步最值得推进的事项

1. 把 stub provider 替换成真实 OpenAI provider。
2. 给 watchlist copilot 增加 query-to-view / query-to-filter 能力。
3. 给 fund copilot 增加更强的 tab-aware summarization。
4. 让 documents、monitoring、research 的 context chain 更结构化，而不是纯文本拼接。
