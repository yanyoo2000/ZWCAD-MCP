# Claude Desktop 接入

## 步骤

1. 打开配置文件：

   ```
   %APPDATA%\Claude\claude_desktop_config.json
   ```

   若不存在，新建该文件。

2. 把本目录 `mcp.json` 的内容合并进去（注意保留已有的其它 server）：

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

3. 完全退出 Claude Desktop（含托盘图标），再重新启动。

4. 在对话中让 AI 调用 `zwcad_get_capabilities`，能返回 ZWCAD 版本即接入成功。

## 首次启动说明

首次调用会等待约 30~70 秒（自动创建 Python 环境并安装依赖），之后约 2 秒。

## 前置条件

- Windows 10/11 x64
- 中望CAD 或 中望机械CAD 已启动，并打开了至少一张 DWG
- Python 3.10+（缺失时也会尝试 WorkBuddy 自带的 Python）
- Node.js 18+

## 常见问题

| 现象 | 处理 |
|---|---|
| 客户端里看不到 zwcad 工具 | 检查 JSON 是否合法（常见是多了或少了逗号），然后完全重启客户端 |
| 首次调用超时 | 首次需装依赖，等 30~70 秒；若客户端自身超时较短，可先手动执行 `npx -y zwcad-mcp --prepare` 预热 |
| 报「未找到可用的 Python」 | 安装 Python 3.10+，或在 `env` 里加 `ZWCAD_MCP_PYTHON` 指向解释器路径 |
