# Yungu Platform

这是 `Yungu` 的极轻平台入口 app。

当前职责只有：

- 展示 `Yungu` 平台首页
- 提供到 `Watchlist` 和 `Portfolio` 的入口
- 承载共享 `Instruments / FX` 数据 API

当前不承载：

- 共享业务路由
- 共享 detail 页面
- 跨 app 业务状态

## 目录

- `frontend/`
  平台 landing / app switcher
- `backend/`
  平台 backend，提供健康检查、app registry、共享资产核心与 FX API
