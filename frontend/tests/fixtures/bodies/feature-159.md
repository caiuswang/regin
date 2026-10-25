# F159 面试题整理

## 要做成什么

把 `interview/resume_catalog.md` 里列出的每一个「简历点 → 会被怎么追问」，都落成 `interview/` 题库里一道能被 `服务/prep.py` 解析、能进复习队列的题。题的格式、契约、判分和队列规则以 `interview/README.md` 为准，这个 feature 不改格式，只补题、修题、收尾迁移。

整理的标准不是「有笔记」，而是 `prep_stats` 能数到它：README 里写着的就是唯一格式（`### 前缀-NN 题面` + 契约 / 节点 / 信号 / 证据 / 追问），不进编号目录、不按这个格式写的内容，服务看不见，等于没整理。

## 现状（2026-10-12 实测）

- 题库 165 题，19 个文件，没有题号冲突，每题都有节点、信号和参考答案。
- 练习记录集中在 jvm（13 题，8 题没过）、netty（12 题，9 题没过），其余主题基本没练过。这属于练习，不归本 feature 管。
- `markdownfile/面试/` 往 `interview/` 的迁移已经做完，但**还只是 staged，没有提交**；`00-叙事/自我介绍.md`、`非技术.md` 和 `10-项目/` 下的三份是 untracked。根目录的 `CLAUDE.md` 还写着「`resume_catalog.md` 未跟踪、cheatsheet 在 `markdownfile/面试/`」，已经过时。
- `markdownfile/面试/` 只剩归档的 `.org`（IM、限流、mysql、zookeeper、hr、面试）和 `design/design_twitter.md`。按约定 `.org` 不批量转换，但 IM、限流、twitter 三份可以作为系统设计题的素材来读。

对照 `resume_catalog.md`，缺口有这些：

| 提纲章节 | 现状 |
|---|---|
| 四、ORM 框架设计与切换 | 没有题库文件。Hibernate 原理和 Tree-sitter AST 改写都没有题 |
| 六、去哪儿 | 没有题库文件。聚合任务、慢查询 explain、价格重构、启动预热、到店率归因、Dubbo/QMQ 都没有题 |
| 七、中国软件 / 电子公文 | 没有题库文件。WebSocket 集群 + RocketMQ 广播、工作流引擎、Memcached 取舍都没有题 |
| 九、系统设计 | `30-系统设计/` 是空目录。提纲里的四道题（高并发下单、开奖结算、RAG/Agent 平台、单体拆微服务）都没写 |
| `10-项目/regin.md` | 四个 STAR 故事还是散文，解析出来是 0 题；有 6 处 `[填]` |
| 八、基础深挖 | Docker/K8S（namespace + cgroup、容器里的 JVM 内存）、Linux 排查命令组合、CompletableFuture、HashMap 扩容与树化、Redis Cluster 槽位、Redlock 争议都还没有专门的题 |
| 薄主题 | cap 3 题、agent-skills 3 题、java版本 4 题 |
| 十一、反问环节 | 只在 `非技术` 的「薪资与收尾」节点里有 2 题，没有按公司类型分的反问清单 |

数据层面有两个小问题：

- `JAVA-01` 的契约写的是 `Knowledge`，这个骨架在 `设计.md` 里已经明确弃用，会让判分结构落空。
- `JVM-02` 没有追问链。`HR-03/04/05/08/10/11/12` 也没有，但 HR 题本来就不一定有追问链，可以不补。

## 范围（按优先级）

1. **把迁移提交掉。** 当前 staged 的整理和 untracked 的叙事、项目文件一起提交（`.gitignore` 已经排除 `attempts.db` 和 `__pycache__`），同时把根目录 `CLAUDE.md` 里讲面试目录的那两段改成 `interview/` 的现状。这一步不做，后面每一步都叠在一棵没提交的树上。
2. **`10-项目/` 补三份：** `ORM切换.md`、`去哪儿.md`、`中国软件.md`。每份的题从提纲对应章节逐条转写，涉及数字的题挂 `证据: EV-xx`。如果证据表里还没有对应条目，就新增 EV 编号，`基线 / 测量 / 归因 / 能说` 留 `[填]`。
3. **`regin.md` 改写成题格式。** 四个故事各拆成「STAR 主题 + 可能追问里的每一条」，前缀用 `REGIN-`。散文保留为参考答案，`[填]` 占位符原样保留，并挂到证据表。
4. **`30-系统设计/` 起头：** 提纲第九节的四道题，契约用 `Design`，信号按「容量→链路→一致性→扩展点」写。素材可以读 `markdownfile/面试/IM.org`、`限流.org`、`design/design_twitter.md`，原文件不动。
5. **补基础深挖的缺口：** 落在已有主题文件的新节点里（如 `jvm` 加「容器」，`并发` 加「CompletableFuture」「HashMap」，`redis` 加「Cluster」，`redis` 的「分布式锁」加 Redlock 争议）。Docker/K8S 和 Linux 排查命令如果放不进现有主题，就新建 `20-主题/容器与排查.md`。
6. **修数据：** `JAVA-01` 的契约改成 `Mechanism` 或 `Tradeoff`；`JVM-02` 补追问链。
7. **`设计.md` 第六节「目前状态」重写**成现在的样子，那一节还停在「10 道 Redis 题」。

## 不做

- 不填 `00-叙事/证据.md` 和 `regin.md` 里的 `[填]`。这些数字只有本人能给，代写就等于编数字，正好撞上 `fabricated` 门禁。新题只负责把证据编号挂上，让练习时撞到门禁。
- 不改 `prep.py` 的格式、队列或判分规则。
- 不转换、不删除 `markdownfile/面试/*.org`。
- 不刷题，不往 `attempts.db` 写记录。

## 验收

每条对应一个测试，放在 `interview/服务/test_bank.py`（标准库 `unittest`，和 `prep.py` 一样零依赖，在 `interview/` 下跑 `python3 -m unittest 服务/test_bank.py`）。测试只读 markdown 和 git，不碰 `attempts.db`。

1. `test_interview_tree_committed`：`git status --porcelain -- interview/` 为空（`attempts.db` 被忽略）；根目录 `CLAUDE.md` 不再说 cheatsheet 在 `markdownfile/面试/`。
2. `test_bank_has_no_duplicate_ids`：`load_bank()` 返回的题号冲突列表为空。
3. `test_every_question_has_signals`：每题的 `signals` 都不为空。
4. `test_no_retired_or_missing_contract`：没有 `structure` 为 `Knowledge` 或 `None` 的题。
5. `test_follow_ups_except_hr`：除 `HR-` 前缀以外，每题都有追问链。
6. `test_resume_projects_covered`：`ORM切换`、`去哪儿`、`中国软件`、`regin` 四个项目主题都存在，每个 ≥ 6 题。
7. `test_system_design_seeded`：`30-系统设计/` 下 ≥ 4 题，契约全是 `Design`。
8. `test_fundamentals_have_nodes`：提纲第八节的每一组（Java/JVM、并发、Spring、Redis、Netty、MQ、MySQL、网络/OS/Linux、Docker/K8S）都能在某个主题里找到对应节点。对应关系在测试里写成一张表。
9. `test_evidence_refs_resolve`：每个 `证据: EV-xx` 引用都能在 `00-叙事/证据.md` 里找到。
10. `test_design_doc_status_current`：`设计.md` 第六节写出的题数等于 `load_bank()` 的实际题数。

## 约束

- 提交用 Conventional Commits，scope 写 `面试`，正文用中文。
- 题号稳定，永不复用；删题只留空号。新前缀不要和已有前缀冲突（先跑一遍 `prep_list`）。
- 新增和改写的题面、信号、参考答案都按仓库的中文写作规则走（`zh-prose` / `zh-terms`），尤其是信号要写成「能核对的一句话」，不写「理解 xxx」。
