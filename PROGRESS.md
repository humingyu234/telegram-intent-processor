# 项目进度日志

## 阶段 1：核心模型 + 分类器 — 已完成 (2026-05-10)

### 做了什么

| 文件 | 内容 |
|------|------|
| `backend/app/models.py` | Pydantic 模型定义：Intent、GroupState、ProcessingStatus、IncomingMessage、ProcessingResult、GroupSnapshot、SystemHealth |
| `backend/app/classifier.py` | 关键词+正则意图分类器，支持 context-aware `classify_message(text, group_context=None)` |
| `backend/app/state.py` | 群级状态机：IDLE → PRODUCT/PRICING/SUPPORT/COMPLAINT_ESCALATED，投诉最高优先级 |
| `backend/tests/test_classifier.py` | 31 条测试：5 种意图 + 标签 + billing_dispute 上下文感知 |
| `backend/tests/test_state.py` | 11 条测试：状态转换 + 生命周期 + 投诉升级 + 快照 |

### 环境

- Python 3.12.3，虚拟环境 `backend/.venv`
- 阶段 1 依赖：pytest 9.0.3、pytest-asyncio 1.3.0、pydantic 2.13.4
- 测试结果：**46 passed，0 failed**

### 关键设计决策

1. 分类器关键词优先，不接 LLM
2. 投诉在分类和状态机中都是最高优先级
3. `classify_message` 支持 group_context，COMPLAINT_ESCALATED 下的定价消息打 `billing_dispute` 标签
4. OTHER intent 不改变群状态（中性消息）
5. Pydantic 在入口做校验，内部代码不重复检查

### 故意没做

- 没有 FastAPI / uvicorn 主应用
- 没有 Redis 集成
- 没有 Dashboard
- 没有并发控制
- 没有 README（按 CLAUDE.md 要求，演示阶段再做）

---

## 阶段 2：FastAPI + Redis + Dashboard — 待开始

依赖：fastapi、uvicorn、redis、httpx-sse（或 sse-starlette）

内容：
- 主应用入口 /process
- Redis 存储 + 内存 fallback
- asyncio.Semaphore 并发控制 + per-group lock
- message_id 幂等/重复检测
- SSE Dashboard /dashboard
- Demo 端点
- 剩余边界测试（异常消息隔离、Redis fallback、并发一致性、重复幂等）
