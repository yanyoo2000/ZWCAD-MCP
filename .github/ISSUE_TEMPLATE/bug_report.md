---
name: 缺陷报告
about: 工具调用失败、连接异常、首次安装卡住等问题
title: '[Bug] '
labels: bug
---

## 现象

<!-- 具体发生了什么。贴上报错原文，不要只写「用不了」 -->

## 复现步骤

1.
2.
3.

## 诊断信息

**先执行这条命令，把输出贴上来：**

```bash
npx -y zwcad-mcp --doctor
```

它会列出 Python 候选、虚拟环境状态、逐项依赖导入结果，以及最近的错误日志摘要。

<details>
<summary>粘贴 --doctor 输出</summary>

```
（这里粘贴）
```

</details>

## 环境

| 项 | 值 |
|---|---|
| Windows 版本 | |
| ZWCAD 版本与产品 | <!-- 如：ZWCAD 2026 / 中望机械CAD 2026 --> |
| 是否打开了 DWG | |
| npm 包版本 | <!-- npx zwcad-mcp --version 或看装的是哪个版本 --> |
| Node 版本 | |
| MCP 客户端 | <!-- WorkBuddy / Cursor / Claude Desktop，及版本 --> |

## 是否首次使用

- [ ] 第一次用就失败
- [ ] 之前能用，某次开始失败

> 首次使用时需要装 Python 运行时（约 40 秒）。如果是在这期间失败，请说明等待了多久。
