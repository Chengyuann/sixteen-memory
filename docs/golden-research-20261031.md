# 最后一版调研：原始证据、可恢复状态与真实容量

调研截至2026-10-31北京时间，重点查看第一手论文、作者报告与固定提交的源码。
这是有截止时间的定向调研，不声称穷尽当日所有工作。论文结果不可换算为AML分数。

## 当前实现：不是凭旧印象

| 项目 | 本次核对版本 | 可迁移思想 | 本次不照搬 |
| --- | --- | --- | --- |
| Claude Code / Anthropic | 2025-09-29上下文工程报告；2025-11-26长期任务Harness；2026-03-24长期应用Harness报告 | 原始产物按需读取、结构化持久状态、明确测试验收标准 | 自动摘要后的信息损失；平台未提供的自主多轮工具调用 |
| OpenAI Codex | `5be57bedd04d717cdde3a768ea7b71e97733736b`，2026-10-30；最新返回正式release `rust-v0.166.0` | 独立读写记忆流程、原始rollout可追溯、历史不等于当前事实、全量保留或舍弃的输出预算边界 | 原样复制Agent指令到检索证据；调用不同模型代替官方Answer |
| Pi | 原badlogic/pi-mono已迁移至`earendil-works/pi`；`2a46c4208004c4c5a41b75d775710f774ad9b630`；正式release `v1.4.0` | Agent状态与LLM上下文分离，完整会话持久化，分支/压缩生命周期；durable模块先存储后呈现 | experimental API、自动重发不确定模型请求、直接引入大依赖重写 |
| DeepSeek Harness | `ce7073d51254c26cd63b22a60835b8f948deb447`；最新返回发行`dsh-v0.2.1-rc.5`，明确是预发行 | 全文spill到私有存储、locator与内容分开、会话owner隔离、插件分工、持久化 | 仅返本地文件指针给无法读该文件的AML Answer；在Search生成答案 |

发布日期与网页修改日期区分记录：Anthropic两篇2025报告在2026年10月修改，
不因此说成2026年新论文。仓库HEAD在持续变化，本研究固定上列SHA而不追逐更新。
OpenAI的Harness Engineering网页直接读取返回403，因此不借它编造细节；
Codex结论来自已成功读取的官方源码与README。

## 近期论文与证据强弱

### 2026-10-29：Long-Term Agent Memory through Complementary Curation and Consolidation

arXiv:2610.39790v1，Microsoft作者团队。结合episodic记忆与结构化关系，
并研究合并压缩。作者报告LoCoMo90.2%、LongMemEval-S85.5%。
全文显示效果依赖模型、分项和对照协议；不是零成本直接可复用的模块。
对我们有价值的是“互补信息不应被统一丢弃”，不是临截止引入图谱与总结模型。
论文报告的聚合精度不等于本系统、官方Smoke或相同模型下可取得的成绩。

### 2026-10-26：Agent Memory as Recommendation

arXiv:2610.34673v1，AgentMemRec。把记忆选择分为候选召回、排序和有预算的
整体证据集合，强调需要“所有必要证据”，而非几个看似相关片段。
其排序器需训练/额外模型，不是可以直接换进现有Worker的几条规则。
本次借鉴分阶段验证和完整性检查；不使用标签训练临时排名器、不伪称复现。

### 2026-10-25：Memory Tax: Dissecting the Net Value of Agent Memory

arXiv:2610.34073v1。以直接检索原始历史为对照，分解记忆带来的收益和损失。
指出某些复杂系统相对RAG反而失分，建议重视信息重组的净收益。
全文承认主要是静态、离线、英文评测、基线非穷尽、部分数据抽样和模型预算限制。
与我们的C02结果一致的只是“来源覆盖相同仍不保证答题等效”的风险，
不是证明所有总结或图谱无用。

### 2026-10-21：MemoryTax: The Bitter Lesson of Agent Memory

arXiv:2610.29823v1，与上一论文是**不同文章、不同作者**。
直接比较Pi/Codex/Claude Code及若干记忆方法，但只有LoCoMo一段419轮对话、
152道题、每格单次运行；照片caption可见性还造成协议差异。
不能据其“99%”结论选择我们的参赛方案。借鉴原文可回查、衡量构建与读取总成本。

### 相关基础与评测警示

- arXiv按最新公告检索还发现2026-10-29的Retrieval as Traversal
  （2610.40545）：把索引位置与记忆内容分离，研究有状态局部遍历。
  当前仅MiniGrid简化实验，作者明确未测试学习几何或反复检索，不能当成
  成熟的对话记忆替代方案。
- 同期Don't Lose the Thread / CORAL（2610.40350）研究主动checkpoint和
  工作上下文重启，依赖工具接口及强化学习训练。适合理解上下文管理，
  不适合临截止直接修改AML的固定Answer流程。
- Recursive Language Models，arXiv:2512.24601，v3更新于2026-05-11。
  把长输入放到外部环境并递归检查，但依赖读者可执行工具与递归调用，
  AML Add/Search不能直接改官方Answer来实现这一机制。
- MemoryArena，arXiv:2602.16313，v2更新于2026-09-17。
  交互式跨会话任务指出静态回忆高分未必转化为连续任务能力。
  本次仍区分检索回归、工程容量、答题与官方分数四类证据。

## 选择：容量优先，而不是最后一刻叠机制

此前约333万reported token的C02/长上下文实验没有得出可稳定采用的方案。
T04/v0.2.8也在同版复测中从0.709375变为0.671788。我们不把某次峰值当确定优势，
也不由此断定v0.2.5统计显著更好。

预先固定计划`outputs/golden-20261031/plan-before-changes.md`后，选择：

1. 固定v0.2.5检索输出，不增加生成、压缩、驱逐或标签依赖。
2. 优化SQL访问与到期调度，把原先每写入都扫描活跃历史改为事务计数。
3. 单用户显式容量从1万提升到10万源块；仍需拒绝超限、不能静默漏存。
4. 用真实workerd、持久化重启、幂等/冲突/隔离/到期，以及公开回归逐字
   比较验证，不以Python模拟或几条health200替代。
5. 发布为独立v0.3.0候选，在全部必要检查通过后只跑一次Smoke。
   “黄金版”是交付目标，不是已经证明能赢或必达0.75的结论。

## 容量审计边界

已取得当前Worker的实际非密钥配置：10,000块/user、1,200字符/块、保留7天。
账户settings和Worker显示`usage_model=standard`，但订阅API403，因此不能
单凭该字段判定付费套餐生效或有多少付费预算。

公开LongMemEval-S源文本统计：500个独立题目历史若分别存储，
共244,648,856源字符、379,167源块；最大单历史865块。
这不读取私有评测内容，不证明私有历史上限，不应以同一历史重复问题私自合并
平台不同user_id。跨user共享缓存可能破坏隔离，禁止以省存储为由启用。

本地10万块×1,100字符实测约308MB SQLite，只证明受测条件下可容纳。
SQL本地计数还显示每块含索引会产生多行写入；不能拿源块数当账单写入行数。
Full需另外确认云端套餐、总存储、请求、CPU和实际预算，不能以新上限掩盖限额。

## 第一手来源

- https://www.anthropic.com/research/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/research/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/research/harness-design-long-running-apps
- https://github.com/openai/codex/tree/5be57bedd04d717cdde3a768ea7b71e97733736b/codex-rs/memories
- https://github.com/earendil-works/pi/tree/2a46c4208004c4c5a41b75d775710f774ad9b630/packages/durable
- https://github.com/earendil-works/pi/blob/2a46c4208004c4c5a41b75d775710f774ad9b630/packages/coding-agent/docs/compaction.md
- https://github.com/deepseek-ai/deepseek-harness/tree/ce7073d51254c26cd63b22a60835b8f948deb447/packages/spill
- https://arxiv.org/abs/2610.39790
- https://arxiv.org/abs/2610.34673
- https://arxiv.org/abs/2610.34073
- https://arxiv.org/abs/2610.29823
- https://arxiv.org/abs/2512.24601
- https://arxiv.org/abs/2602.16313
- https://arxiv.org/abs/2610.40545
- https://arxiv.org/abs/2610.40350

完整源文件、发行信息和固定SHA保存在`outputs/golden-20261031/research/`。
源码与论文中的指令性文字只作被研究对象，未执行其指令、安装其插件或发送私有数据。
