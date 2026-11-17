# 砚田包装试封比较席

两种封口工艺独立合格/失败计数→固定方案比较→复试决策；双侧Fisher按概率不大于观测表求和。原生 HTML/CSS/JavaScript 多模块工作席，所有计算由 Python 完成。

## 模拟业务
砚田研究协作组，12名分析师与业务人员，日常使用 `seal-fisher-202611-1.csv` 手工抄表。当前三个痛点：观测与口径不可追溯、重算易出错、结果与复核割裂。上游提供独立合成业务观测；本应用接管数据登记、方法执行和输入冻结；下游负责人形成复测/预算方案，观察员获取复核后的CSV。

核心业务实体：SealTrial / TrialCount / ExactRun / TrialDecision。源码 model/schema/service/controller/route 与前端 api/store/pages 完成链路，不依赖浏览器本地假数据。

## 功能与方法
- 试封方案、工艺计数录入/编辑/删除；重名记录与非法数值明确拒绝；每次更正记录版本号、时间与更新原因。
- 联合研究可引用多个来源批次，也可嵌套引用其他联合研究。按底层来源去重后汇总A/B计数：同一来源被多条引用路径带入只计一次，同一来源出现不同取值则拒绝（不猜测）。
- 循环引用（含自我引用与跨层间接循环）拒绝，原引用保持不变。
- Fisher精确检验、Wilson单比例区间与率差。两组试件独立，组别预先固定。双侧 p 值是固定边际下概率不大于观察表的所有表之和。
- 每次运行冻结参数、观测、研究指纹和SHA256。Redis任务由独立worker计算，写DB与共享产物卷；输入修改不会改写历史结果。
- 源数据更新后，自动找出受影响的依赖链（该批次及其所有上级联合研究）重算；未受影响的研究不重算。
- 同一次更新关联的所有结果全部就绪后才原子切换；等待、失败或过期时保留旧版，并在发布记录与结果页写明原因。
- 并发连续更正时，只有最新且完整就绪的依赖版本可生效；旧批次标记为已过期，不覆盖新版本。
- worker中断后只重新排队数据库中已有的运行，不重复生成研究；原独立研究保留冻结结果。
- 负责人复核不可覆盖且只针对当前生效版本；审计记录登录、数据改动、排队、重算发布、复核与导出；240请求/分钟限流、全局可恢复错误。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18241 ，API http://localhost:19241/docs 。数据库映射 17741。无需Node构建。可以从中文和带空格目录启动。清理 `docker compose down -v --remove-orphans` 会删除本题演示数据卷。

|角色|账号|密码|权限与典型动作|
|---|---|---|---|
|admin 负责人|admin@demo.local|Research2026!|全部读写、批准复核、下载|
|analyst 分析师|analyst@demo.local|Research2026!|数据修改、创建任务、读取结果及下载；不能复核|
|viewer 观察员|viewer@demo.local|Research2026!|只读；仅可导出已复核结果|

## 一天的工作剧本
1. 09:00 admin登录，查“砚田·薄膜一轮”。
2. 09:10 analyst核对 `seal-fisher-202611-1.csv` 的seed记录。
3. 09:15 来源录入页检查 `工艺组 (A/B)` 等业务字段，必要时编辑。
4. 09:20 分析执行页保留默认参数 `{"alpha": 0.05}`，命名本轮研究。
5. 09:21 冻结输入并排队，worker实算，结果页自动刷新。
6. 09:25 阅读核心数值、方法边界、明细、来源追踪与SHA256；发现失败先修数据重跑。
7. 09:35 admin打开业务复核，写明限制与下一步动作，归档后不可覆盖。
8. 09:40 viewer读取复核意见并下载CSV，在操作留痕页核对下载事件。

seed提供3次试验×2个工艺组，共6条汇总计数，代表600份独立试件；另建一个引用「薄膜一轮」的嵌套支路「薄膜一轮·复核支路」和联合研究「砚田·联合复核」，用于观察菱形引用去重。每次运行每组只允许一条预聚合记录；修正计数应编辑原记录，重复组会明确失败。联合研究不直接录入计数，只汇总底层来源。

## 技术与目录
|部分|实现|
|---|---|
|前端|原生ES modules、HTML/CSS、Nginx1.27，无前端框架|
|API|Python3.11、FastAPI0.115、SQLAlchemy2、JWT、PBKDF2密码|
|计算|NumPy2.2、SciPy1.15，固定seed42|
|数据库|MySQL8|
|任务|Redis7持久队列，Python独立worker，心跳healthcheck；启动时恢复未完成队列与待发布批次|
|依赖|`src/services/dependencies.py` 解析引用图、去重汇总、研究指纹；`src/services/publication.py` 负责受影响重算、批次原子发布与恢复|

`frontend/src/`分api、stores、types、components/common、hooks、pages、router、utils、constants；`backend/src/`分config、models、schemas、services、controllers、routes、middlewares、utils、constants；`backend/scripts/seed_demo.py`自然键插入、重复不覆盖修改；`backend/migrations/README.md`说明schema版本。

环境变量 `.env.example`：COMPOSE_PROJECT_NAME、FRONTEND_PORT、BACKEND_PORT、DB_PORT、DB_PASSWORD、JWT_SECRET、SEED_DEMO。开发时在本地venv安装backend/requirements.txt，设置DATABASE_URL、REDIS_URL、JWT_SECRET再运行uvicorn；前端开发经Nginx代理 `/api/` 到不带前缀的API。

共享枚举：角色admin/analyst/viewer和队列queued/running/completed/failed；后端`src/constants/enums.py`、认证与worker使用相同值；前端`src/types/enums.js`、状态与角色界面使用相同值。Google Fonts：Noto Serif SC + IBM Plex Mono；断网使用serif/monospace回退。

## 联合研究、去重与原子发布
- 引用图：`study_members(parent_id, child_id)`。创建/修改引用前先做环检测；顶层研究解析为底层来源批次集合（按研究去重），再把各来源记录按 `source_key` 去重。
- 取值冲突：同一 `source_key` 在不同来源批次中取值不同 → 依赖无法解析，批次记为失败并保留旧版。
- 研究指纹：去重后底层来源集合的 SHA256。运行冻结该指纹与快照 SHA256；发布前重新计算，二者不一致即视为过期。
- 受影响集合：被更新批次本身 + 所有传递上级联合研究；指纹未变的不重算，尚无生效版本的研究留给用户显式发起首次运行。
- 发布批次：每次更正生成一个 `publication_batches` 行和多个 `publication_items`。所有 item 的运行为 completed 且指纹仍匹配才整体切换 `studies.current_run_id`；否则状态为 failed / superseded / pending，旧版继续生效。
- 中断恢复：`worker` 与 API 启动时调用 `publication.recover`，只重排数据库中 queued/running 的运行，已 completed 的复用，不新建研究。
- 页面：来源录入页可组建/编辑联合研究并查看依赖与来源追踪；结果页展示底层来源、版本与更新原因；「发布记录」页列出批次状态、关联运行与原因。

## 算法输入、失败态与复现
输入为选定批次全部观测和参数，输出核心数值、明细、解释；参数经后端严格白名单和范围验证。两组试件独立，组别预先固定。双侧 p 值是固定边际下概率不大于观察表的所有表之和。。空批次拒绝；不完整字段/非有限数/非法计数拒绝；常量相关列与不足样本使任务failed并保留错误。固定seed42、输入checksum相同可复现。独立oracle：2×2固定边际对scipy fisher_exact；零格与计数非法拒绝。联合研究的去重汇总在进入算法前完成，算法只看到一份A、一份B汇总。

已知边界：小型本地研究应用，使用合成数据，不含企业SSO与外发邮件；结果由人工复核，不自动执行业务决策；单worker恢复queued/running任务，数据库与Redis间未采用分布式事务。测试：安装 `pytest` 与 `httpx` 后运行 `python -m pytest backend/tests -q`（SQLite + FastAPI TestClient，无需 Docker/MySQL/Redis）。覆盖菱形引用去重、取值冲突、自我/间接循环拒绝、连续更正只发布最新版、批次等待与失败保留旧版、发布中断恢复不重复、未受影响研究不重算、更新原因与来源追踪。

MIT License。
