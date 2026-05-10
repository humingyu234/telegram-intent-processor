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

## 阶段 2：FastAPI + Redis + Dashboard — 已完成 (2026-05-10)

### 做了什么

| 文件 | 内容 |
|------|------|
| `backend/app/store.py` | Redis 主存储 + 内存 fallback，degraded 标志，计数器 |
| `backend/app/processor.py` | 消息处理管线：校验→去重→分类→加锁→状态更新→存储→SSE 推送 |
| `backend/app/main.py` | FastAPI 应用：/process、/process/batch、/dashboard、/events、/health、6 个 demo 端点 |
| `backend/app/dashboard.html` | 单文件 Dashboard：系统健康、实时消息流、分类结果、群状态面板、Demo 控制 |
| `backend/tests/test_processor.py` | 13 条边界测试：异常隔离、Redis fallback、并发一致性、重复幂等 |

### 环境

- Python 3.12.3，虚拟环境 `backend/.venv`
- 新增依赖：fastapi 0.136.1、uvicorn 0.46.0、redis 7.4.0
- 测试结果：**59 passed，0 failed**

### 关键设计决策

1. asyncio.Semaphore 控制全局并发上限（默认 50），按 CLAUDE.md 术语标注 "In-flight Messages"
2. 每个 group_id 独立 asyncio.Lock，不同群可并发、同群内串行
3. Redis 连接失败自动降级到内存 dict，暴露 degraded 状态
4. SSE 用 asyncio.Queue 广播，Dashboard 实时更新
5. Demo 端点: sample / burst (100msg) / load (1200msg) / redis-down / redis-recover / malformed
6. Dashboard 单文件 HTML，零前端构建

### 故意没做

- 没有接真实 Redis（当前开发环境无 Redis，启动即 degraded）
- 没有做压测/监控系统

---

### UX polish (2026-05-10)

- 默认状态改为 "local"（没有 Redis 但健康），不再是 "degraded"
- degraded 只在 Redis 连接成功后断开时触发
- 健康面板拆为 Total Processed / Redis Persisted / Fallback Writes
- 新增 Reset Demo State 按钮，一键清空所有状态和计数
- Group State Panel 意图计数格式化显示
- README 说明 needs_human_attention 粘性设计

---

## 阶段 3：README + 收尾 — 已完成 (2026-05-10)

- 添加 README.md（项目描述、快速启动、Dashboard 说明、API 文档、测试命令、设计说明、取舍）
- 添加 requirements.txt
- 更新 PROGRESS.md

### 如何运行

```bash
cd backend
source .venv/bin/activate
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
# Dashboard: http://localhost:8000/dashboard
# API docs: http://localhost:8000/docs
```

### 如何测试

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```
