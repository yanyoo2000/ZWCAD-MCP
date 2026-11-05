# ZWCAD MCP

中望CAD（ZWCAD）MCP 服务，让大模型通过 [MCP 协议](https://modelcontextprotocol.io)直接操控中望CAD 平台与中望机械CAD，完成绘图、标注、图层/块/样式管理，以及图框、标题栏、明细表（BOM）等机械操作。

**共计 41 个工具**：平台 27 个（不依赖机械环境）+ 机械 11 个（需中望机械CAD）+ 诊断 2 个 + 启动器侧 1 个。

> 启动器侧的 `zwcad_wait_ready` 在 Python 运行时尚未就绪时也可用，用于等待初始化完成。
> 诊断统一走 `zwcad_diagnose`：Python 已就绪时返回完整诊断，
> 未就绪时自动降级为启动器视角（含错误日志与环境信息）并标注未检测项。

## 环境要求

通用（三条渠道都需要）：

- **Windows 10/11 x64**
- **中望CAD 或 中望机械CAD** 已启动，且至少打开一张 DWG

按渠道不同：

| 渠道 | Python | Node.js |
|---|---|---|
| npm | 3.10+（缺失时会尝试 WorkBuddy 自带的 Python） | 18+ |
| PyPI | 3.10+（自备） | 不需要 |
| 离线包 | **不需要**（自带） | 推荐 18+，无 Node 时可用纯 Python 方式 |

## 获取方式

同一个服务，三条发布渠道，工具集与版本号完全一致。

### npm

包页：https://www.npmjs.com/package/zwcad-mcp

```bash
npx -y zwcad-mcp           # 免安装，客户端会用它拉起服务
npm install -g zwcad-mcp   # 或装到本地
zwcad-mcp --doctor         # 环境诊断
```

### PyPI

包页：https://pypi.org/project/zwcad-mcp/

```bash
pip install zwcad-mcp
python -m zwcad2d          # 以 stdio 方式启动 MCP 服务
```

### 离线包（内网 / 产线环境）

发布页：https://github.com/yanyoo2000/ZWCAD-MCP/releases

下载 `zwcad-mcp-<版本>-win-x64-offline.zip`，**解压即用**。

适用于三种约束同时成立的机器：**没有 Python 环境、不能联网、不允许自行安装**。

包内自带便携式 Python 与全部依赖，运行时不访问网络。解压后的结构：

```
zwcad-mcp-<版本>-win-x64-offline/
├── bridge.js  lib/  tools.json      启动器
├── runtime/zwcad2d/                 Python 源码
├── python/                          便携式 Python
├── site-packages/                   预装依赖
├── mcp.json                         客户端配置模板
└── 使用说明.txt
```

客户端配置（推荐，需要机器上有 Node.js）：

```json
{
  "mcpServers": {
    "zwcad-2d": {
      "command": "node",
      "args": ["<解压路径>\\bridge.js"]
    }
  }
}
```

> 机器上没有 Node.js 时，可改用纯 Python 启动。包内 `使用说明.txt` 提供了
> 填好全部 `PYTHONPATH` 的完整配置——**直接复制即可，不要手写**：
> pywin32 的模块与 DLL 分散在三处，漏一段会报 `No module named 'pywintypes'`。

> **三条渠道的区别**：
>
> | | npm | PyPI | 离线包 |
> |---|---|---|---|
> | 需要 Node.js | ✅ | ❌ | 推荐 |
> | 需要 Python | 自动准备 | ✅ 自备 | ❌ 自带 |
> | 需要联网 | 首次需要 | 安装时需要 | ❌ 不需要 |
> | 适合场景 | 普通开发机 | 已有 Python 环境 | 内网 / 产线 |
>
> 本页按 npm 版书写；PyPI 版说明见
> [README-pypi.md](https://github.com/yanyoo2000/ZWCAD-MCP/blob/main/README-pypi.md)，
> 离线包部署细节见包内 `使用说明.txt`。

### 版本说明

查看当前版本：

```bash
npm view zwcad-mcp version       # npm 渠道
pip index versions zwcad-mcp     # PyPI 渠道
```

固定版本（避免自动升级到不兼容版本）：

```bash
npx -y zwcad-mcp@<版本>          # 把 <版本> 换成上面查到的具体版本号
pip install zwcad-mcp==<版本>
```

各版本的具体变更见 [Releases](https://github.com/yanyoo2000/ZWCAD-MCP/releases)——
每次发布都由 CI 自动列出「改动了哪些 PR」并附完整 diff 链接。

> **关于 PyPI 上的 `0.1.0` ~ `0.1.4`**：那是早期纯 Python 版本的独立发布线，
> 与 npm 版的 `0.1.x` **不是同一批产物**。从 `0.3.2` 起两条渠道的版本号才统一。

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
├── bridge.js                     Node 启动器（npm bin 入口，含离线模式判定）
├── runtime/zwcad2d/              Python 源码（本仓库为唯一源头）
├── connector/                    各平台连接器元数据
│   ├── workbuddy/                connector-meta + mcp.json + icon + skills
│   ├── cursor/                   mcp.json
│   └── claude/                   mcp.json
├── scripts/
│   ├── build-connector.js        扁平化构建各平台分发包
│   ├── build-offline.js          构建离线包（自带 Python 与依赖）
│   ├── export-tools.py           从 Python 导出工具清单到 tools.json
│   └── sync-version.py           同步四处版本号
└── pyproject.toml                Python 包定义（发布到 PyPI）
```

## 开发

```bash
npm run doctor            # 环境诊断
npm run build:connector   # 生成 dist/<platform>/ 可提交的分发包
npm run check:connector   # 校验各平台源目录结构
npm run build:offline     # 构建离线包到 dist-offline/
```

`runtime/zwcad2d/` 是本项目的唯一源码位置。发布 npm 时随包分发，无需再从 PyPI 安装。

> 本地 `build:offline` 用的是机器上的精简 Python，产物约 37 MB，仅供调试。
> 对外发布的离线包由 CI 用官方 CPython 构建（约 77 MB），体积更大但兼容性更稳妥。

## 依赖

Python 依赖：`fastmcp`、`pyzwcad`、`pyzwcadmech`、`comtypes`、`pywin32`。

| 渠道 | 依赖怎么来 |
|---|---|
| npm | 启动器首次运行时自动创建 venv 并安装 |
| PyPI | `pip install zwcad-mcp` 时自动装（`pyproject.toml` 已声明） |
| 离线包 | **已预装**，无需任何操作 |

第三方组件许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## License

MIT
