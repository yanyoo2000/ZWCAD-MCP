# WorkBuddy 连接器

## 文件说明

| 文件 | 用途 |
|---|---|
| `connector-meta.json` | 连接器注册信息（名称、描述、示例） |
| `mcp.json` | MCP 服务连接配置 |
| `icon.png` | 市场展示图标（64×64） |
| `skills/SKILL.md` | 教 AI 如何正确使用这些工具 |

## 目录结构

```
connector/workbuddy/
├── README.md              本文件（平台级开发说明，不参与打包）
└── zwcad-mcp/             连接器资源
    ├── connector-meta.json
    ├── mcp.json
    ├── icon.png
    └── skills/SKILL.md
```

一个平台下可以有多个连接器（例如将来新增 `zwcad-mech-mcp`），
构建脚本会自动遍历 `workbuddy/` 下所有含必需文件的子目录。

## 提交到市场

WorkBuddy 要求**文件在包根目录扁平放置**。本仓库为了多平台共存放在子目录里，所以提交前需要构建：

```bash
npm run build:connector
```

产物在 `dist/workbuddy/zwcad-mcp/`，将该目录内容作为连接器包提交：

```
dist/workbuddy/zwcad-mcp/
├── connector-meta.json
├── mcp.json
├── icon.png
└── skills/SKILL.md
```

## 本地调试

参考 `connector-developer-guide.md` 的本地调试章节：

1. 建 `~/.workbuddy/connectors/connector-marketplace.json` 阻断自动更新
2. 在 `~/.workbuddy/connectors-marketplace/connectors/` 下建目录并放入上述文件
3. 重启 WorkBuddy

或者更简单：直接写用户级 `~/.workbuddy/mcp.json`，跳过连接器机制做功能验证：

```json
{
  "mcpServers": {
    "zwcad-mcp": {
      "type": "stdio",
      "command": "npx",
      "args": ["--registry=https://registry.npmmirror.com", "-y", "zwcad-mcp"],
      "env": { "PYTHONUTF8": "1" },
      "timeout": 180000
    }
  }
}
```

## 关于 timeout

设为 `180000`（3 分钟）是为首次自动安装依赖留足余量（实测 40~70 秒）。
安装完成后每次启动约 2 秒。

## 关于 runtime 字段

```json
"runtime": { "type": "node", "version": ">=18" }
```

声明后，用户的机器即使没装 Node，WorkBuddy 也会自动准备。
