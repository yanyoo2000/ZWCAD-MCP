# Cursor 接入

## 方式一：项目级（推荐）

在项目根目录创建 `.cursor/mcp.json`：

```json
{
  "mcpServers": {
    "zwcad": {
      "command": "npx",
      "args": ["--registry=https://registry.npmmirror.com", "-y", "zwcad-mcp"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

直接复制本目录下的 `mcp.json` 即可。

## 方式二：全局

写入 `~/.cursor/mcp.json`，内容同上，对所有项目生效。

## 生效与验证

1. 重启 Cursor
2. 在对话中让 AI 调用 `zwcad_get_capabilities`
3. 能返回 ZWCAD 版本与工具组清单即接入成功

## 首次启动说明

首次调用会等待约 30~70 秒（自动创建 Python 环境并安装依赖），之后约 2 秒。

## 前置条件

- Windows 10/11 x64
- 中望CAD 或 中望机械CAD 已启动，并打开了至少一张 DWG
- Python 3.10+（缺失时也会尝试 WorkBuddy 自带的 Python）
- Node.js 18+

## 可选：让 AI 了解使用规范

把 `../workbuddy/skills/SKILL.md` 的内容放到 `.cursor/rules/zwcad-mcp.mdc`，
可以让 AI 在调用前先做环境检查和正确的操作顺序。
