# Telegram Intent Processor

一个 Telegram 风格的群消息处理器，重点展示真实后端边界：**意图识别、群级状态、并发处理、Redis 持久化、Redis 断连降级、异常消息隔离和实时 Dashboard。**

---

## 快速启动

```bash
# 1. 启动 Redis（二选一）
docker compose up -d redis          # Docker（推荐）
# 或: sudo apt install redis-server && redis-server --daemonize yes

# 2. 启动后端
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

打开 **http://localhost:8000/dashboard** 进入实时面板。

> Redis 启动后 `/health` 显示 `healthy`，消息写入 Redis。
> 没有 Redis 时系统使用本地存储模式，`/health` 显示 `local`，功能不受影响。

---

## Dashboard 功能

单文件 HTML 面板，零前端构建：

| 区域 | 内容 |
|------|------|
| 系统健康 | Redis 状态、已处理/无效/重复/降级写入计数、In-flight Messages |
| 实时消息流 | 消息 ID、群 ID、用户 ID、文本、处理状态 |
| 分类结果 | 意图、标签、优先级、错误原因 |
| 群状态面板 | 当前状态、意图计数分布、最新意图、是否需人工关注 |

### Demo 按钮

| 按钮 | 行为 |
|------|------|
| Send Sample Message | 发送一条正常消息，展示完整处理结果 |
| Run Visual Burst | 100 条消息、10 个群，展示实时并发处理 |
| Run Load Demo | 1200 条消息、50 个群，展示压力下的状态一致性 |
| Simulate Redis Down | 临时断开 Redis，验证降级不丢数据 |
| Recover Redis | 尝试重连 Redis，恢复后自动切回 |
| Send Malformed Message | 发送坏消息，验证安检不崩溃 |

---

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/messages` | 处理单条消息 |
| POST | `/messages/batch` | 批量处理，返回汇总统计 |
| GET | `/groups/{group_id}/state` | 查看群状态 |
| GET | `/stats` | 处理统计（独立于 health） |
| GET | `/health` | Redis 状态和系统健康 |
| GET | `/dashboard` | 实时 Dashboard 页面 |
| GET | `/events` | SSE 事件流 |

API 文档：**http://localhost:8000/docs**

### 请求示例

```json
POST /messages
{
  "message": {
    "message_id": "msg_001",
    "group_id": "group_01",
    "user_id": "user_01",
    "text": "这个多少钱"
  }
}
```

### 响应

```json
{
  "message_id": "msg_001",
  "group_id": "group_01",
  "user_id": "user_01",
  "text": "这个多少钱",
  "intent": "pricing",
  "tags": ["sales_lead", "needs_reply"],
  "status": "processed",
  "reason": "",
  "group_state_after": "PRICING_DISCUSSION",
  "fallback_used": false
}
```

---

## 测试

```bash
cd backend
source .venv/bin/activate
pytest tests/ -v
```

覆盖 6 类边界契约：

| 测试类 | 证明什么 |
|--------|----------|
| 意图分类 | 5 种意图 + context-aware billing_dispute |
| 群状态机 | 完整生命周期，投诉最高优先级 |
| 异常消息隔离 | 缺字段/空文本被拒绝，服务不崩溃 |
| Redis 降级 | 断连时 fallback 可用，health 报 degraded |
| 并发一致性 | 多群并发无状态丢失，同群计数精确 |
| 重复幂等 | 同 message_id 第二次标 duplicate，不重复计数 |

---

## 设计说明

**意图分类。** 关键词 + 正则规则，不接 LLM。投诉优先级最高，优先级链条：complaint > help > pricing > product > other。支持群上下文感知——COMPLAINT_ESCALATED 群中的询价会额外打 `billing_dispute` 标签。

**群状态机。** 每个群维护一个状态机：IDLE → PRODUCT_DISCUSSION → PRICING_DISCUSSION → SUPPORT_NEEDED → COMPLAINT_ESCALATED。投诉直接覆盖任何状态，并设置 `needs_human_attention`，不自动消除。

**并发模型。** 全局 `asyncio.Semaphore` 限制 In-flight Messages（默认 50）。每个群独立 `asyncio.Lock`，同群内串行处理，不同群间完全并行。

**去重。** Redis 路径用 `SET key value NX` 原子化查重，不存在 TOCTOU 窗口。降级模式下用 per-message 内存锁保证等价语义。

**存储降级。** Redis 不可用自动切到内存 dict，同时暴露 `degraded` 状态。API 返回、Dashboard 健康面板、统计计数器中均体现。Redis 恢复后自动重连。

---

## 取舍

**不做的：**

- 不接 LLM —— 关键词规则可预测、零延迟、零外部依赖
- 不做 React 前端 —— 单文件 HTML + SSE，零构建
- 不接真实 Telegram Bot Token —— HTTP API 模拟消息流
- 不做登录系统 —— 演示系统，不需要 auth
- 不做大规模压测平台 —— 内置 load demo 已够展示并发能力

**做了且有意这么做的：**

- 分类器接受 `group_context` 参数，支持上下文感知，但不依赖上下文做意图反转——关键词仍然决定意图，上下文只影响标签
- `OTHER` 意图不改变群状态——"好的谢谢" 不应该冲掉之前的讨论状态
- 每个处理结果都有一个明确的状态枚举，不存在隐式结果
- `needs_human_attention` 是**粘性的**——一旦某群出现投诉并设为 True，不会自动清除。这是业务设计：被投诉过的群值得持续关注，即使之后消息正常
