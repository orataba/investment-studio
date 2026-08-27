# Portfolio 文档索引

这里的文档只描述当前 Portfolio 合同。发布记录、验收数字和历史优化过程由 Git、PR 或任务记录保存，不能替代当前代码、migration source 或实时数据审计。

建议按以下顺序阅读：

1. [01_CALCULATION_SPEC.md](./01_CALCULATION_SPEC.md)
   Portfolio 估值、NAV、TWR、风险、归因、Risk Budget、Research solve 和质量状态的 canonical 计算规格。
2. [03_HOLDINGS_FIELD_REFERENCE.md](./03_HOLDINGS_FIELD_REFERENCE.md)
   Holdings 每个字段、分组聚合和不可用状态的逐项口径。
3. [04_TRANSACTION_OPERATIONS.md](./04_TRANSACTION_OPERATIONS.md)
   交易事实、日期时钟、Preview/Commit、幂等、修改审计和派生账本的操作合同。
4. [02_GIPS_ALIGNMENT.md](./02_GIPS_ALIGNMENT.md)
   GIPS-informed 方法边界、当前覆盖限制，以及哪些结果不能声称为 GIPS-compliant。

仓库级架构、数据库、部署和开发入口见
[../../../docs/DEVELOPER_GUIDE.md](../../../docs/DEVELOPER_GUIDE.md)。

## 维护规则

- 公式和失败语义只在 `01_CALCULATION_SPEC.md` 定义；其他文档链接过去，不复制第二份公式。
- Holdings 字段增加、删除或改口径时，同步修改 `03_HOLDINGS_FIELD_REFERENCE.md`。
- 交易 command 或日期/金额语义变化时，同步修改 `04_TRANSACTION_OPERATIONS.md` 和外部导入合同。
- 发布日期、commit、测试数量、生产数据量与一次性性能测量不进入长期文档。
- 计算 payload 或口径变化必须同步评估 `calculation_version`、物化快照重建和回归测试。
