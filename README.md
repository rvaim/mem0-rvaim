# mem0-rvaim — Local-first Memory Plugin

基于 Mem0 官方 `integrations/mem0-plugin` 二次开发的**本地自托管**记忆插件。
插件自动安装并管理一个本地 Python Memory Daemon:内嵌 Mem0 Python 库、
使用 Qdrant Local 保存向量、SQLite 保存状态。**无云账户、无 `MEM0_API_KEY`、
无 Docker、无 PostgreSQL**。

```
Claude Code / Codex
     │
     ├── Lifecycle Hooks (SessionStart / UserPromptSubmit / PreCompact / Stop)
     └── Local MCP Proxy (stdio)
              │
              ▼
     Plugin-managed Memory Daemon  (仅监听 127.0.0.1,随机 Bearer Token)
     ├── Mem0 Python Library
     ├── Qdrant Local + SQLite
     ├── Recall / Capture / Summary pipelines
     └── Workspace isolation (server-side namespace)
              │
      ┌───────┴────────┐
      ▼                ▼
  Memory LLM      Embedding Provider
 (remote/local)   (remote/local)
```

## 功能

- **全自动**:SessionStart 启动 daemon 并注册 session/workspace;UserPromptSubmit
  本地召回;Stop 捕获完整回合;PreCompact 强制保存。
- **模型独立**:`llm`、`summary_llm`、`recall_llm`、`embedder` 各自独立配置,
  支持任何 OpenAI 兼容端点(Ollama / LM Studio / vLLM / 代理网关)。
- **两级作用域**:`global`(跨工作区)与 `workspace`(仅当前工作区),
  由服务端不可伪造的 namespace 强制隔离;`auto` 由独立 Memory LLM 分类。
- **宿主 Agent 不参与记忆处理**:提取、分类、总结、查询改写、冲突合并全部
  由独立配置的模型完成。
- **保留官方工具名与 Skills**:`add_memory`、`search_memories`、
  `recall_context`、`memory_health` 等 MCP 工具;`remember`、`peek`、
  `context-loader`、`dream` 等 Skills。
- **数据安全**:数据在 `~/.mem0/local/data`,卸载/升级不删除;备份恢复见下文。

## 安装

> 以 Claude Code 插件为例。Codex 插件同样适用(配置文件名不同)。

1. 将本目录作为插件安装到 Claude Code 的插件目录。
2. 首次启动会话时,`SessionStart` hook 会自动:
   - 在 `~/.mem0/local/venv` 创建 Python 虚拟环境;
   - 按 `requirements.txt` 安装 mem0ai + qdrant-client;
   - 启动 Memory Daemon 并注册当前 session。

### 配置模型 Provider

编辑 `~/.mem0/local/config/config.json`(首次自动生成默认值):

```json
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1"
  },
  "summary_llm": null,
  "recall_llm": null,
  "embedder": {
    "provider": "openai",
    "model": "text-embedding-3-small",
    "api_key": "sk-...",
    "base_url": "https://api.openai.com/v1",
    "dimensions": 1536
  },
  "recall": {
    "mode": "direct",
    "workspace_top_k": 8,
    "global_top_k": 4,
    "session_top_k": 3,
    "max_tokens": 1600,
    "timeout_ms": 5000,
    "threshold": 0.3
  }
}
```

- `summary_llm` / `recall_llm` 留空则继承 `llm`。
- `embedder.dimensions` 必须与模型输出维度一致(openai text-embedding-3-small
  为 1536;本地模型按实际填写)。
- 也可以用环境变量覆盖:`MEM0_LLM_MODEL`、`MEM0_LLM_BASE_URL`、
  `MEM0_LLM_API_KEY`、`MEM0_EMBEDDER_MODEL`、`MEM0_EMBEDDER_DIMENSIONS` 等。

修改后执行 `python3 scripts/admin.py restart` 生效。

### 本地模型示例(Ollama)

```json
{
  "llm": {"provider": "openai", "model": "qwen2.5:7b",
          "api_key": "ollama", "base_url": "http://127.0.0.1:11434/v1"},
  "embedder": {"provider": "openai", "model": "nomic-embed-text",
               "api_key": "ollama", "base_url": "http://127.0.0.1:11434/v1",
               "dimensions": 768}
}
```

## 管理命令

```bash
python3 scripts/admin.py doctor      # 环境诊断
python3 scripts/admin.py status      # daemon 状态 + 统计
python3 scripts/admin.py start       # 确保 daemon 运行
python3 scripts/admin.py stop        # 停止 daemon
python3 scripts/admin.py restart     # 重启(配置修改后)
python3 scripts/admin.py config      # 查看生效配置(密钥打码)
python3 scripts/admin.py export      # 导出记忆(markdown)
python3 scripts/admin.py import <f>  # 导入记忆文件
python3 scripts/admin.py wipe        # 清空当前工作区记忆
```

## 目录结构

```
~/.mem0/local/
├── venv/                  Python 虚拟环境(ensure_deps.sh 创建)
├── config/
│   ├── config.json        模型/行为配置
│   └── identity.json      本地稳定用户标识(随机 hash)
├── data/
│   ├── qdrant/            Qdrant Local 向量库
│   ├── mem0-history.db    mem0 历史库
│   └── state.db           SQLite 状态(游标/任务/session/workspace)
├── runtime/               daemon.pid / daemon.port / daemon.token
├── logs/
└── backups/               导出备份
```

## 升级与数据保留

- 插件升级只替换**程序目录**;`~/.mem0/local` 数据目录不受影响。
- 升级后首次会话自动重启 daemon,复用旧数据(状态库带版本迁移)。
- 卸载插件默认不删除 `~/.mem0/local`;清除数据请显式
  `python3 scripts/admin.py wipe` 或删除整个目录。

## 备份与恢复

```bash
# 备份(导出为可移植 markdown)
python3 scripts/admin.py export > ~/.mem0/local/backups/mem0-backup-$(date +%Y%m%d).md

# 恢复(导入回当前工作区)
python3 scripts/admin.py import ~/.mem0/local/backups/mem0-backup-YYYYMMDD.md
```

## 测试

```bash
python -m venv tmp/venv-test
tmp/venv-test/Scripts/pip install pytest mem0ai
tmp/venv-test/Scripts/python -m pytest tests/ -q
```

测试使用 Fake LLM/Embedding 端点,不触网、不依赖真实 API。

## 从官方 Mem0 Cloud 插件迁移

见 [docs/migration-from-mem0-cloud.md](docs/migration-from-mem0-cloud.md)。

## 已知限制

- mem0 OSS 的实体提取依赖 spaCy,未安装时实体功能降级(`list_entities`
  可能返回空)——`pip install "mem0ai[nlp]"` 可启用。
- Qdrant Local 单进程持有,多个 Claude/Codex 会话共享同一 daemon(由文件锁
  保证单实例),并发写已串行化。
- `rewrite` 召回模式依赖 `recall_llm` 可用;不可用时自动回退 `direct`。
- Windows 上 token 文件权限依赖 NTFS ACL(尽力而为)。
- mem0 的 BM25 混合检索需要 `mem0ai[extras]`,默认仅向量检索。

## 安全说明

- Daemon 只监听 `127.0.0.1`,请求需携带随机 Bearer Token(token 每次启动
  重新生成)。
- API Key 仅存在于 `config.json` 与进程环境,从不写入日志。
- 客户端无法伪造 `user_id`/`workspace_id`——作用域由注册的 session 决定。
- 默认关闭 mem0 telemetry(`MEM0_TELEMETRY=false`),无任何云端调用。

## License

Apache-2.0(与上游 mem0 一致)。
