<div align="center">

# Yamato AI 助手平台

### 大和计量设备（上海）智能工作台

<p align="center">
  <a href="#核心能力">核心能力</a> •
  <a href="#前端页面与权限">前端页面与权限</a> •
  <a href="#仓库结构速览">仓库结构</a> •
  <a href="#快速开始">快速开始</a> •
  <a href="#api-访问">API 访问</a>
</p>

![Python Version](https://img.shields.io/badge/python-3.12+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.116.1-009688.svg)
![Vue](https://img.shields.io/badge/Vue-3-4FC08D.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)

</div>

---

## 简介

Yamato AI 助手平台是专为**大和衡器（上海）**打造的企业内部 AI 工作平台。员工可以通过自然语言对话访问企业知识库、智能处理各类文档、填写设备订单报单，大幅减少重复性信息查找与手动录入工作。

---

## 核心能力

### AI 知识库对话

员工可以直接用自然语言向 AI 提问，AI 将基于企业内部文档给出有依据的精准回答。无需翻阅手册，直接问答即可。

- 支持**本地知识库**检索（基于已上传的企业文档）
- 支持**联网搜索**补充外部信息
- 支持**本地 + 联网混合**检索模式，按需切换
- 对话历史自动保存，随时回顾

---

### 智能文档处理

上传企业文档后，系统自动解析内容并建立可检索的知识库，无需人工标注或整理。

- 支持格式：PDF、Word（.docx）、Excel（.xlsx）、PowerPoint（.pptx）、HTML、纯文本
- 上传即处理，实时显示处理进度
- 处理完成后文档内容立即可被 AI 对话检索引用

---

### 营业订单信息填报

专为**大和智能组合秤产品**设计的营业订单信息页面，结构化收集订单参数、规格书与原价书图片，替代手工录表。

支持填报的字段包括：成交时间、客户名称、产品类型与型号、数量、不含税单价、制造编号、合同编号、物料与称重规格（物料名称、称重规格、速度、精度、包装机类型）、机械结构参数（顶锥形式、线振形式、料层圈、供料斗、计量斗、存储斗、溜槽角度、集合斗形式等）与秤体类型。

- 普通用户提交后可查看自己的表单；管理员与超级用户可查看全部表单
- 管理员与超级用户可审批通过、退回待修改，并删除已通过或不通过记录
- 被退回的表单会进入 **待修改** 标签，原提交者修改后可重新提交
- 审批通过后自动写入知识库集合，支持后续语义检索
- 报单图片存储于 MinIO，并支持预览与归属校验删除

---

### OCR 图像识别

将扫描件、拍照图片或 PDF 中的文字内容自动识别并提取为结构化文本。

- 支持中英文混合识别
- 支持表格结构识别
- 支持将 PDF 文档逐页转换为图片，便于预览和 OCR 处理

---

### 报价生成（PDF 图纸流水线）

侧边栏 **「报价生成」**（路由 `/files`）用于上传 **PDF 图纸**，自动走 PDM 解析、人工审核、U8 查询等异步任务；任务进度支持 **WebSocket** 推送（并保留轮询兜底）。

- 上传 PDF 创建任务，看板展示排队 / 处理中 / 已完成等状态
- **PDM**：解析结果可按类型分组，用户在 **等待审核** 状态下勾选保留的 PARTID 后继续
- **U8**：第二阶段按类型汇总 BOM；结果写入任务 `result_payload`
- **MinIO**：阶段产物与报价相关文件使用对象存储；Phase2 结束后生成 **按类型多 Sheet 的 xlsx**，写入 `quotation-results/{task_id}/u8_by_type.xlsx`（上传失败不阻塞任务成功，此时无 Excel 下载）
- 完成后可 **鉴权下载** 原始处理 PDF 与 **U8 分组 Excel**（`GET /api/v1/quotation/tasks/{task_id}/u8-by-type-workbook`）

> **说明**：知识库文档的上传与处理进度在 **AI 对话页** 内完成；集合与文档管理在 **知识库管理页**（`/collection2`）。请勿与报价任务的 PDF 混淆。

---

### 对话记录归档

平台自动分析用户的历史对话，提炼出用户的问询习惯与偏好，生成**用户画像摘要**，帮助团队了解员工实际需求。

---

## 前端页面与权限

前端 Monorepo 主应用为 [`frontend/apps/chat`](frontend/apps/chat)。登录后可使用核心业务页面；侧边栏会按角色与页面权限自动隐藏无权限入口：

| 页面 | 路径 | 访问说明 |
|------|------|----------|
| 登录页 | `/login` | 公开；已登录访问将跳转对话页 |
| 注册页 | `/register` | 公开；新用户默认注册为普通用户 |
| AI 对话页 | `/chat` | 需登录；侧边栏含知识库文档上传入口 |
| 报价生成页 | `/files` | 需登录且拥有 `view_quotation` 页面权限；admin / superuser 默认可访问 |
| 营业订单信息页 | `/closing-form` | 需登录且拥有 `view_closing_form` 页面权限；admin / superuser 默认可访问 |
| 知识库管理页 | `/collection2` | 需登录且角色为 **admin** 或 **superuser** |
| 用户管理页 | `/users` | 需登录且角色为 **superuser**；可管理用户角色与页面权限 |

> 未登录用户访问除 `/login`、`/register` 外的路由将跳转至登录页。无相应角色或页面权限时将被重定向至对话页。系统包含 10 分钟无操作自动退出登录机制。

---

## 快速开始

### 前置要求

- Python 3.12+
- PostgreSQL 14+（需安装 pgvector 扩展）
- Redis 6.0+
- **MinIO**（对象存储；报价任务 PDF/临时图/结果 xlsx 等依赖桶配置，见 `.env.example`）
- **SQL Server**（**U8** 与 **PDM** 库；报价流水线与启动时的连通性检查，见 `.env.example`）
- Node.js 18+；**pnpm 8.x**（与 [`frontend/package.json`](frontend/package.json) 中 `packageManager` 一致）

### 后端启动

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd project-yamato-shanghai

# 2. 创建 Python 环境
conda create -n yamato python=3.12
conda activate yamato

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 PostgreSQL、Redis、MinIO、SQL Server（U8/PDM）及 AI 推理服务地址

# 5. 初始化数据库（首次运行）
# 在 PostgreSQL 中执行：CREATE EXTENSION IF NOT EXISTS vector;

# 6. 启动服务
python main.py
```

### 前端启动

前端使用 **pnpm workspace** 与 **Turbo**（`pnpm dev` 等价于 `turbo run dev`）。建议使用与仓库一致的 **pnpm 8.x**（见 [`frontend/package.json`](frontend/package.json) 中 `packageManager`），以减少安装与脚本行为差异。

```bash
cd frontend

# 安装依赖
pnpm install

# 配置前端环境变量
cp apps/chat/env.example apps/chat/.env

# 启动开发服务器
pnpm dev
```

---

## API 访问

后端服务启动后，可通过以下地址访问：

| 服务 | 地址 |
|------|------|
| 主服务 | http://localhost:8000 |
| Swagger UI（交互式 API 文档） | http://localhost:8000/api/v1/docs |
| ReDoc（API 参考文档） | http://localhost:8000/api/v1/redoc |
| 健康检查 | http://localhost:8000/api/v1/health |

常用业务接口前缀：

| 功能 | 前缀 | 说明 |
|------|------|------|
| 认证与用户 | `/api/v1/auth` | 登录、注册、当前用户、superuser 用户/权限管理 |
| 报价生成 | `/api/v1/quotation` | PDF 报价任务、PDM 审核、U8 查询、结果下载 |
| 营业订单信息 | `/api/v1/closing-form` | 表单提交、列表、审批、退回修改、图片上传与知识库记录管理 |
| 文档任务 | `/api/v1/document-tasks` | 知识库文档处理任务与 WebSocket 进度推送 |
| OCR | `/api/v1/ocr` | 图片文字识别与 PDF 转图片 |

报价相关 OpenAPI 标签为 **Quotation Generation**（实现见 `app/api/v1/quotation_generation.py`）。营业订单信息接口实现见 `app/api/v1/closing_form.py`。

---

## 仓库结构速览

| 路径 | 说明 |
|------|------|
| [`main.py`](main.py) | FastAPI 入口、生命周期（含报价队列恢复、SQL Server 连通性检查） |
| [`app/`](app/) | 领域用例、API、RAG、报价集成（`integrations/Quotation_Generation`）等 |
| [`frontend/`](frontend/) | pnpm + Turbo Monorepo；业务应用在 [`frontend/apps/chat`](frontend/apps/chat) |
| [`tests/`](tests/) | 单元/回归测试（含 `U8ResultByTypeCsvAdapter` 等） |

### 架构分层（Clean Architecture）

依赖方向（向内为尊）：`domain ◄ ports ◄ usecases ◄ adapters ◄ api(组合根) + main.py`。内层（domain / ports / usecases）只依赖端口与 DTO，不直接 import 适配器实现；`api` 与 `main.py` 作为组合根负责装配。分层由 [`scripts/check_layered_architecture.sh`](scripts/check_layered_architecture.sh)（CI 门禁，见 `.github/workflows/layered-architecture-guard.yml`）强制。

`app/adapters/` 内部按 driving / driven 两类组织（同层）：

| 子目录 | 语义 | 实现的端口 |
|--------|------|-----------|
| `adapters/ragsystem/` | driven | `RetrieverPort`、`ChartAnalysisPort` |
| `adapters/monitoring/` | driven | `RequestMetricsPort`（health_check 无 port，组合根直调） |
| `adapters/workers/` | driving（异步入口） | `TaskDispatchPort`（`dispatch.py`） |
| `adapters/web/` | driving（FastAPI 请求/响应 DTO） | - |
| `adapters/{quotation,ocr,sqlserver,doc_processing,closing_form,auth,pdm_matcher,chat_archive}/` | driven | 各自端口 |
| `adapters/retriever.py` | facade | `RAGRetrieverAdapter`、`ChartAnalysisAdapter` |

> `app/core/`、`app/models/` 暂作外层工具岛原位保留，`core` 未纳入内层禁列；其与 `domain/usecases` 的纠缠留作后续整合。

---

## 更新日志

### v0.2.3（2026-06）

- **营业订单信息**：同步当前页面名称、普通用户页面权限、admin / superuser 审批流程与待修改重新提交机制
- **权限控制**：补充 `view_quotation`、`view_closing_form` 页面权限与侧边栏可见性说明
- **API 文档**：补充认证、报价、营业订单、文档任务与 OCR 的常用接口前缀

### v0.2.2（2026-04）

- **报价生成**：Phase2 完成后生成 U8 按类型多 Sheet **Excel**，上传 MinIO，并在 `result_payload` 中记录路径与建议文件名
- **API**：`GET /api/v1/quotation/tasks/{task_id}/u8-by-type-workbook` 鉴权流式下载 xlsx
- **前端**：报价页任务完成后提供 **下载 Excel**（与下载 PDF 相同鉴权与 blob 行为）

### v0.2.1（2026-04）

- 文档：同步前端路由（含 `/closing-form`、注册与用户/知识库管理页及权限说明）、Monorepo（pnpm + Turbo）与 `env.example` 说明

### v0.2.0（2026-03）

- 新增智能组合秤报单填写功能
- 新增聊天记录归档与用户画像摘要
- 新增 PDF 转图片与 OCR 信息提取
- 新增 WebSocket 实时任务进度推送
- 新增用户认证系统（JWT）
- 完成 Vue 3 前端应用（对话、报价/文件相关页、报单等）

### v0.1.0（2025-12）

- RAG 知识库检索系统上线
- 多格式文档处理管线
- 文件管理与对象存储集成

---

<div align="center">

Made with ❤️ by Shanghai Marinetime 331 Team

</div>
