# Portfolio Backend

这是 `Portfolio` 的后端实现目录。

当前包含：

- FastAPI
- SQLAlchemy
- Alembic

运行与校验命令以 [apps/portfolio/README.md](../README.md) 为准。

## Daily snapshot recalculation

`POST /api/portfolios/snapshots/daily/recalculations` 是唯一外部重算入口，返回
`202 Accepted`。请求必须且只能指定 `portfolio_ids`、`instrument_ids` 或
`refresh_all` 之一；服务只更新 durable calculation state，不在请求线程执行计算。

后台单线程 worker 消费 `stale` 和租约过期的 `running` generation。并发请求通过
`refresh_request_id` 合并，`dirty_from` 永远保留最早日期；计算发布前再次核对 source
generation，被新请求取代的结果不会落库。worker 还会分批核对有持仓且已有物化快照的
组合与 Registry 市场数据水位，使通知丢失或进程重启能够自动恢复。
