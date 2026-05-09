# CLAUDE.md

本项目是 Kapibala 面试交付项目：

`telegram-group-intent-processor`

目标：
实现一个具备工程交付质量的 Telegram 群消息处理器。

它需要支持：
- 接收 Telegram 风格的群消息流
- 识别用户意图
- 给消息打业务标签
- 写入 Redis
- 维护群级对话状态
- 处理并发消息
- 处理 Redis 断连降级
- 隔离异常消息
- 提供实时 Dashboard 展示
- 提供关键单元测试

技术栈固定：
- Python
- FastAPI
- asyncio
- redis.asyncio
- Pydantic
- pytest / pytest-asyncio
- 单文件 HTML Dashboard + SSE
- 不使用 React/Vite/TypeScript 前端

不做的事：
- 不接真实 Telegram Bot Token
- 不做登录系统
- 不调用 LLM API
- 不做 React 前端
- 不做大型压测平台
- 不做生产部署系统
- 不添加无关架构

这个项目要像一个可运行的小型工程系统，而不是一个脚本作业。

---

## Skill 1：范围守护

每次实现都使用这个 skill。

目标：
快速推进，但不乱扩范围。当前阶段要完整交付，不要做飞。

当前项目范围：
一个 FastAPI 消息处理系统，包含：
- 消息校验
- 意图识别
- 业务标签
- 群级状态机
- Redis 存储
- Redis fallback
- 并发处理
- SSE Dashboard
- 可视化 demo
- load demo
- 单元测试
- README

规则：
1. 写代码前，先用一句话说明当前阶段只做什么。
2. 当前阶段要做完整，不要半成品。
3. 除非用户明确要求只讨论，否则不要只停留在计划。
4. 不引入当前阶段不需要的重框架。
5. 出现有趣但超范围的想法，放到 Future Work，不要塞进当前实现。
6. 保证评审者可以用简单命令跑起来。
7. 优先写稳定、清楚、可维护的代码，不追求炫技。

每个阶段完成后必须汇报：
- 改了什么
- 改了哪些文件
- 怎么运行
- 做了哪些验证
- 哪些内容是故意没做的

阶段完成标准：
- 代码已实现
- 相关测试已添加
- 测试或 smoke check 已运行
- 如果影响用户使用，README 或说明已更新

危险信号：
- 引入 React/Vite
- 接真实 Telegram Bot Token
- 接 LLM API
- 做完整监控系统
- demo 没跑通前先写一堆抽象
- 修改无关文件

---

## Skill 2：后端可靠性

实现消息处理、Redis、状态更新、并发时使用这个 skill。

目标：
展示真实后端工程判断，而不是只做功能翻译。

核心契约：
系统在正常消息、并发消息、异常消息、重复消息、Redis 失败时，都要安全处理。

必须采用的设计：
1. 用 Pydantic 做输入校验。
2. 用 `message_id` 做幂等和重复消息检测。
3. 用 `asyncio.Semaphore` 限制正在处理中的消息数量。
4. 用每个 `group_id` 独立的锁，保证群级状态一致。
5. 不同群可以并发处理。
6. 同一个群内部的状态更新必须安全、有序。
7. 用 Redis 存储消息和群状态。
8. Redis 失败时，降级到内存 fallback store，并暴露 degraded 状态。
9. 异常消息不能让服务崩溃。
10. 每次处理结果都要有清晰状态。

术语要求：
- Dashboard 上写 `In-flight Messages`，不要写 `Current Concurrency`。
- 因为 asyncio 是协程并发，不是多线程并行。

消息处理流程：
1. 接收原始消息
2. 用 Pydantic 校验
3. 异常消息返回 invalid，并给出原因
4. 检查 `message_id` 是否重复
5. 识别 intent
6. 生成 tags
7. 获取当前 group 的锁
8. 更新群级状态
9. 写入 Redis
10. 如果 Redis 失败，写入 fallback store
11. 通过 SSE 推送结果到 Dashboard
12. 返回结构化处理结果

意图分类：
- `pricing`：询价
- `product`：产品咨询
- `help`：求助
- `complaint`：投诉
- `other`：其他

群级状态机：
- `IDLE`：空闲
- `PRODUCT_DISCUSSION`：产品讨论中
- `PRICING_DISCUSSION`：询价讨论中
- `SUPPORT_NEEDED`：需要支持
- `COMPLAINT_ESCALATED`：投诉升级

状态规则：
- 询价消息让状态变成 `PRICING_DISCUSSION`
- 产品消息让状态变成 `PRODUCT_DISCUSSION`
- 求助消息让状态变成 `SUPPORT_NEEDED`
- 投诉消息让状态变成 `COMPLAINT_ESCALATED`
- 投诉状态必须设置 `needs_human_attention = true`
- 投诉拥有最高业务优先级

处理结果状态：
- `processed`
- `duplicate`
- `invalid`
- `fallback`
- `failed`

不要隐藏失败：
如果使用了 fallback，必须在这些地方体现：
- API 返回结果
- Dashboard 健康状态
- 统计 counter
- 测试用例

---

## Skill 3：边界测试设计

添加或修改核心逻辑时使用这个 skill。

目标：
测试要证明关键风险被处理，而不是只测 happy path。

最低测试矩阵：

1. 意图识别测试
   测试名：`test_classifier_detects_core_intents`
   输入：价格、产品、求助、投诉、无关消息。
   期望：识别出正确 intent，并生成合理 tags。

2. 群状态机测试
   测试名：`test_group_state_machine_transitions`
   输入：同一个群内依次出现：产品咨询 -> 询价 -> 求助 -> 投诉。
   期望：群状态正确变化，投诉会升级，并设置 `needs_human_attention = true`。

3. 异常消息隔离测试
   测试名：`test_malformed_message_is_rejected_without_crashing`
   输入：缺少 `text`、缺少 `user_id`、字段类型错误、空文本。
   期望：结果为 `invalid`，包含错误原因，服务继续运行。

4. Redis 断连 fallback 测试
   测试名：`test_redis_disconnect_uses_fallback_store`
   输入：Redis store 抛连接错误时处理一条正常消息。
   期望：消息仍然返回 fallback 结果，fallback counter 增加，系统健康状态为 degraded。

5. 并发状态一致性测试
   测试名：`test_concurrent_messages_keep_group_state_consistent`
   输入：多条消息并发进入，分布在多个 group。
   期望：总处理数正确，每个 group 的计数正确，状态没有丢失或覆盖。

6. 重复消息幂等测试
   测试名：`test_duplicate_message_id_is_not_counted_twice`
   输入：同一个 `message_id` 提交两次。
   期望：第一次是 `processed`，第二次是 `duplicate`，群计数不会重复增加。

测试风格：
- 测试要确定性强
- 尽量避免 sleep
- Redis 失败要显式 mock
- 测行为，不要测内部实现细节
- 测试名要说明业务契约
- 测试要让面试官读得懂

验证命令：
```bash
pytest
```

汇报完成前必须说明：
- 多少测试通过
- 是否有跳过
- 是否还有已知限制

---

## Skill 4：演示优先交付

构建 README、Dashboard、demo 接口、最终 polish 时使用这个 skill。

目标：
让评审者快速看懂、快速跑起来、快速感受到价值。

评审者体验目标：
- 5 分钟内知道项目做什么
- 10 分钟内跑起 Dashboard
- 30 分钟内能看测试和设计取舍

Dashboard 要求：
由 FastAPI 提供单文件页面：`/dashboard`
不需要前端构建步骤。

Dashboard 分区：
- 系统健康：Redis 状态（healthy / degraded）、processed messages、invalid messages、duplicate messages、fallback writes、in-flight messages、max in-flight limit
- 实时消息流：message id、group id、user id、text、status
- 分类结果：intent、tags、priority、reason
- 群状态面板：current state、intent counts、last intent、recent tags、needs human attention
- Demo 控制区：Send Sample Message、Run Visual Burst、Run Load Demo、Simulate Redis Down、Send Malformed Message

Demo 行为：
- Send Sample Message：发送一条正常消息，并立即展示处理结果。
- Run Visual Burst：处理 100 条消息，分布在 10 个群。目的：清楚展示实时处理流程。
- Run Load Demo：处理 1000+ 条消息，分布在 50 个群。目的：展示并发处理能力和状态一致性。注意：不要把每条消息都渲染出来，只展示汇总结果：processed、invalid、duplicates、groups、duration、fallback writes、state consistency。
- Simulate Redis Down：临时让 Redis 写入失败。期望：Redis 状态变 degraded，fallback writes 增加，服务继续运行。
- Send Malformed Message：发送坏消息。期望：invalid counter 增加，出现错误原因，服务继续运行。

README 第一屏必须包含：
- 一句话项目描述
- 它解决什么问题
- 快速启动命令
- Dashboard 地址
- demo 按钮说明
- 测试命令
- 设计说明
- 取舍 / non-goals

README 语气：
不要把它写成玩具或作业。要写成一个小型、production-minded 的消息处理系统。

推荐描述：
"本项目实现了一个 Telegram 风格的群消息处理器，重点展示真实后端边界：意图识别、群级状态、并发处理、Redis 持久化、Redis 断连降级、异常消息隔离和实时 Dashboard。"

---

## Skill 5：审查响应模式

每个阶段完成后、最终交付前、或者 review diff 时使用这个 skill。

目标：
即使只有一个模型，也要制造严格 review 流程。

重要规则：
进入 reviewer 模式时，先不要改文件。
先审查，再等用户确认是否修改。

Reviewer 模式输入材料：
- git diff
- 文件路径
- 测试输出
- README
- 如有必要，Dashboard 运行表现或截图

Review 输出按严重程度分类：

P0 = 交付前必须修
包括：正确性 bug、数据丢失、启动失败、测试失败、并发状态损坏、Redis fallback 实际不可用、Dashboard 无法运行

P1 = 建议修，提高质量
包括：README 不清楚、错误信息弱、命名容易误解、缺少重要测试、Dashboard 没展示关键状态

P2 = 可选 polish
包括：样式优化、文案优化、额外例子、小重构

每个问题必须包含：
- 文件路径
- 函数或区域
- 为什么重要
- 最小修复建议

Reviewer checklist：
1. 是否符合原始题目？
2. 是否容易运行？
3. Dashboard 是否能快速展示价值？
4. 消息校验是否能安全拒绝坏输入？
5. Redis 失败时是否真的 fallback，而不是崩溃？
6. 并发处理是否保护了每个 group 的状态？
7. 重复消息是否不会重复计数？
8. 测试是否覆盖关键风险？
9. README 是否讲清楚运行方式和设计取舍？
10. 是否避免了不必要的范围扩张？

不要做：
- 不要重写整个架构
- 不要建议 React 前端
- 不要建议 LLM 分类
- 不要建议真实 Telegram Bot Token 接入
- 不要编造生产级使用结论
- 不要把猜测问题标成 P0

Review 后：
- 如果有 P0，先修 P0
- 如果只有 P1/P2，询问用户是继续 polish 还是准备交付

单模型 reviewer 的做法：
不要让同一个会话一边写一边夸自己。
更好的流程是：
- 会话 A：实现
- 会话 B：只读 review
- 会话 B 只给它：原始题目、CLAUDE.md、git diff、测试输出、README
- 不要给它实现会话的讨论过程
- 要求：你现在只做 reviewer，不要修改文件。按 P0/P1/P2 找问题。每个问题必须有文件路径、原因和最小修复建议。

另外，工具本身也是 reviewer：
- pytest
- ruff
- uvicorn 启动检查
- Dashboard 手动点击
- Redis down 手动验证

最终质量不靠"相信 AI"，而是靠：
- CLAUDE.md 限制范围
- 分阶段实现
- 测试验证
- 单独 review 会话
- 工具检查
- 最后手动验收
