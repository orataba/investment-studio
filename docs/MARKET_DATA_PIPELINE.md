# 共享市场数据与资讯

Investment Studio 自己持有公开数值采集、历史库和应用数据。外部市场资讯采集服务
`market-information-feed` 负责抓取、初步清洗、原始内容留存和文本包；不生成日报、周报或研究报告。
旧数值数据库只作为一次性迁移源，运行时不导入其 Python 包、不打开其数据库或读取其密钥目录。
文本从 2026-09-07 的正常采集重新积累，不迁入旧项目的文本历史和报告。

## 数据归属

| 数据 | 所有者 | 存储与使用 |
| --- | --- | --- |
| 全市场证券目录、日行情、财报、预期、成份、宏观等 | Studio 共享数值层 | `market_data` 目录与最新投影；不可变 Parquet 历史及原始响应 |
| 新闻、公告、事件、观点、舆论 | 资讯采集服务输出；Studio 接收并保存 | `market_text` 版本索引、数据包回执、原文与渠道覆盖 |
| 登记资产身份及应用使用的行情／净值／FX | Instrument Data | `instrument_data`；公开数值按原有单位、币种与复权合同投影 |
| 邮件、私有附件、净值候选、解析状态 | 私有摄取层 | `data_ingestion` |
| 账户、交易、FCN／期权合约、账本 | Portfolio | `portfolio`，不进入公开数据包 |
| 投资观点、底稿、事件判断、风险跟进 | Watchlist | `watchlist`，原始资讯按不可变版本引用 |
| 状态识别的物化输入、模型及结果 | Regime | 自有数据库和运行目录；来源读取 `studio_market` |
| 日报／周报 | Briefing | `briefing`，保存版本、输入引用和生成记录 |

共享数值没有第二套完整 DuckDB；DuckDB 仅查询 Parquet。全市场数据积累不受观察列表范围限制。
消费者用数据集和不可变来源 ID 查询，不直接遍历其他项目的文件布局。

## 数值范围与 PIT

- 美股：证券目录及退市／代码变更、日行情、分红拆股、公司资料、标准财报及字段、分析师预期／评级／目标价、ETF 成份及披露、财报事件、宏观与市场序列。
- 原样财报、SEC 目录和评级细项历史支持显式指定证券补采；不宣称所有细项都已全市场每日刷新。
- A 股、港股及其他登记资产通过已有 FMP、Tushare／DataHub 与权威来源适配接入；按实际权限与数据覆盖记录缺口。
- 期货只保留日频及其他低频事实。分钟行情、分钟衍生数据与旧 Parquet 重复导出不迁入，也不排入采集。
- 分析师预期按公司、年度／季度、完整采集轮次保存。A→B→A 是三个观察，空的完整快照不会复活已撤掉的预测财期或成份。
- `observed_at` 是实际捕获时间，供应商明确的发布时间另存；历史查询必须满足相应可用时钟。今天回补的修订财报不能伪装成多年以前已经知道的数据。
- 旧资料中的异常未来财期和明显不匹配会计年度不进入可用事实，迁移回执记明排除数量与理由，原始证据仍保留。
- 财报推断的预期币种保留推断来源；不能用股票报价币种填补未知的预期币种。

`us_eod_daily.close` 是拆股调整价；`raw_eod_daily` 保存未经复权的 OHLC，并另存供应商的分红调整价。
Portfolio 的登记行情投影使用后者，保留 GBp 等报价单位转换与复权因子合同。报表价格变化明确展示两端实际交易日，不将价格收益称为含分红总回报。

FMP 的 `market_series_daily` 按可识别的 series／日期隔离不合格数值：保留整份不可变原始响应，
严格校验后只发布有效日期；非正数或冲突重复记录所在的整日不进入本次可用事实。身份不符、
结构错误或无法定位日期的响应仍整份拒绝，不按周末无条件过滤，也不修造缺失价格。
包含坏日的批次在 `details.validation` 保留 `status=partial`、有效行数、拒绝日期、原始行索引及原因，
原始响应仍由 `raw_ref` 追溯。这里的 batch `ready` 只表示有效事实及证据可以发行，源／采集阶段仍为
`failed`，消费者不能把它作为完整历史；后续重叠采集可以继续发布新的有效日期，不能清除旧失败回执或
覆盖 PIT 历史。异常旧日不能阻塞同源有效新日。
这一规则仅用于市场序列，不改变股票复权补采必须完整覆盖历史后才能写完成 receipt 的合同。

Regime 的指数、汇率、现货和加密资产原始报价同样按日期隔离异常，保留仅有收盘价的来源合同。
采集批次、复制后的读取来源和模型输入 manifest 都保留相关质量证据；源初始化不能把部分有效响应
标为完整成功。后续有效响应可解除当前读取范围内已纠正的警告，历史证据保持不变。模型是否可用
仍由既有交易日覆盖与样本要求判定，不通过补造价格或降低模型门槛处理源端异常。

## 文本与研究

每条资讯分别保存首发、事件发生、源端观察和本端接收时间。只有日期时保留日期精度，未知时间保持未知。
来源提供摘要、付费墙片段或无法取得正文时按实际完整度标注；初步清洗不允许把模型推断改写成原文。
修订、撤回和重复来源可以追溯，不能仅用 URL 覆盖旧版本。

研究追踪先分页检索共享资讯，再读取绑定版本原文，按需要补充在线搜索。新抓取的原文同样进入共享库。
资料不足、未检索、来源失败和未发现重大新增是不同结果；研究可以完成有限分析，但必须保留覆盖不足说明。

日报／周报均通过受限 DeepSeek Harness 生成。数值变化在程序中计算，模型解释绑定输入并结构化提交引用。
首轮形成草稿后，独立校稿会话重新阅读所引原文，核对事实、条件、时间与归属；校稿提交成功后才形成完成版本。
正式报告由云端 `publisher` 生成，本地为 `preview`。周报重新读取整周窗口，历史补录与本期新资讯分别说明。

## 两端运行

外部配置位于服务用户的 `~/.config/orataba/secrets/investment-studio`：

- `market.env`：明确数据库、公开数据目录、`ROLE=collector|replica`、源密钥文件路径及传输位置。
- `briefing.env`：Briefing 数据库、API 回调和 `EDITION_ROLE=publisher|preview`。
- 原有 `data.env`：私有摄取、登记资产投影与结算；行情供应商密钥也必须指向本项目自己的秘密目录。
- `portfolio-copilot.env`：现有 DeepSeek 凭据；不会传给公共数据包，也不会把数据库凭据交给 Harness。

云端负责公开数值采集；本地只复制。各端私有账本与材料分别维护，不同步整套业务数据库。
本地 Regime 使用 `studio_market_regime_reader` 读取共享数值，只获 `market_data`
schema 的 `USAGE` 和表的 `SELECT`。云端采集进程使用独立的 `studio_market_regime_writer`，
只获数值库现有五张事实／发行表所需的读写权限；两个账号均无私有账本或 schema DDL 权限。
市场库迁移在这些账号已由部署环境创建时重设上述权限，并维护新表的默认读取权限。
同库升级备份和回滚保留原 ACL；跨实例导入若省略 ACL，须先按部署合同配置账号并执行统一迁移。
共享对象目录的默认文件 ACL 必须允许 Studio 与 Regime 的运行 UID 读取对方的新对象，并在目录中创建对象。
数值原始响应、数值包导入和文本原件通过 `mkstemp` 原子发布；新临时文件在发布前设为 `0640`，
使继承的具名读取 ACL 生效，同时不给其他用户权限或给另一写者原地修改对象的权限。
这些入口不修改已存在对象的权限；部署时须单独核验历史对象的 ACL，私有目录不适用此公共对象合同。
资讯包分别送达两端；Mac 离线后逐包补齐，成功回执才推进接收状态。接收时间保留本机真实时钟。
追数同时核对数据库的实际导入记录，外部传输回执不能代替数据库事实。恢复较早数据库后，仍在发行目录中的缺失数值包和资讯包会重新导入；已经退役的数值包缺批次时明确要求从采集端生成新的恢复包，不会报告已追平。
本地数值同步成功后会唤起已安装的 Regime `catch-up` agent，按最近已结束交易日补跑。
这样云端发行晚于本地固定排期时，数据到达后仍会计算；已有运行由 launchd 和 Regime 全局锁串行处理。
数值与文本分别判断成功；文本接收失败不阻断已成功数值的应用投影。Regime 唤起、行情投影和资料投影也分别完成，任一步失败仍使本轮返回失败，保留真实诊断供后续补跑。
Mac 通过已信任的 Tailscale SSH 入口连接资讯采集服务器，不依赖其局域网地址。

```bash
bin/investment-studio market numeric status
bin/investment-studio market text coverage
bin/investment-studio market pipeline status
bin/investment-studio market pipeline sync
bin/investment-studio market pipeline daily
bin/investment-studio market pipeline weekly
bin/investment-studio market pipeline registered-prices --market hk
```

数值定时采集、发行和复制由 `infra/scripts/install_market_pipeline.py` 安装；它仅写定义，启用是独立部署操作。
每周任务更新证券目录、公司资料以及指数当前成份和历史成份事件，已有历史族持续积累。采集时间与各市场闭市日期分别判断；大型全市场批次不会阻塞登记资产收盘价采集。
原有数据刷新任务负责将共享数据投影到应用并执行私有净值结算，不再各自抓取股票及 ETF 历史。

日报和周报定时器由 `infra/systemd/install_briefing_timers.sh` 安装，默认北京时间日报 08:30、周六周报 09:00。
计划时间不代表资料齐备；实际源日期、渠道覆盖和失败记录始终随报告展示。
源码与具体命令选项见 [共享市场层说明](../shared-data/market/README.md) 和 [Briefing](../apps/briefing/README.md)。

## 首次迁移与存储

```bash
bin/investment-studio market numeric migrate-source /absolute/old/data
bin/investment-studio market numeric migrate-source /absolute/old/data --apply
bin/investment-studio market numeric export-bundle /absolute/bootstrap.zip
bin/investment-studio market numeric import-bundle /absolute/bootstrap.zip
```

迁移流式读取原始观察与响应，成功的数据集重复执行会跳过；不要为恢复失败任务而无差别使用 `--reimport`。
批次元数据和原始来源 ID 随数值包传递，导入不改写旧观察时钟。迁移完成后核对回执的源行数、可用行数、排除数量及空间占用。

大规模首次入站包会额外占用一份空间；容量规划需要同时计入规范数据、原始响应和发行包。本地已具备全部批次时直接跳过该包下载。
后续增量包保留到两个接收端都能恢复；容量规划以实际留存与增长测量为准，不假设压缩率。

PostgreSQL 备份只包含目录与业务数据，**不包含 Parquet 和文本原文文件**。
完整备份必须同时包含公开数据目录；执行备份／恢复时先停止共享写入与报告生成，再保存数据库及对应不可变对象。
只恢复 PG 元数据不能恢复共享数据。已有项目 schema 备份、恢复和迁移脚本已纳入 `market_data`、`market_text`、`briefing`。

Regime 云端来源容器也会写入 `market_data`。共享 schema 迁移或还原前，必须等其当前来源采集退出，并暂停对应的 autonomous source timer；操作完成后恢复。Studio 的停止任务集合不包含 Regime 原生 units，不能代替这一步，也不表示所有共享写入已自动停止。
