# Yungu Phase 1 Platform Setup

> 历史阶段文档，不是当前操作手册。当前落地状态请以 [README.md](../README.md)、[docs/README.md](./README.md) 和各 app README 为准；下面内容只保留为 phase 1 建设记录。

## 目标

第一阶段只完成平台骨架和 app 迁入准备：

- 建立 `Yungu` 平台目录
- 迁入 `Watchlist` 基线工程
- 建立 `Portfolio` 初始 app 骨架
- 定义最小共享边界

这一阶段不做：

- Watchlist / Portfolio 业务融合
- 共享 read model
- 共享 portfolio / transaction / review 领域对象

## 已完成

- 创建 `apps/watchlist`
- 从原 `fof` 复制当前基线工程进入 `apps/watchlist`
- 创建 `apps/portfolio`
- 迁入 `pmw` 设计文档至 `apps/portfolio/docs`
- 创建 `packages/asset-core`
- 创建 `packages/ui`
- 创建 `packages/copilot` 预留目录
- 补充平台 README 与边界文档

## 下一步

1. 稳定 `apps/watchlist` 在新目录下的启动与构建
2. 在 `packages/asset-core` 定义最小共享 contract
3. 在 `apps/portfolio` 开始建立后端域模型与前端路由骨架
4. 统一平台视觉系统与公共 UI primitives
