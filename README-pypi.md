# ZWCAD MCP

中望CAD（ZWCAD）MCP 服务。让大模型通过 [MCP 协议](https://modelcontextprotocol.io)
直接操控中望CAD 平台与中望机械CAD，完成绘图、标注、图层/块/样式管理，
以及图框、标题栏、明细表（BOM）等机械操作。

**共计 40 个工具**：平台 27 个（不依赖机械环境）+ 机械 11 个（需中望机械CAD）+ 诊断 2 个。

> 走 npm 渠道或离线包时是 41 个——启动器会额外注入 `zwcad_wait_ready`，
> 用于等待 Python 运行时就绪。PyPI 版直接跑 Python，没有这一层。

## 安装

```bash
pip install zwcad-mcp
```

依赖 `fastmcp`、`pyzwcad`、`pyzwcadmech`、`comtypes`、`pywin32`，pip 会自动安装。

## 运行

三种方式等价，任选一种：

```bash
uvx zwcad-mcp              # 有 uv 时最省事，无需预装
zwcad-mcp                  # pip 安装后的 console script
python -m zwcad2d          # 模块方式，一定可用
```

这是一个 stdio 方式的 MCP 服务，通常由客户端拉起，不需要你手动执行。

> **给 Agent 平台接入**：如果你的平台托管 Python 环境，用 `uvx zwcad-mcp` 最直接——
> 它会自动创建隔离环境、安装依赖并启动，平台不需要预装本包。
> 若平台只能提供 Python 而不能保证有 uv，改用 `python -m zwcad2d`（需先 `pip install zwcad-mcp`）。

## 环境要求

- **Windows 10/11 x64**
- **Python 3.10+**
- **中望CAD 或 中望机械CAD** 已启动，且至少打开一张 DWG

## 接入客户端

把下面整段放进客户端的 MCP 配置文件（Claude Desktop 的 `claude_desktop_config.json`、
Cursor 的 `.cursor/mcp.json` 等）：

```json
{
  "mcpServers": {
    "zwcad-2d": {
      "command": "python",
      "args": ["-m", "zwcad2d"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

如果用的是虚拟环境，把 `python` 换成该环境的完整路径，例如
`D:\\project\\.venv\\Scripts\\python.exe`。

也可以用 `uvx` 免预装拉起：

```json
{
  "mcpServers": {
    "zwcad-2d": {
      "command": "uvx",
      "args": ["zwcad-mcp"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

## 验证是否可用

服务启动后，让 AI 调用诊断工具：

```
zwcad_diagnose(probe_cad: true)
```

返回里 `platform.available` 为 `true` 表示已连上 ZWCAD。

## 排障

| 现象 | 处理 |
|---|---|
| `ModuleNotFoundError: No module named 'zwcad2d'` | 确认 `pip install zwcad-mcp` 与运行用的是同一个 Python |
| 报「未找到可用的 Python」或导入依赖失败 | 补装依赖：`pip install fastmcp pyzwcad pyzwcadmech comtypes pywin32` |
| `platform.available=false` | ZWCAD 未启动，或未打开 DWG。启动后重连客户端 |
| 机械工具报 `TYPELIB_NOT_LOADED` | 需中望机械CAD 已启动；平台工具不受影响 |

### 关于 `-2147312566`（加载类型库/DLL 时出错）

这是机械类型库的已知情况，**不是文件缺失或权限问题**。根因：`ZwmToolKit.tlb`
内部引用了另一个仅在 ZWCAD 进程运行时才可解析的类型库。

- `ZwmToolKit.tlb` 本身可被正常加载，14 个类型中仅 `ZwmBalloonWrapper` 的第二个基类型解析失败
- 该接口自身 `cFuncs=0`、`cVars=0`，是无方法的空壳，**跳过不损失功能**
- **处置**：确保 ZWCAD 已启动并打开 DWG，然后完全重启 MCP 客户端

## 三条渠道的对比

同一个服务共发布三处，**工具集与版本号完全一致**，区别只在启动方式：

| 渠道 | 启动 | 需要 Python | 需要 Node | 需要联网 |
|---|---|---|---|---|
| **PyPI**（本页） | `python -m zwcad2d` | 3.10+ 自备 | ❌ | 安装时需要 |
| **npm** | `npx -y zwcad-mcp` | 启动器自动准备 | 18+ | 首次需要 |
| **离线包** | `node bridge.js` | ❌ 自带 | 推荐 18+ | ❌ 不需要 |

如果你的机器已经有可用的 Python 环境，**本页这套更轻**：没有中间层，启动即用。

如果你的机器**不能联网、没有 Python、也不允许自行安装**，用离线包：
从 [Releases](https://github.com/yanyoo2000/ZWCAD-MCP/releases) 下载
`zwcad-mcp-<版本>-win-x64-offline.zip`，解压即用。

## 源码与反馈

- 源码仓库：https://github.com/yanyoo2000/ZWCAD-MCP
- 问题反馈：https://github.com/yanyoo2000/ZWCAD-MCP/issues

## License

MIT
