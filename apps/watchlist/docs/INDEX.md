# 文档索引

这组文档只保留当前工程基线、接口契约和产品框架。

建议阅读顺序：

1. [../../../docs/README.md](../../../docs/README.md)
   仓库级文档入口，先看全局数据库工作流和平台边界。
2. [README.md](../README.md)
   项目概览、启动方式、校验命令、当前行为边界。
3. [CURRENT_SYSTEM_BASELINE.md](./CURRENT_SYSTEM_BASELINE.md)
   当前 watchlist app 已经实现到哪里、哪些页面和接口是真实可用的。
4. [FUND_PRODUCT_FRAMEWORK.md](./FUND_PRODUCT_FRAMEWORK.md)
   fund 产品框架的三层模型：`Fund Taxonomy / Research Tags / Monitoring Assessment`。
5. [FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md](./FUND_QUALITATIVE_RESEARCH_FRAMEWORK.md)
   fund detail `Research` 页的定性研究标签、`timeline_notes` 和人工 rating 规则。
6. [FUND_TERMINAL_V2_DATA_MODEL_AND_API.md](./FUND_TERMINAL_V2_DATA_MODEL_AND_API.md)
   当前后端数据分层、关键表、API 分组与路由边界。
7. [RETURN_SERIES_CONTRACT.md](./RETURN_SERIES_CONTRACT.md)
   主图、Sparkline、1W/1M/3M/6M/MTD/YTD/1Y、自定义区间和私募复权收益的统一边界契约。
8. [FUND_TERMINAL_V2_AI_COPILOT.md](./FUND_TERMINAL_V2_AI_COPILOT.md)
   保留中的 Copilot 后端接口、上下文范围、返回协议与 provider 边界；当前 UI 默认关闭。

后续如果需要新文档，默认只补三类内容：

- 当前实现基线
- 当前可执行接口契约
- 明确、收敛的下一步推进事项
