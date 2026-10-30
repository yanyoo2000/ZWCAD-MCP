# ZWCAD MCP

中望CAD（ZWCAD）MCP 服务，让大模型通过 [MCP 协议](https://modelcontextprotocol.io)直接操控中望CAD 平台与中望机械CAD，完成绘图、标注、图层/块/样式管理，以及图框、标题栏、明细表（BOM）等机械操作。

**共计 40 个工具**：平台 26 个（不依赖机械环境）+ 机械 11 个（需中望机械CAD）+ 诊断 2 个 + 启动器侧 1 个。

> 启动器侧的 `zwcad_wait_ready` 在 Python 运行时尚未就绪时也可用，用于等待初始化完成。
> 诊断统一走 `zwcad_diagnose`：Python 已就绪时返回完整诊断，
> 未就绪时自动降级为启动器视角（含错误日志与环境信息）并标注未检测项。

## 为什么需要 Node 启动器

本服务的业务逻辑是 Python 写的——它通过 Windows COM 驱动 ZWCAD，这部分**无法在 Node 侧等价重写**（COM 类型库解析、SAFEARRAY 编组等能力依赖 `comtypes`）。

而客户端环境通常只保证有 Node，不保证有可用的 Python 依赖。所以本包用一个 Node 启动器抹平差异：

```
MCP 客户端 ──stdio──► bridge.js ──spawn──► python -m zwcad2d
```

启动器自动完成三件事，用户无感：

1. **定位 Python** —— 优先 WorkBuddy 自带，其次系统 Python（需 >= 3.10）
2. **准备隔离环境** —— 首次运行在 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\` 建 venv 装依赖（约 30~60 秒），之后复用
3. **托管服务进程** —— 启动 MCP Server 并原样转发 stdin/stdout

## 环境要求

- **Windows 10/11 x64**
- **中望CAD 或 中望机械CAD** 已启动，且至少打开一张 DWG
- **Python 3.10+**（缺失时也会尝试 WorkBuddy 自带的 Python）
- **Node.js 18+**

## 快速开始

无需安装，客户端会自动拉起。把下面整段复制进 MCP 客户端的配置文件即可。

### 方案一：国内镜像（推荐国内用户）

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

### 方案二：npm 官方源

```json
{
  "mcpServers": {
    "zwcad": {
      "command": "npx",
      "args": ["-y", "zwcad-mcp"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

两者只有 `args` 里的 `--registry` 参数不同，功能完全一致。国内网络用方案一可显著加快首次拉取。

各平台的配置文件位置见 [`connector/`](connector/) 目录。

## 验证接入

让 AI 调用 `zwcad_get_capabilities`：能返回 ZWCAD 版本与工具组清单即表示接入成功。

失败时用 `zwcad_diagnose`（机械问题用 `zwcad_mech_diagnose`）查看逐项探测结果。

## 排障

```bash
npx -y zwcad-mcp --doctor      # 环境诊断：Python 候选、虚拟环境、逐项依赖导入
npx -y zwcad-mcp --prepare     # 只准备运行时后退出
```

| 现象 | 处理 |
|---|---|
| 首次使用提示「正在首次初始化」 | **正常**，正在后台安装依赖（约 40 秒），握手本身只需约 120 ms |
| 报「未找到可用的 Python」 | 装 Python 3.10+，或设 `ZWCAD_MCP_PYTHON` 指向已有解释器 |
| 装依赖失败（网络） | 设 `ZWCAD_MCP_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` |
| 运行时损坏 | 删 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\` 后重连，自动重建 |
| 机械工具报 `TYPELIB_NOT_LOADED` | 需中望机械CAD 已启动；平台工具不受影响 |

### 错误日志

启动器会把**失败事件**写入本地文件，便于排查与反馈：

```
%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\logs\error.log
```

- 每行一条 JSON：时间、事件名、Node 版本、诊断细节
- **只记失败**，不记成功的工具调用，不含图纸内容
- 单文件上限 1 MB，自动轮转为 `.1`~`.3`，总量 ≤ 4 MB
- `--doctor` 末尾会直接打印最近 10 条摘要，多数情况无需打开文件

可用 `ZWCAD_MCP_LOG_DIR` 改变存放位置。

### 关于 `-2147312566`（加载类型库/DLL 时出错）

这是机械类型库的已知情况，**不是文件缺失或权限问题**。实测根因：`ZwmToolKit.tlb` 内部引用了另一个仅在 ZWCAD 进程运行时才可解析的类型库。

- `ZwmToolKit.tlb` 本身可被正常加载，14 个类型中仅 `ZwmBalloonWrapper` 的第二个基类型解析失败
- 该接口自身 `cFuncs=0`、`cVars=0`，是无方法的空壳，**跳过不损失功能**
- **处置**：确保 ZWCAD 已启动并打开 DWG，然后完全重启 MCP 连接器

## 环境变量

| 变量 | 说明 |
|---|---|
| `ZWCAD_MCP_PYTHON` | 指定 Python 解释器路径，跳过自动探测（无效时报错而非静默回退） |
| `ZWCAD_MCP_PIP_INDEX` | 指定 pip 镜像源 |
| `ZWCAD_MCP_LOG_DIR` | 指定错误日志目录，默认在 `zwcad-mcp-runtime/logs` |

## 仓库结构

```
├── bridge.js                Node 启动器（npm bin 入口）
├── runtime/zwcad2d/         Python 源码（本仓库为唯一源头）
├── connector/               各平台连接器元数据
│   ├── workbuddy/           connector-meta + mcp.json + icon + skills
│   ├── cursor/              mcp.json
│   └── claude/              mcp.json
├── scripts/build-connector.js   扁平化构建各平台分发包
└── pyproject.toml           Python 包定义（同时可发布到 PyPI）
```

## 开发

```bash
npm run doctor            # 环境诊断
npm run build:connector   # 生成 dist/<platform>/ 可提交的分发包
npm run check:connector   # 校验各平台源目录结构
```

`runtime/zwcad2d/` 是本项目的唯一源码位置。发布 npm 时随包分发，无需再从 PyPI 安装。

## 依赖

安装时自动准备：`fastmcp`、`pyzwcad`、`pyzwcadmech`、`comtypes`、`pywin32`。

第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

MIT
