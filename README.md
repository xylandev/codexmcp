# codexmcp

将 **Codex CLI** 以 MCP 接入 Cursor、Claude Code等客户端。

## 前置要求

- Python **≥ 3.12**
- Codex
- [uv](https://docs.astral.sh/uv/)


## 安装

在克隆后的仓库根目录执行：

```bash
uv sync
```

## 在 Cursor / windsurf 等 ide 中配置

把 `YOUR_REPO_PATH` 换成克隆后的**绝对路径**：

```json
{
  "mcpServers": {
    "codex": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "YOUR_REPO_PATH",
        "codexmcp"
      ]
    }
  }
}
```

示例：`YOUR_REPO_PATH` → `/Users/worker/mcp/codexmcp`。

## 在 Claude Code 中配置

```bash
claude mcp add codex -s user --transport stdio -- uv run --directory YOUR_REPO_PATH codexmcp
```

同样将 `YOUR_REPO_PATH` 替换为实际路径。

## 配置 ide 提示词 以及 claude code 提示词


```bash
~/.claude/CLAUDE.md 
修改为 本仓库中的 PROMPT.md 文件中的提示词
```

```bash
cursor 等 ide 请自行参考用户规则的设置
```