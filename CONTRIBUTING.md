# 贡献指南

感谢你愿意改进这个项目。本文说明如何提交改动、以及合并前必须满足什么。

## 快速上手

```bash
# 1. Fork 本仓库，然后克隆你自己的 fork
git clone https://github.com/<你的用户名>/ZWCAD-MCP.git
cd ZWCAD-MCP

# 2. 安装依赖
npm install                          # JS 侧（实际只有开发期工具）
pip install fastmcp pyzwcad pyzwcadmech comtypes pywin32   # Python 侧

# 3. 准备运行时环境（可选，用于真实调用验证）
node bridge.js --prepare
```

## 环境要求

| 依赖 | 版本 | 说明 |
|---|---|---|
| Node.js | >= 18 | 启动器与连接器构建 |
| Python | >= 3.10 | 业务逻辑（COM 调用） |
| 中望CAD | 2024+ | 真实调用验证时需要；纯改代码可不装 |

## 项目结构

```
├── bridge.js              Node 启动器入口
├── lib/
│   ├── proxy.js           代理层：握手、转发、代理工具
│   └── errorlog.js        本地错误日志
├── runtime/zwcad2d/       Python 源码（本仓库为唯一源头）
│   └── server.py          MCP 服务与 40 个工具的实现
├── tools.json             ★ 从 Python 导出的工具清单（见下方「必须重新导出」）
├── connector/             各平台连接器元数据
└── scripts/               构建与测试脚本
```

## 改动后必须做的三件事

### 1. 如果改了 Python 工具，必须重新导出 tools.json

```bash
python scripts/export-tools.py
```

**这一步最容易忘，也最容易让 CI 失败。**

原因：代理层需要在整个 Python 运行时装好之前就应答 `tools/list`（这是首次使用不超时的关键）。所以工具清单在构建期从 Python 导出成静态文件 `tools.json`，随包分发。

改了 `@mcp.tool` 装饰的函数、增删工具、改工具描述或参数，都要重新导出。CI 会比对两者，不一致直接失败。

### 2. 跑本地检查

```bash
npm run check:connector    # 连接器目录结构
npm test                   # 错误日志与轮转测试
node --check bridge.js     # 语法
```

### 3. 确认发布包干净

```bash
npm pack --dry-run | grep -i pycache
```

出现 `__pycache__` 说明 `.npmignore` 有问题（历史上漏进过一次）。

## 提交 PR

```bash
git checkout -b feat/your-feature   # 分支名用 feat/ 或 fix/ 前缀
git commit -m "feat: 简明描述改了什么"
git push origin feat/your-feature
```

然后在 GitHub 上向 `main` 发起 Pull Request。

### 提交信息约定

用 `<类型>: <说明>` 形式，类型取以下几种：

| 类型 | 用于 |
|---|---|
| `feat` | 新增工具或能力 |
| `fix` | 修 bug |
| `docs` | 只改文档 |
| `refactor` | 重构，行为不变 |
| `chore` | 版本号、依赖、构建配置 |
| `test` | 只改测试 |

说明部分用中文即可，写清「改了什么」和「为什么」。

### 版本号不要自己改

`package.json` 的 `version` 由维护者在发布时统一修改。如果你在 PR 里改了，合并时容易冲突。

## CI 会检查什么

PR 创建后自动运行（见 `.github/workflows/ci.yml`）：

| 检查 | 说明 |
|---|---|
| 连接器结构 | `connector/<平台>/<连接器>/` 下的必需文件齐全 |
| Python 可编译 | 所有 `.py` 能通过 `ast.parse` |
| JS 语法 | `bridge.js`、`lib/*.js`、`scripts/*.js` |
| tools.json 一致 | 重新导出后与仓库中的文件比对 |
| 单元测试 | `npm test` |
| 打包检查 | 产物文件列表、不含 `__pycache__` |
| 安装冒烟 | 打包后能被 npm 正常安装 |
| 版本一致 | 仅打 tag 时检查 tag 与 `package.json` |

**所有检查必须先本地跑通。** CI 失败的分支维护者不会 review。

## 审核标准

维护者 review 时会关注：

- **是否会破坏现有用户**：新增工具安全，改已有工具的参数或不返回结构需要谨慎
- **错误处理**：工具内部异常要用 `_err()` 包装，返回结构化错误而非抛异常
- **文档同步**：改了行为要更新 `README.md`，改了首次使用流程要更新 `connector/workbuddy/zwcad-mcp/skills/SKILL.md`
- **不要引入无法回退的写入**：涉及删实体、覆盖保存、关闭文档的操作，保持与现有工具一致的安全约束

## 关于机械功能

`runtime/zwcad2d/server.py` 里分两类工具：

| 类型 | 依赖 | 说明 |
|---|---|---|
| 平台工具 | 只要 ZWCAD 运行 | 绘图、标注、查询、文档管理 |
| 机械工具 | 中望机械CAD + ZwmToolKit 类型库 | 图框、标题栏、BOM、球标 |

改机械部分要特别注意类型库加载的 5 级回退策略（见 `README.md`）。没有中望机械环境也能改代码，但**无法真实验证**，请在 PR 里说明这一点。

## 遇到问题

- 功能不符合预期 → 开 Issue，附上 `node bridge.js --doctor` 的输出
- 不确定怎么改 → 先开 Issue 讨论，避免白做
- CI 失败但本地通过 → 检查 Node/Python 版本是否与 CI 一致（Node 20 / Python 3.12）
