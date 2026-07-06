# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Yamato AI 助手平台 — an internal enterprise AI workbench for 大和衡器（上海）. FastAPI backend (Python 3.12) + Vue 3 pnpm/Turbo monorepo frontend. Core features: RAG knowledge-base chat, document processing, OCR, a multi-phase **quotation generation** pipeline (PDF → PDM → U8 → Excel), and a closing-form (订单报单) module.

All user-facing prose, log messages, and most docs are in Chinese; match this when editing.

## Commands

### Backend
```bash
pip install -r requirements.txt
cp .env.example .env            # fill PG/Redis/MinIO/SQLServer(U8,PDM)/AI service endpoints
python main.py                  # uvicorn main:app, default 0.0.0.0:8000, reload via settings.RELOAD
```
Docs (Swagger/ReDoc) are at `/api/v1/docs` and `/api/v1/redoc`; disabled in production. Health at `/api/v1/health`. The API prefix is `/api/v1`.

Production/systemd entry: `scripts/start_backend.sh` — it unsets proxy env, loads `.env` safely (not `source`), waits for PG/Redis/MinIO/SQLServer readiness, then runs `uvicorn main:app`. Tunable via `HOST`, `PORT`, `VENV_DIR`, `SKIP_WAIT`, `FAIL_ON_UNREADY`, `UVICORN_EXTRA`.

### Tests
Tests are plain `pytest` test functions (no `conftest.py`, no `pyproject` pytest config — run from repo root):
```bash
pytest tests/                                       # all
pytest tests/test_quotation_phase2.py               # one file
pytest tests/test_quotation_phase2.py::test_xxx     # one test
```
`tests/main.py` is **not** a test — it's a standalone FastAPI dev server (port 8765) for replaying the OCR + SpecificationMapping pipeline used by Phase1.

End-to-end smoke (needs a running backend + fixtures): `bash tests/scripts/full_acceptance_regression.sh` (configurable via `BASE_URL`, `SUPER_USER`, `SUPER_PASS`, `TEST_PDF`, `RUN_QUOTATION_APPROVE`, etc.).

### Architecture guard (run before pushing layering changes)
```bash
bash scripts/check_layered_architecture.sh
```
This is the only CI check (`.github/workflows/layered-architecture-guard.yml`). It enforces three rules:
1. `app/usecases/**` must **not** `from app.integrations` import.
2. A fixed list of "remediated" routes must **not** import `app.integrations` directly (see script for the list — includes `quotation_generation`, `document_processing`, `chat_summary`, `closing_form`, `sqlserver_queries`, `file_manager`, `pdf2image`, `image2url`, `context_compression`).
3. `app/ports` must contain `Protocol` definitions.

### Frontend
```bash
cd frontend
pnpm install
cp apps/chat/env.example apps/chat/.env
pnpm dev           # turbo run dev → Vite
pnpm build         # vue-tsc + vite build
pnpm lint          # eslint --fix
pnpm type-check    # vue-tsc --noEmit
```
Use **pnpm 8.x** (matches `packageManager`). The only app is `frontend/apps/chat` (`@yamato/chat`); shared code lives in `frontend/packages/`. All scripts run through Turbo.

## Architecture: Route → UseCase → Port → Adapter

This is the dominant pattern and is enforced by the guard script. Understanding it is essential before editing `app/`.

**Dependency direction** (inner = stable, outer = volatile):
```
Route (app/api/v1) ──creates──▶ Adapter ──implements──▶ Port (Protocol)
   │                                                    ▲
   └────── invokes ──────▶ UseCase (app/usecases) ──────┘
                            UseCase only knows Port types, never the Adapter class
```

| Layer | Location | Responsibility | Must NOT |
|-------|----------|----------------|----------|
| **Route** | `app/api/v1/` | Parse/validate input, `Depends`, **construct Adapters + UseCase**, map result to HTTP response | import `app.integrations` (for remediated routes); hold long business flows |
| **UseCase** | `app/usecases/` | Orchestrate business steps; receives Ports via constructor; returns stable command/query results | import `app.integrations`, ORM, or HTTP clients |
| **Port** | `app/ports/` (`dto`, `contracts`, `outbound`) | `Protocol` contracts + pure DTOs | do IO; reference ORM entities in signatures |
| **Adapter** | `app/adapters/` | Implement Port by translating to `app/integrations/*`, ORM, config | contain whole business flows (those stay in UseCase) |
| **Domain** | `app/domain/` | IO-free pure functions + shared exceptions | — |
| **Integration** | `app/integrations/` | Third-party/HTTP/SQL implementation details | be imported by UseCase or (remediated) Route |

`app/ports` split: `dto/` = pure data classes; `contracts/` = cross-business Protocols (`TaskStatePort`, `CurrentUserPort`, `RequestMetricsPort`); `outbound/` = per-business-line driven (outbound) Protocols (`QuotationTaskRepoPort`, `FileManagerPort`, …). **Import Ports from their subpackage explicitly** — `app/ports/__init__.py` does not re-export symbols.

`app/schemas/` vs `app/ports/dto/`: `app/schemas/` holds **HTTP wire models** (pydantic request/response, under `endpoints/` and `platform/`); `app/ports/dto/` holds **Port-boundary pure data classes** (read models, command/result DTOs). The two do not import each other — wire models stop at the Route layer, Port DTOs cross the UseCase↔Adapter boundary.

`__init__.py` convention: package `__init__.py` files hold **only a docstring** — no convenience re-exports; import symbols from their explicit submodule path (e.g. `from app.ports.outbound.quotation import QuotationTaskRepoPort`). Two deliberate exceptions: lazy `__getattr__` loaders that defer heavy optional deps (`app/adapters/quotation/__init__.py`, `app/adapters/conversation/__init__.py`) and ORM model aggregation for SQLAlchemy metadata registration (`app/models/orm/platform/__init__.py`).

When adding a feature: add a Port method/new Port → add Adapter → adjust UseCase → wire in Route. The Router is the **composition root** (it `new`s Adapters and injects them into the UseCase). For multi-Adapter flows, prefer a factory like `app/adapters/quotation/deps.py:build_execute_quotation_phase1_use_case()` rather than scattering `new` across workers.

Canonical worked examples live in `docs/architecture-route-usecase-port-adapter.md` (chat_summary create/query, quotation task create) and `docs/di-and-layered-architecture.md` (how Container/Provider/Inject/Wire map onto these layers).

## Quotation generation pipeline

The most complex subsystem. Read `docs/quotation-task-and-data-flow.md` and `docs/task-state-truth.md` together.

**Two-phase state machine:** `queued` → `running` (Phase1) → `awaiting_approval` → `running` (Phase2) → `completed`; plus `failed` / `cancelled`. Status vocab is defined by `QuotationTaskStatus` in `app/models/orm/quotation_task.py`.

- **Phase1** (`ExecuteQuotationPhase1UseCase`): PDF page1 rasterize → MinIO temp upload → OCR → keyword mapping → PDM BOM query. Ends in `awaiting_approval`; `result_payload` holds `keywords_payload`, `pdm_result`, `pdm_partids`, `u8_parent_inv_codes`, etc.
- **Phase2** (`ExecuteQuotationPhase2UseCase`): triggered by `/approve` with `approved_partids`; runs U8 BOM+inventory query; final `result_payload` includes `u8_result`, `u8_result_by_type`, and a multi-sheet xlsx uploaded to MinIO at `quotation-results/{task_id}/u8_by_type.xlsx`.

**Task state sources of truth** (differs per task type — see `docs/task-state-truth.md`):
- `quotation_generation_*`: Postgres `quotation_tasks` is authoritative for status/progress/result; Redis `task:{id}` (24h TTL) is a WS cache mirror; `owner_id` from PG, WS auth via `TaskOwnerRegistry`.
- `doc_process_*`: Redis-only (expired key = task gone).
- `pdf_convert_*` / `image_upload_*`: in-memory `ExecutorManager` Futures — lost on restart.

**Cooperative cancellation:** Pass `cancel_checker` through Port calls in the pipeline; cancellation raises `QuotationPipelineCancelledError` (`app/domain/quotation/exceptions.py`); unrecoverable business errors raise `QuotationPipelineError`. REST `sqlserver_queries` routes call the **same** PDM/U8 Adapters but pass `cancel_checker=None`.

**Workers** (`app/integrations/Quotation_Generation/quotation_task_workers.py`) own the thread-local event loop, TaskManager progress sync, MinIO download, UseCase invocation, and ORM cleanup — **no** OCR/PDM/U8 business logic there. On startup, `main.py` lifespan resets stale `running` tasks → `queued` and re-dispatches per owner; this is required for restart-safe behavior.

## Conversation workflow (langchain, replaces Dify)

The chat feature runs in-process on a langchain backbone (no Dify). Frontend hits Dify-compatible SSE endpoints on the backend; the backend owns conversations + messages + memory.

- **Route** `app/api/v1/conversation.py` — mounted with empty prefix so paths are `/api/v1/chat-messages` (streaming SSE: `message`/`message_end`/`error`), `/api/v1/conversations`, `/api/v1/messages`, `/api/v1/conversations/{id}/name`. Auth via JWT `get_current_user` (no more gateway API key). In-memory `_CANCEL_FLAGS` powers `/chat-messages/{task_id}/stop` (cooperative cancel; per-process, lost on restart).
- **UseCase** `app/usecases/conversation/run.py` (`RunConversationUseCase`) — owns the turn sequence: resolve/create conversation → apply `background` memory override → load user profile → assemble dual memory → stream answer → persist user message (before stream) + assistant message + `recent_dialogs` line (after). Yields `ConversationStreamEvent` (token/done/error); the route translates to SSE bytes.
- **Workflow** `app/integrations/conversation/pipeline.py` (`ConversationPipeline`) — the three-branch answering engine (联网搜索 / 本地检索 / 本地&网络): keyword extraction (Qwen3-8B) → local retrieval (`RetrieverPort`, instance_id 1=表单数据 / 2=离散知识, collection `doc_collection_{n}`) and/or web search (`WebSearchPort`, Tavily) → intent enhancement → streaming answer (Qwen3.6-35B-A3B) with `<think>` stripped via `ThinkStreamFilter`. Prompts ported verbatim under `app/domain/conversation/prompts.py`.
- **Storage** `app/models/orm/conversation.py` — `conversations` (owns `long_memory` + `recent_dialogs` JSONB) and `messages` rows. `ConversationRepoPort` is the single source of truth, also used by `chat-summary` and `context-compression` (both refactored off Dify: they read local messages / memory instead of Dify HTTP).
- `chat-summary` (`MessageExtractorChatArchiveAdapter`) reads user queries from the local `messages` table; `context-compression` (`IntegrationContextCompressorAdapter`) reads `long_memory`/`recent_dialogs` via `ConversationRepoPort`.

## Task infrastructure (shared by all async work)

- `app/core/task_manager.py` (`task_manager`) — Redis-backed task state, observer pattern. Observers: `LoggingObserver`, `MetricsCollector`, `WebSocketTaskObserver` (registered in lifespan).
- `app/core/executor.py` (`executor_manager`) — thread pool for async tasks; `set_task_manager` bridges Future → task state (auto-sync off by default).
- `app/core/websocket_task_manager.py` (`ws_manager`) — WS progress push (polling fallback exists).
- `app/core/quotation_dispatcher.py` / `retention_scheduler.py` — per-owner queue dispatch and retention (global count > 100 trims terminal rows to ≤ 50; `awaiting_approval` > 24h hard-purge).
- Bounded model pools (PaddleOCR, TagGenerator) in `app/integrations/doc_processing/` are closed on shutdown.

## API routing

Routes are flat modules in `app/api/v1/` assembled by `registry.py:build_api_router()`. Each module exposes a `router`; `registry` mounts it under a prefix from `prefixes.py` with a tag from `tags.py`. Legacy path aliases are duplicate mounts of the **same** router objects (kept for old clients) — changing a handler changes both paths. Add a new endpoint module by importing it in `registry.py` and mounting with `_mount(r, module.router, p.X, [t.Y])`.

## Middleware stack (order matters, added in `create_app`)

CORS → TrustedHost → Monitoring (Prometheus) → SecurityHeaders → RequestSize → RateLimit → Cache. In production `ENVIRONMENT=production`: docs/OpenAPI disabled, `ALLOWED_HOSTS=["*"]` rejected by config validator, `/metrics` gated by `X-API-KEY` (when `METRICS_REQUIRE_API_KEY`).

## Config & external dependencies

`app/core/config.py` (`settings`, pydantic) reads from `.env` (see `.env.example` for the full key list). External services: **PostgreSQL+pgvector** (RAG + task + conversation store), **Redis** (cache + task state), **MinIO** (object storage), **SQL Server** (U8 + PDM databases), and AI inference endpoints (`BGE_M3_API_URL`, `RERANKER_API_URL`, `QWEN3_8B_API_URL`/`QWEN3_6_35B_API_URL` for the langchain conversation workflow, `TAVILY_API_KEY` for web search). The backend **degrades gracefully** — `lifespan` wraps each init in try/except and logs `[warning]` rather than crashing if a dependency is unreachable. RAG system is built in `lifespan` and stored on `app.state.rag`.

## Key docs (read these before non-trivial work in an area)

- `docs/architecture-route-usecase-port-adapter.md` — the layering pattern with real code excerpts
- `docs/di-and-layered-architecture.md` — DI terminology mapped to Port/UseCase/Adapter/Router
- `docs/quotation-task-and-data-flow.md` — quotation pipeline control + data flow
- `docs/conversation-workflow.md` — langchain conversation workflow (replaces Dify)
- `docs/task-state-truth.md` — where each task type's state actually lives
- `docs/SECURITY_PUBLIC_ENDPOINTS.md` — which endpoints are public vs auth-gated
- `docs/u8_bom_tables.md`, `docs/review-pdm-bom-model-query.md`, `docs/sqlserver-query-parameter-passing.md` — U8/PDM SQL details
- `docs/layered-architecture-remediation-plan.md` + `*-phase2-*.md` — history of the layering refactor
