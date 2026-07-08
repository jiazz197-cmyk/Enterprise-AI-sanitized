# 对话工作流（LangChain 底座，弃用 Dify）

本文记录把原 Dify `advanced-chat` 应用（`对话工作流 qwen3.5版.yml`，已删除）迁移到 yamato 进程内、以 langchain 为底座的实现。前端不再直连 Dify，改为调用 yamato 的 Dify 兼容 SSE 端点。

## 分层（遵循 Route → UseCase → Port → Adapter）

| 层 | 路径 | 说明 |
|----|------|------|
| Route | `app/api/v1/conversation.py` | Dify 兼容 SSE 端点；JWT 鉴权；翻译 UseCase 流事件为 SSE 字节 |
| UseCase | `app/usecases/conversation/{run,list,rename}.py` | 记忆装载/覆盖、调工作流、持久化、列表、重命名 |
| Port | `app/ports/domains/conversation.py`、`app/ports/dto/conversation.py` | `ConversationRepoPort`、`ConversationWorkflowPort`、`WebSearchPort`、`UserProfilePort` |
| Adapter | `app/adapters/conversation/` | `SqlAlchemyConversationRepoAdapter`、`TavilyWebSearchAdapter`、`LangChainConversationWorkflowAdapter`、`deps.py` |
| Integration | `app/integrations/conversation/` | langchain LCEL 链：关键词拆分 / 问题分析 / 答案生成（流式） |
| Domain | `app/domain/conversation/` | 纯函数：提示词常量、`<think>` 剥离、搜索结果筛选、双通记忆拼装 |
| ORM | `app/models/orm/conversation.py` | `Conversation`（`long_memory`/`recent_dialogs` JSONB）、`Message` |

`app/api/v1/conversation.py` 与 `app/usecases/**` 不得 import `app.integrations`（guard 脚本强制）。

## 工作流节点映射（Dify yml → langchain）

- **记忆管理**（Start→背景清洗→判断覆盖→双通记忆）：落在 `RunConversationUseCase`；`background` 非空时清空 `long_memory`+`recent_dialogs` 并写入 `background`。双通记忆模板为 domain 纯函数 `assemble_dual_memory`。
- **获取当前时间**：`app/usecases/conversation/run.py:_now_date_str`（Asia/Shanghai `%Y-%m-%d`）。
- **获取用户习惯**：复用 `ChatSummaryRepoPort.get_latest_summary`（进程内，替代 HTTP 回调）。
- **关键词拆分 / 问题分析**：Qwen3-8B（thinking 关闭），langchain `ChatPromptTemplate | ChatOpenAI | StrOutputParser`。
- **本地检索**：`RetrieverPort.query_db`，`instance_id` 1=表单数据、2=离散知识，collection=`doc_collection_{n}`（与 `app/api/v1/retriever.py` 一致）。
- **联网搜索**：`WebSearchPort`（`TavilyWebSearchAdapter`，httpx 直连 Tavily API，替代 SearXNG）。`搜索筛选`/`搜索内容筛选` 代码节点 → domain `filter_by_relevance`/`filter_by_time`。
- **答案生成**：Qwen3.6-35B-A3B（streaming，thinking 开启），`<think>` 由 `ThinkStreamFilter` 流式剥离。
- **近期对话存储**：domain `format_dialog_line`，UseCase 在流结束后 append 到 `recent_dialogs` 并落库。
- **三路条件分支**：`ConversationPipeline` 按 `search_mode` 选择管线；UseCase 仅透传。
- **订单询价表格提示词**：本地&网络、本地检索两路答案提示词末尾的「涉及到的产品参数」表格要求，逐字保留于 `app/domain/conversation/prompts.py`。

## 数据存储（替代 Dify 拥有的会话/消息）

- `conversations`：`id`(UUID)、`owner_id`、`name`、`long_memory`(JSONB list[str])、`recent_dialogs`(JSONB list[str])、时间戳。
- `messages`：`id`、`conversation_id`(FK CASCADE)、`role`、`content`、`seq`、`created_at`。

`ConversationRepoPort`（`app/adapters/conversation/persistence.py`，基于 `AsyncSessionLocal`）是会话/消息/记忆的**唯一真相源**。

## Dify 兼容 SSE

`POST /api/v1/chat-messages` 输出 Dify 形态事件：

```
data: {"event":"message","task_id":"...","id":"...","conversation_id":"...","answer":"<chunk>","created_at":<ts>}\n\n
...
data: {"event":"message_end","task_id":"...","id":"...","conversation_id":"...","metadata":{"usage":{...},"retriever_resources":[]}}\n\n
```

`POST /chat-messages/{task_id}/stop` 设置内存取消标志，工作流在 token 间检查 `cancel_checker` 协作式停止。

## 关联改造（脱离 Dify）

- `chat-summary`：`MessageExtractorChatArchiveAdapter` 改读本地 `messages` 表（`fetch_user_queries`）；LLM 摘要仍走 langchain→vLLM。
- `context-compression`：`IntegrationContextCompressorAdapter` 注入 `ConversationRepoPort`，从 `conversations` 行读 `long_memory`/`recent_dialogs`，不再调 Dify 变量接口。

## 配置

- 新增：`QWEN3_8B_MODEL`、`WEB_SEARCH_PROVIDER`（默认 `tavily`）、`TAVILY_API_KEY`。
- 移除：`DIFY_BASE_URL`、`DIFY_API_KEY`、`CHAT_API_KEY`（对话端点改用 JWT）。
