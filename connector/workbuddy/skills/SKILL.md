---
name: zwcad-mcp-skill
description: ZWCAD 2D 平台与中望机械 — 绘制、查询、标注、块/图层/样式管理、图框/标题栏/BOM/球标等机械能力
version: "0.1.0"
author: "ZWCAD 2D MCP Team"
---

# ZWCAD 2D 建模与制图 Skill

本 Skill 让 AI 通过自然语言操作中望 CAD（ZWCAD）二维图纸与中望机械扩展：绘制实体、查询图面、标注尺寸、管理图层/块/样式，以及机械图框、标题栏、BOM 明细表等。

---

## 环境前提检查（任务开始前必须先做）

本 MCP 经 **Windows COM** 连接本机正在运行的 ZWCAD。每次任务前按 ①②③ 检查，任一不满足则按对应话术告知用户并停止调用工具。

### ① Windows 系统

`os.name != 'nt'` / `sys.platform != 'win32'` 即不满足，直接告知：

> 本 MCP 只支持 Windows 环境，当前系统无法使用。请更换到 Windows 电脑，安装 ZWCAD 后再试。

### ② 已安装中望 CAD（或中望机械 CAD）

检测 `zwcad_get_capabilities(probe_cad=true)` / `zwcad_get_app_info(scope="cad")`；返回 `platform.available=false`、`Connection closed` 或 COM 错误 `-2147221008` 即不满足。

- **未安装**：

> 当前电脑没有安装 ZWCAD，本 MCP 无法使用。请先到这里下载并安装：https://www.zwsoft.cn/product/zwcad
> 装好后：① 打开 ZWCAD 并新建/打开一张 DWG；② 在连接器管理页重连「中望CAD MCP」；③ 回来让我继续。

- **已装未启动**：提示启动 ZWCAD 并打开/新建 DWG，重连后继续。

### ③ 运行依赖 Python 3.10+（首次使用需等待约 40 秒安装）

本连接器通过 `npx -y zwcad-mcp` 启动。启动器分为两层，**握手与安装是分开的**：

- **JS 层**（随包分发，立即可用）：完成 MCP 握手、暴露全部工具定义、转发请求
- **Python 层**（首次使用时现场安装）：实际执行 COM 调用与绘图逻辑

因此**首次使用时 MCP 可以瞬间连上**（实测 117 ms），工具列表也立刻可见。
真正的 Python 运行时在后台准备，约 40 秒，期间工具调用会返回「正在初始化」。

启动器按以下顺序寻找 Python，取第一个可用（>=3.10）的：

| 优先级 | 位置 |
|---|---|
| 1 | 环境变量 `ZWCAD_MCP_PYTHON` 指定的解释器 |
| 2 | WorkBuddy 托管 Python：`%USERPROFILE%\.workbuddy\binaries\python\versions\<ver>\python.exe` |
| 3 | WorkBuddy 安装目录自带：`%LOCALAPPDATA%\Programs\WorkBuddy\resources\vendor\python\python.exe` |
| 4 | 系统 `python` / `python3` |

---

#### 首次使用：如何向用户解释

工具调用若返回 `isError: true` 且文案为「**正在首次初始化（准备 Python 运行时并安装依赖）**」，
说明这是该用户**第一次使用**，属正常流程，**不是故障**。

此时必须这样做：

1. **优先调用 `zwcad_wait_ready` 等一次**（推荐）

   ```
   zwcad_wait_ready(timeout_seconds: 90)
   ```

   它会阻塞到就绪或超时，直接给出确定结论。在它返回前**不要调用其它工具**。

2. 同时**明确告知用户这是一次性安装**，例如：
   > 这是首次使用，我正在为你准备运行环境（约 40 秒），完成后立即继续。请稍候。

3. **不要反复重试同一个业务工具**——每秒重试一次也不会更快，只会刷屏。

4. **不要下「不可用」「安装失败」之类的结论**——这会让用户误以为环境有问题。

5. 就绪后**直接继续原任务**，无需让用户重新发起请求。

> 判据：错误文案里出现「正在首次初始化」= 等待状态；
> 出现「运行时准备失败」= 真故障。

`zwcad_wait_ready` 的返回含义：

| 返回 | 含义 | 下一步 |
|---|---|---|
| `isError: false` | 已就绪 | 直接继续原任务 |
| `isError: true`，文案含「仍在安装依赖」 | 还在装，超时了 | 再调一次等待，或告知用户继续等待 |
| `isError: true`，文案含「初始化失败」 | 真故障 | 按下一节处置 |

#### 确实失败时：如何向用户报告

工具返回 `isError: true` 且文案含「**Python 运行时准备失败**」，才是真故障。

**先自己查，不要一上来就让用户跑命令**：

```
zwcad_launcher_diagnose(limit: 10)
```

本工具由启动器实现，**在 Python 未就绪时也可用**，会返回运行时状态、
Python 路径与版本、以及最近若干条错误记录（含 pip 镜像源、依赖清单、耗时等细节）。

按返回内容判断原因，再用自然语言告诉用户**具体怎么修**：

| 错误事件 | 原因 | 你该告诉用户什么 |
|---|---|---|
| `runtime.python.not_found` | 本机没有 3.10+ 的 Python | 装 Python（建议 3.13），或设 `ZWCAD_MCP_PYTHON` 指向已有解释器 |
| `runtime.install.failed` | 依赖装不上，多为网络 | 设 `ZWCAD_MCP_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` 后重连；detail 里能看到具体报错 |
| `python.spawn.failed` / `python.exited` | Python 起不来或崩溃 | detail 里的 `stderrTail` 是 Python 自身的异常堆栈，据此说明问题 |
| 无错误记录但仍不可用 | 多半仍在安装 | 让其再等，或用 `zwcad_wait_ready` 继续等 |

只有在你判断需要人工介入（例如依赖冲突、磁盘损坏等无法靠改配置解决的情况）时，
才让用户把日志文件发给开发团队：

```
%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\logs\error.log
```

> 隐私说明：日志只记录**错误事件与运行环境信息**，不包含图纸内容，
> 也不记录成功的工具调用。

| 现场情况 | 处置 |
|---|---|
| 工具可正常返回 | 就绪，无需操作 |
| 需要彻底重置 | 删除 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\` 后重连，会自动重建 |

> 用户若在终端自行排查，可执行 `npx -y zwcad-mcp --doctor`，
> 它会打印同样的诊断信息与最近 10 条错误摘要。

> 若环境已把 `zwcad-mcp` pip 装进某个 Python 环境，可改用 `python -m zwcad2d` 直接启动，跳过自动准备流程。

---

## 工具发现机制（重要）

- 全部工具由 **MCP 服务动态提供**（`tools/list`），当前版本 41 个（平台 26 + 机械 11 + 诊断 2 + 启动器侧 2），随版本演进可能增减。工具名称、参数与输入 schema 一律**以 MCP 服务返回为准**，本文档不再重复罗列。

> 启动器侧的两个工具在 Python 未就绪时也可用：
> `zwcad_wait_ready`（等待就绪）、`zwcad_launcher_diagnose`（诊断运行时与错误日志）。
- 调用前先查目标工具的 description 与 input schema，再按其说明传参（角度单位为弧度）。
- 工具调用失败时优先读取返回的 `error` / `code` / `hint` 字段，并结合「常见错误场景」排查。

> **参考信息源**：本 Skill 涉及的工具详细说明、类型库加载机制与重要说明，以连接器内随包的 README 为准，位于 `node_modules/zwcad-mcp/README.md`；与其同源的完整版（含 39 个 Python 侧工具逐项说明、类型库 5 级回退策略）见项目仓库的 `README.md`。
>
> 若改用 `pip install zwcad-mcp` 安装方式，则 README 位于对应 Python 环境的 `site-packages` 目录下，也可这样读取全文：
>
> ```bash
> python -c "import importlib.metadata as m; print(m.metadata('zwcad-mcp')['Description'])"
> ```
>
> 下文中的「README」均指此文档。

---

## 进程与文档安全约束（必须遵守）

- **严禁自行强行关闭 ZWCAD 进程**：未经用户同意，不得以任何方式终止 ZWCAD 进程。
- **破坏性操作必须先确认**：删除实体、覆盖保存、关闭文档、替换插件、修改系统变量等写操作会直接改变当前 DWG，**执行前必须向用户说明并征得同意**，必要时提示先备份图纸。
- **单活动实例**：同一时间只操作一个 ZWCAD 系列实例。多开后 COM 可能连接非预期窗口，操作前先调用 `zwcad_get_capabilities(probe_cad=true)` 确认活动产品与图纸。

---

## 工作流原则（按序执行）

1. **环境与开画前探测**：先按上文「环境前提检查」确认 ①Windows 平台、②已安装 ZWCAD 且打开 DWG、③Python 运行时可用；再调用 `zwcad_get_capabilities(probe_cad=true)` 确认活动产品、图纸、已挂载工具组；`zwcad_get_app_info(scope="cad")` 验证平台 COM。
2. **先查询后操作**：绘图、标注、变换、样式管理等操作前，先用对应查询工具确认目标实体、名称与参数，避免盲目调用；同批绘制用 `zwcad_draw_batch` 一次提交，减少交互轮次。
3. **机械流程**：机械诊断 → 初始化标准 → 建图框 → 填标题栏 → 球标/BOM，按此顺序执行。
4. **排障**：`zwcad_diagnose(probe_cad=true)` 获取平台/机械连接状态与修复建议。

> 绘图/标注/变换/样式管理等工具的名称、参数与枚举值不再在此罗列；调用前通过 `tools/list` 及目标工具的 description 与 input schema 获取最新定义，亦可查阅 README「工具详细说明」。

---

## 常见错误场景

| 错误场景           | 表现                                                                                            | 解决方案                                                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| 非 Windows 系统    | 运行环境为 macOS/Linux，工具全部不可用                                                          | 直接告知用户"本 MCP 只支持 Windows"并停止操作，详见「环境前提检查 ①」                                                                     |
| 未安装 ZWCAD       | 探测不到 CAD 实例：`platform.available=false` / `Connection closed`                            | 直接告知用户下载安装 ZWCAD：https://www.zwsoft.cn/product/zwcad ，装好打开 DWG 后再重连，详见「环境前提检查 ②」                            |
| 未找到 Python      | 报「未找到可用的 Python（需要 >= 3.10）」                                                        | 安装 Python 3.10+（建议 3.13），或设 `ZWCAD_MCP_PYTHON` 指向已有解释器，见「③」                                                           |
| 首次使用提示正在初始化 | 工具返回「**正在首次初始化**…约需 30~60 秒」 | **正常流程，不是故障**。告知用户等待，20~30 秒后重试，勿频繁刷屏，详见「③」 |
| 运行时确实失败     | 工具返回「**Python 运行时准备失败**」                                                              | 调 `zwcad_launcher_diagnose` 自取错误详情，按「③ 确实失败时」处置                                         |
| 首次安装后工具仍不可用 | 提示仍是「正在首次初始化」，但已超过 2 分钟                                                   | 调 `zwcad_launcher_diagnose` 查错误记录；确认无错误后可删 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\` 后重连 |
| 运行时损坏         | 上次可用、某次突然启动失败                                                                        | 调 `zwcad_launcher_diagnose` 看错误记录；确认后删 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\` 重连自动重建                         |
| 未启动/未打开图纸  | `Connection closed`、COM 初始化错误（-2147221008）                                              | 启动 ZWCAD 并打开 DWG，重启 MCP                                                                                                             |
| 机械工具失败       | `MECHANICAL_NOT_AVAILABLE` / `TYPELIB_NOT_LOADED`                                               | 调用 `zwcad_mech_diagnose` 获取逐项探测结果与修复建议；类型库加载机制与 `PYZWCADMECH_TLB_PATH` 配置详见 README「ZwmToolKit 类型库加载机制」 |
| 机械类型库解析失败 | `TYPELIB_NOT_LOADED`，`typelib_error` 含 `-2147312566`（`0x80029C4A`）「加载类型库/DLL 时出错」 | 按下方「机械类型库加载失败（-2147312566）的处置」分步处理，多数情况在第 1~3 步内解决                                                        |
| 安装依赖失败       | 启动报 pip 相关错误（超时/网络）                                                                | 设置 `ZWCAD_MCP_PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple` 后重连                                                                  |
| 中文乱码           | `mbcs codec can't decode bytes`                                                                 | 确认使用本项目 server（`PYTHONUTF8=1`）并完全重启 WorkBuddy                                                                                 |
| 操作错窗口         | 多开时改到非预期图纸                                                                            | 关闭其他 ZWCAD 实例，仅保留目标产品后重连（单活动实例策略详见 README「重要说明」及上文「安全约束」）                                        |
| 实体找不到         | 返回空结果                                                                                      | 先用 `zwcad_get_objects_in_model` 确认真实 object_type/handle，再过滤                                                                       |
| 参数不匹配         | `缺少参数` 错误                                                                                 | 对照工具 description 补全必填字段；角度用弧度                                                                                               |
| 机械样式缺失       | 图框/标题栏操作报 XML 解析错误                                                                  | 用 `zwcad_get_app_info(scope="mech_style_path")` 查询本机实际样式路径并确认存在对应标准（GB 等）；路径与版本对应关系详见 README「重要说明」 |

> 工具返回的 `error` / `code` / `hint` 三字段是排障的权威信息；表中未覆盖的错误码同样按这三字段排查，并以工具 description 为准。

### 机械类型库加载失败的处置

**场景**：机械工具（图框/标题栏/明细表等）返回 `TYPELIB_NOT_LOADED`；`zwcad_mech_diagnose` 显示 `typelib_loaded: false`、`typelib_error` 含 `-2147312566`（`0x80029C4A`）「加载类型库/DLL 时出错」。按顺序处置：

1. **确认产品环境（多数在此解决）**：已装与 ZWCAD 版本匹配的中望机械、已完整启动并打开至少一张 DWG；近期卸载/升级/移动过组件的，以管理员身份执行**修复安装**，确保 `ZwmToolKit` 正确注册。
2. **核对类型库路径**：依赖 `ZwmToolKit.tlb`，默认在 `C:\Program Files\ZWSOFT\ZWCAD Mechanical <版本> Chs\Zwcadm\ZwmToolKit.tlb`；自动定位失败则把实际路径设为 `PYZWCADMECH_TLB_PATH` 并**完全重启 MCP 连接器**（须重建服务进程，仅重开客户端无效）。
3. **诊断与验证**：`zwcad_mech_diagnose` 复查 `typelib_loaded` / `typelib_source` / `typelib_error`。
4. **实测结论（重要，避免走弯路）**：`-2147312566` 的根因是类型库内部引用了另一个**仅在 ZWCAD 进程运行时才可解析**的类型库，不是文件缺失或权限问题。具体表现：
   - `ZwmToolKit.tlb` 本身可被 `LoadTypeLibEx` 成功加载，14 个类型中仅 `ZwmBalloonWrapper` 的第二个基类型（外部引用）解析失败；
   - 该接口自身 `cFuncs=0`、`cVars=0`，是不带任何方法的空壳，**跳过它不会损失功能**；
   - ZWCAD 未运行时，连 `GetModule("ZWCAD.Application")` 都会报同一错误。

   **因此正确处置是：先确保 ZWCAD 已启动并打开 DWG，然后完全重启 MCP 连接器。** 已实测「改 comtypes 源码做容错」这条路走不通——跳过两个 Balloon 类型后，代码生成器仍会在 `ComInterfaceBody` 处因另一个 None 崩溃，且需改动第三方库，不可持续。

   > 本连接器的 Python 环境固定在 `%USERPROFILE%\.workbuddy\zwcad-mcp-runtime\venv\`（用 `npx -y zwcad-mcp --doctor` 确认实际路径）。

---

## 最佳实践

- **修改优先于重建**：能用 `zwcad_transform_entity` / `zwcad_modify_entity` 就不删了重画。
- **单位与弧度**：坐标沿用当前图纸单位；角度一律弧度。
- **样式走 XML**：标准/标题栏/BOM/图框样式名来自本机 `styles/*.xml`，可用 `zwcad_mech_manage_*` 查询后使用。

> 探测先行、先查询后操作、写入需确认等约定已并入「工作流原则」与「进程与文档安全约束」，不再重复罗列；工具能力细节见 README「工具详细说明」。
