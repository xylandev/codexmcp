# 环境准备

1. codex
2. uv

# 初始化环境

uv sync

# 使用方法
## cursor 等 IDE
{
  "mcpServers": {
    "codex": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "/Users/xuyu/me/mcp/codexmcp",
        "codexmcp"
      ]
    }
  }
}

## claude code
claude mcp add codex -s user --transport stdio -- uv run --directory  yourpath/codexmcp codexmcp


# 配置 prpmpt 开始使用