# Investment Studio Home

这是登录与导航主页，不是第四个业务 App。仅提供 Watchlist、Portfolio、Regime 三个入口。

- `frontend/`：登录页面和主页导航。
- `backend/home_api/`：验证登录、会话、入口地址和主页自身健康状态。
- 不连接业务数据库，不持有 FMP/DataHub/邮箱密钥，不导入数据维护或业务计算模块。
- 私密配置来自仓库外 `home.env`，前缀为 `INVESTMENT_STUDIO_HOME_`。
- `apps.json` 是唯一导航清单。增删卡片不需要修改 React；云端地址通过 `INVESTMENT_STUDIO_HOME_APP_URLS` JSON 对象按 app_id 覆盖。

所有数据接入、解析、导入、人工修正和定时更新都属于独立的 [Data](../shared-data/README.md)，不经由主页执行。Regime 的代码、更新和数据库仍属于它自己的项目。

本地服务与部署见 [Local Service](../docs/LOCAL_MACOS_SERVICE.md) 和 [Server Deployment](../docs/SERVER_DEPLOYMENT.md)。
