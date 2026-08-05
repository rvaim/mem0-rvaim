# 从官方 Mem0 Cloud 插件迁移到 mem0-rvaim

> 本文档用于迁移说明,因此会提到 `MEM0_API_KEY` / `api.mem0.ai` 等
> 云端术语。**运行代码中已全部移除**,此文件仅作迁移参考。

## 区别概览

| 维度 | 官方插件 (Cloud) | mem0-rvaim (本地) |
|---|---|---|
| 账户 | 需要 Mem0 Platform API Key | 不需要,无云端 |
| 服务 | 远程 MCP (`mcp.mem0.ai`) | 本地 stdio MCP Proxy |
| 存储 | Mem0 Platform | Qdrant Local + SQLite |
| 记忆写入 | 云端 API | 本地 daemon `/v1/capture` |
| 隔离 | `user_id` + `app_id` 过滤(agent 可提交) | 服务端 namespace(不可伪造) |
| 全局搜索 | 可跨所有用户/项目 | 仅 global 作用域(本用户) |
| 记忆处理 | 云端 AI | 独立配置的 Memory/Summary/Recall LLM |
| 安装 | 填 API Key | 自动建 venv + daemon |

## 迁移步骤

### 1. 导出云端记忆(可选)

在旧环境(官方插件)中,导出所有记忆:

```bash
python3 scripts/export.py --user <user_id> --app <project_id> > mem0-cloud-export.md
```

### 2. 卸载官方插件,安装 mem0-rvaim

按 README 的安装方式安装本插件。首次会话自动完成 venv 安装与 daemon 启动。

### 3. 配置模型 Provider

编辑 `~/.mem0/local/config/config.json`,按 README 填写 `llm` 与 `embedder`
(使用你自己的 LLM/Embedding API Key,或本地模型)。

### 4. 导入旧记忆(可选)

```bash
python3 scripts/admin.py import mem0-cloud-export.md workspace
```

导入后可用 `mem0:tour` 浏览确认。

### 5. 校验

- `python3 scripts/admin.py doctor` — 全部 ✓
- `python3 scripts/admin.py status` — daemon running,记忆计数符合预期
- 新开一个会话,确认首条状态行显示 `Mem0 Active (local)`

## 行为变化提醒

- **不再有"跨用户全项目搜索"**。旧全局搜索模式被移除;需要跨项目共享的
  记忆请显式存入 `global` 作用域(`/mem0:remember` 的 `scope=auto` 由
  Memory LLM 自动分类)。
- **project 更名为 workspace**。官方插件的 `app_id`/`project_id` 语义
  在本插件中为 `workspace_id`,解析逻辑(显式映射 → git remote → 目录名)
  保持一致。
- **`pin` 不再用正文前缀**。官方插件用 `[PINNED]` 前缀模拟固定;本插件通过
  `update_memory` 的 `metadata_patch={"pinned": true}` 真正写 metadata。
- **写入为同步**。`get_event_status` 恒返回 `SUCCEEDED`(兼容适配层)。

## 云端依赖移除清单

以下内容在本插件中已删除/本地化:

- `api.mem0.ai` 搜索与写入接口 → 本地 daemon HTTP API
- `mcp.mem0.ai` 远程 MCP → 本地 stdio MCP Proxy
- `MEM0_API_KEY` 解析(含 shell profile 扫描)→ 无
- 云端 categories / onboarding / stats / telemetry → 本地实现或移除
- 宿主 Agent 生成多路搜索查询的 rubric → 单一 `recall_context` 工具
- 每 3 条消息自动捕获逻辑 → Stop/PreCompact 增量捕获(去重)
