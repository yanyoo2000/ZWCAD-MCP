#!/usr/bin/env node
/**
 * ZWCAD-MCP 代理层：在 Python 运行时尚未就绪时先完成 MCP 握手。
 *
 * 为什么需要它
 * ------------
 * 首次使用时要在本机创建 venv 并安装 90+ 个 Python 依赖，实测约 40~70 秒。
 * 而 MCP 客户端的 initialize 超时通常只有 30 秒，于是首次连接必定超时失败。
 *
 * 解决办法是把「握手」和「真正干活」拆开：
 *
 *   1. 启动瞬间立刻响应 initialize / tools/list（清单来自随包的 tools.json）
 *   2. 同时后台创建 venv、安装依赖
 *   3. Python 就绪后，把后续请求转发给它
 *   4. 未就绪期间收到工具调用，直接返回可读的错误，而不是挂住
 *
 * 这样客户端永远不会超时，用户只会看到「首次使用需等待初始化」这类说明性错误。
 *
 * 协议要点
 * --------
 * MCP stdio 是「一行一个 JSON-RPC 消息」。本文件必须在两种模式下都保持这个格式：
 * - 本地应答：直接向 stdout 写一行 JSON
 * - 转发模式：与子进程管道对接，逐行转发
 *
 * 请勿在 stdout 输出任何非 JSON 内容（日志一律走 stderr）。
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');
const { logError, logFilePath } = require('./errorlog');

const PROTOCOL_VERSION = '2024-11-05';

/**
 * 影子握手使用的 JSON-RPC id。
 *
 * 用一个客户端几乎不会用到的大整数，避免与真实请求的 id 撞车
 * （真实客户端的 id 通常从 1、2 递增）。
 */
const SHADOW_INIT_ID = 9_000_001;

/* ------------------------------------------------------------------ *
 * 工具清单
 * ------------------------------------------------------------------ */

/**
 * 由代理层（而非 Python）实现的工具。
 *
 * 这些工具在 Python 未就绪时也要可用，因此不在 tools.json 里，
 * 而是在这里注入到 tools/list 的返回结果中。
 */
const PROXY_TOOLS = [
  {
    name: 'zwcad_wait_ready',
    description:
      '等待 ZWCAD MCP 服务就绪。首次使用需安装 Python 运行时（约 40 秒），' +
      '在此期间调用其它工具会返回「正在首次初始化」。' +
      '与其反复重试，不如调用本工具等一次拿到确定结果。\n\n' +
      '返回 ready=true 表示可以正常调用其它工具；' +
      'ready=false 时 state 为 preparing（仍在安装，可再等）或 failed（真故障，需按 error 处置）。',
    inputSchema: {
      type: 'object',
      properties: {
        timeout_seconds: {
          type: 'number',
          description: '最长等待秒数，默认 60，上限 300。',
        },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'zwcad_launcher_diagnose',
    description:
      '诊断启动器与 Python 运行时的状态，并返回最近的错误记录。\n\n' +
      '与 zwcad_diagnose 的区别：本工具由 JS 启动器实现，**在 Python 尚未就绪时也可用**，' +
      '因此是排查「首次安装失败 / 一直停在初始化」的首选；' +
      'zwcad_diagnose 依赖 Python 侧，只能在其就绪后调用。\n\n' +
      '返回内容包括：运行时状态、Python 解释器路径与版本、虚拟环境状态、' +
      '最近若干条错误日志（含事件名与诊断细节）。',
    inputSchema: {
      type: 'object',
      properties: {
        limit: {
          type: 'number',
          description: '返回最近多少条错误日志，默认 10，上限 50。',
        },
      },
      additionalProperties: false,
    },
  },
];

/**
 * 读取随包的工具清单，用于在 Python 未就绪时也能正确回应 tools/list。
 * 读取失败时仍返回代理层工具——客户端至少能连上并看到等待工具。
 */
function loadToolManifest(baseDir) {
  const p = path.join(baseDir, 'tools.json');
  let pythonTools = [];
  try {
    const raw = fs.readFileSync(p, 'utf8');
    const tools = JSON.parse(raw);
    if (Array.isArray(tools)) pythonTools = tools;
  } catch {
    /* 清单缺失不影响握手，只是暂时看不到 Python 侧工具 */
  }
  return pythonTools.concat(PROXY_TOOLS);
}

/* ------------------------------------------------------------------ *
 * 代理
 * ------------------------------------------------------------------ */

/**
 * @param {object} opts
 * @param {string[]} opts.childArgs     传给 python 的参数（通常是 ['-m','zwcad2d']）
 * @param {string}   opts.cwd           启动器目录
 * @param {() => Promise<string>} opts.ensureRuntime  返回 venv python 路径
 * @param {(msg: string) => void} opts.log            stderr 日志
 */
function createProxy(opts) {
  const { cwd, ensureRuntime, log } = opts;
  const tools = loadToolManifest(cwd);

  /** 客户端 → 我们的待处理缓冲 */
  let inputBuffer = '';
  /** 子进程 → 我们的待处理缓冲 */
  let childBuffer = '';
  /**
   * 子进程 stderr 的尾部。
   *
   * Python 崩溃时异常堆栈在这里，是事后定位问题的关键依据，
   * 所以即使已经转发到自己的 stderr，也要留一份用于写错误日志。
   */
  let stderrTail = '';

  /** 子进程是否已就绪并接管 */
  let child = null;
  let childReady = false;
  /** 子进程就绪前收到的、需要转发的请求 */
  const pendingForChild = [];
  /** 运行时准备的结果 */
  let runtimeState = 'preparing'; // preparing | ready | failed
  let runtimeError = null;
  let runtimePromise = null;

  /* ---------------- 输出 ---------------- */

  function writeJson(obj) {
    process.stdout.write(JSON.stringify(obj) + '\n');
  }

  function reply(id, result) {
    writeJson({ jsonrpc: '2.0', id, result });
  }

  function replyError(id, code, message, data) {
    const err = { code, message };
    if (data !== undefined) err.data = data;
    writeJson({ jsonrpc: '2.0', id, error: err });
  }

  /* ---------------- 运行时 ---------------- */

  /**
   * 等待运行时就绪。
   *
   * 之所以提供这个能力：在 Python 安装完成前，AI 只能反复调用工具试探，
   * 既浪费轮次又刷屏。有了等待语义，AI 可以「等 60 秒拿结果」，
   * 拿到确定结论后再决定是继续任务还是报告故障。
   *
   * @param {number} timeoutMs 最长等待时间
   * @returns {Promise<{ready: boolean, state: string, error: string|null}>}
   */
  function waitForReady(timeoutMs) {
    if (runtimeState === 'ready' && childReady) {
      return Promise.resolve({ ready: true, state: 'ready', error: null });
    }
    if (runtimeState === 'failed') {
      return Promise.resolve({ ready: false, state: 'failed', error: runtimeError });
    }

    return new Promise((resolve) => {
      const deadline = Date.now() + timeoutMs;

      const tick = () => {
        if (runtimeState === 'failed') {
          resolve({ ready: false, state: 'failed', error: runtimeError });
          return;
        }
        // childReady 才算真正可用：Python 起来但握手未完时，请求转发仍会失败
        if (runtimeState === 'ready' && childReady) {
          resolve({ ready: true, state: 'ready', error: null });
          return;
        }
        if (Date.now() >= deadline) {
          resolve({
            ready: false,
            state: runtimeState === 'preparing' ? 'preparing' : runtimeState,
            error: runtimeError,
          });
          return;
        }
        setTimeout(tick, 500);
      };
      tick();
    });
  }

  function startRuntime() {
    if (runtimePromise) return runtimePromise;
    runtimePromise = (async () => {
      try {
        const py = await ensureRuntime();
        runtimeState = 'ready';
        log('运行时已就绪');
        onRuntimeReady(py);
        return py;
      } catch (e) {
        runtimeState = 'failed';
        runtimeError = e && e.message ? e.message : String(e);
        log('运行时准备失败：' + runtimeError);
        logError('runtime.ready.failed', { error: e });
        throw e;
      }
    })();
    // 后台任务不允许抛出未捕获异常
    runtimePromise.catch(() => {});
    return runtimePromise;
  }

  /**
   * Python 就绪：拉起子进程，把攒下的请求转发过去。
   */
  function onRuntimeReady(py) {
    if (childReady) return;

    const env = {
      ...process.env,
      PYTHONUTF8: '1',
      PYTHONIOENCODING: 'utf-8',
    };
    const src = path.join(cwd, 'runtime');
    if (fs.existsSync(path.join(src, 'zwcad2d'))) {
      env.PYTHONPATH = env.PYTHONPATH ? src + path.delimiter + env.PYTHONPATH : src;
    }

    log('启动 MCP Server…');
    child = spawn(py, opts.childArgs, { stdio: ['pipe', 'pipe', 'pipe'], env, windowsHide: true });

    // 保留 stderr 尾部：Python 若崩溃，异常堆栈在这里，是定位问题的关键
    child.stderr.on('data', (d) => {
      stderrTail = (stderrTail + d.toString('utf8')).slice(-8000);
      process.stderr.write(d);
    });

    child.stdout.on('data', (chunk) => {
      childBuffer += chunk.toString('utf8');
      let idx;
      while ((idx = childBuffer.indexOf('\n')) !== -1) {
        const line = childBuffer.slice(0, idx);
        childBuffer = childBuffer.slice(idx + 1);
        handleChildLine(line);
      }
    });

    child.on('error', (e) => {
      log('子进程启动失败：' + e.message);
      runtimeState = 'failed';
      runtimeError = e.message;
      logError('python.spawn.failed', { error: e, python: py, args: opts.childArgs });
    });

    child.on('close', (code) => {
      log('MCP Server 退出，code=' + code);
      // 非 0 退出往往是 Python 侧崩了，值得留痕；0 是正常结束
      if (code !== 0) {
        logError('python.exited', {
          code,
          initialized: childInitialized,
          stderrTail: stderrTail.slice(-3000),
        });
      }
      process.exit(code == null ? 1 : code);
    });

    // 关键：我们先替客户端做了 initialize，Python 侧对此一无所知。
    // 若不补这一次握手，它会以 -32602 拒绝后续所有请求：
    //   "Received request before initialization was complete"
    // 所以这里以「影子客户端」身份先完成 Python 的初始化，再放行业务请求。
    child.stdin.write(
      JSON.stringify({
        jsonrpc: '2.0',
        id: SHADOW_INIT_ID,
        method: 'initialize',
        params: {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: {},
          clientInfo: { name: 'zwcad-mcp-bridge', version: opts.version || '0.0.0' },
        },
      }) + '\n'
    );
  }

  /** Python 侧握手是否完成 */
  let childInitialized = false;

  /**
   * 处理来自 Python 的一行输出。
   *
   * 影子握手阶段要吞掉对应响应（客户端没发过这个请求，直接透传会造成困扰），
   * 其余一律原样转发给客户端。
   */
  function handleChildLine(line) {
    const trimmed = line.trim();
    if (!trimmed) return;

    if (!childInitialized) {
      let parsed = null;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        /* 非 JSON 行，交给下面统一处理 */
      }

      if (parsed && parsed.id === SHADOW_INIT_ID) {
        childInitialized = true;
        log('Python 侧握手完成');

        // 补发 initialized 通知，让 Python 真正进入可用状态
        try {
          child.stdin.write(
            JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' }) + '\n'
          );
        } catch {
          /* 忽略 */
        }

        childReady = true;
        flushPending();
        return;
      }

      // 握手响应之前，Python 可能输出启动日志之类的内容，一律不转发给客户端，
      // 否则可能干扰客户端的 JSON-RPC 解析。
      return;
    }

    process.stdout.write(trimmed + '\n');
  }

  /** 把等待中的请求按序转发 */
  function flushPending() {
    for (const line of pendingForChild.splice(0)) {
      try {
        child.stdin.write(line + '\n');
      } catch {
        /* 子进程可能已退出 */
      }
    }
  }

  /* ---------------- 主循环 ---------------- */

  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    inputBuffer += chunk;
    let idx;
    while ((idx = inputBuffer.indexOf('\n')) !== -1) {
      const line = inputBuffer.slice(0, idx).trim();
      inputBuffer = inputBuffer.slice(idx + 1);
      if (line) handleLine(line);
    }
  });

  process.stdin.on('end', () => {
    if (child && child.stdin.writable) {
      try {
        child.stdin.end();
      } catch {
        /* 忽略 */
      }
    } else {
      process.exit(0);
    }
  });

  function handleLine(line) {
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      // 不是 JSON，无法处理；如果是转发状态就原样丢给子进程
      if (childReady) child.stdin.write(line + '\n');
      return;
    }

    // 代理层自己实现的工具：任何状态下都由本地应答，不转发给 Python。
    if (msg.method === 'tools/call' && msg.id !== undefined) {
      const toolName = msg.params && msg.params.name;
      if (isProxyTool(toolName)) {
        handleProxyTool(msg);
        return;
      }
    }

    // tools/list 永远由本地静态清单应答，不受 childReady 影响。
    //
    // 原因：转发给 Python 需要等子进程 import 完 comtypes 等依赖（实测约 2 秒），
    // 而清单内容与 Python 返回的完全一致（tools.json 就是从同一版本导出的）。
    // 客户端在 initialize 之后通常立刻拉一次工具列表，这 2 秒是纯浪费。
    if (msg.method === 'tools/list' && msg.id !== undefined) {
      reply(msg.id, { tools });
      return;
    }

    // 一旦子进程接管，全部转发
    if (childReady) {
      child.stdin.write(line + '\n');
      return;
    }

    const method = msg.method;

    // 通知类消息（无 id）在未就绪时先攒着，等子进程起来再补发。
    if (msg.id === undefined) {
      if (!childRequiredFor(method)) return;
      pendingForChild.push(line);
      return;
    }

    switch (method) {
      case 'initialize':
        reply(msg.id, {
          protocolVersion: PROTOCOL_VERSION,
          capabilities: {
            tools: { listChanged: true },
          },
          serverInfo: {
            name: 'ZWCAD MCP Server',
            version: opts.version || '0.0.0',
          },
        });
        // 握手完成后再开始装依赖，避免占用首次响应时间
        startRuntime();
        return;

      case 'tools/call':
        handleToolCallBeforeReady(msg);
        return;

      case 'ping':
        reply(msg.id, {});
        return;

      default:
        // 其它请求（prompts/resources 等）在未就绪时给出明确错误
        replyError(
          msg.id,
          -32002,
          '服务正在初始化，请稍候重试。当前阶段：' + describeState()
        );
        return;
    }
  }

  function describeState() {
    if (runtimeState === 'preparing') return '正在准备 Python 运行时（首次使用约需 30~60 秒）';
    if (runtimeState === 'failed') return 'Python 运行时准备失败：' + runtimeError;
    return '就绪';
  }

  /**
   * 判断某条通知是否必须交给 Python 处理。
   *
   * `notifications/initialized` 在我们做影子握手时已经补发给 Python 了，
   * 客户端再发一次不能重复转发——否则 Python 会认为重复初始化而报错。
   * 其它通知（如根目录变更）才需要让 Python 知道。
   */
  function childRequiredFor(method) {
    return method !== 'notifications/initialized';
  }

  /** 是否是代理层自己实现的工具 */
  function isProxyTool(name) {
    return PROXY_TOOLS.some((t) => t.name === name);
  }

  /**
   * 执行代理层工具（当前只有 zwcad_wait_ready）。
   *
   * 返回值遵循 MCP 工具结果格式：content 数组 + 可选 isError。
   */
  async function handleProxyTool(msg) {
    const name = msg.params.name;
    const args = msg.params.arguments || {};

    if (name === 'zwcad_wait_ready') {
      let seconds = Number(args.timeout_seconds);
      if (!Number.isFinite(seconds) || seconds <= 0) seconds = 60;
      seconds = Math.min(seconds, 300);

      const r = await waitForReady(seconds * 1000);

      let text;
      if (r.ready) {
        text =
          `服务已就绪（等待 ${seconds} 秒内完成）。现在可以正常调用所有 ZWCAD 工具。\n\n` +
          `提示：接下来调用前，先确认 ZWCAD 已启动并打开了 DWG 文件。`;
      } else if (r.state === 'failed') {
        text =
          `服务初始化失败：${r.error}\n\n` +
          `下一步：调用 zwcad_launcher_diagnose 获取完整的错误记录与运行环境信息，` +
          `据此判断原因并告知用户具体怎么修。\n\n` +
          `常见原因：未安装 Python 3.10+、网络无法访问 PyPI、磁盘空间不足。`;
      } else {
        text =
          `已等待 ${seconds} 秒，初始化仍未完成，仍在安装依赖。\n\n` +
          `下一步：可再调用一次本工具继续等待；若累计超过 2 分钟仍无进展，` +
          `调用 zwcad_launcher_diagnose 查看错误记录。`;
      }

      writeJson({
        jsonrpc: '2.0',
        id: msg.id,
        result: {
          content: [{ type: 'text', text }],
          isError: !r.ready,
        },
      });
      return;
    }

    if (name === 'zwcad_launcher_diagnose') {
      let limit = Number(args.limit);
      if (!Number.isFinite(limit) || limit <= 0) limit = 10;
      limit = Math.min(limit, 50);

      writeJson({
        jsonrpc: '2.0',
        id: msg.id,
        result: { content: [{ type: 'text', text: buildDiagnosisReport(limit) }] },
      });
      return;
    }

    writeJson({
      jsonrpc: '2.0',
      id: msg.id,
      result: {
        content: [{ type: 'text', text: '未知的代理层工具：' + name }],
        isError: true,
      },
    });
  }

  /**
   * 生成诊断报告。
   *
   * 目标是让 AI 读完就能判断问题出在哪，并把结论用自然语言转述给用户，
   * 而不是把一坨技术细节甩给用户让他自己去跑命令。
   */
  function buildDiagnosisReport(limit) {
    const lines = [];
    const stateText =
      runtimeState === 'ready' && childReady
        ? '已就绪'
        : runtimeState === 'failed'
          ? '失败'
          : '正在准备';

    lines.push('## 运行时状态');
    lines.push(`状态：${stateText}`);
    if (runtimeState === 'failed' && runtimeError) {
      lines.push(`错误：${runtimeError}`);
    }
    if (childReady) {
      lines.push('Python 侧已完成握手，可以正常接受请求。');
    }

    lines.push('');
    lines.push('## 运行环境');
    lines.push(`Node：${process.version}（${process.platform}/${process.arch}）`);
    lines.push(`启动器版本：${opts.version || '未知'}`);
    lines.push(`工作目录：${cwd}`);
    lines.push(
      `环境变量：ZWCAD_MCP_PYTHON=${process.env.ZWCAD_MCP_PYTHON || '（未设置）'}，` +
        `ZWCAD_MCP_PIP_INDEX=${process.env.ZWCAD_MCP_PIP_INDEX || '（未设置）'}`
    );

    lines.push('');
    lines.push('## 错误记录');
    const entries = readRecentErrors(limit);
    if (entries.length === 0) {
      lines.push('无错误记录。若问题仍存在，可能是尚在安装中，或故障未落到日志点。');
    } else {
      lines.push(`日志文件：${logFilePath()}`);
      lines.push('');
      for (const e of entries) {
        lines.push(`- [${e.time}] **${e.event}**`);
        if (e.detail) {
          lines.push('  ' + e.detail.replace(/\n/g, '\n  '));
        }
      }
    }

    lines.push('');
    lines.push('## 如何判读');
    lines.push('- `runtime.python.not_found`：本机没有 3.10+ 的 Python。');
    lines.push('  让用户装 Python（建议 3.13），或设 `ZWCAD_MCP_PYTHON` 指向已有解释器。');
    lines.push('- `runtime.install.failed`：装依赖失败。看 detail 里的 `pipIndex` 与 `error`，');
    lines.push('  多为网络问题，让用户设 `ZWCAD_MCP_PIP_INDEX` 为国内镜像后重连。');
    lines.push('- `python.spawn.failed` / `python.exited`：Python 进程起不来或崩溃，');
    lines.push('  看 detail 里的 `stderrTail`（Python 自身的异常堆栈）。');
    lines.push('- 无任何错误记录但仍不可用：多半仍在安装，让其再等或重连。');

    return lines.join('\n');
  }

  /**
   * 读取最近 N 条错误日志，转成易读文本。
   *
   * 读取失败一律返回空数组——诊断工具自己不能因为读不到日志而报错。
   */
  function readRecentErrors(limit) {
    let raw;
    try {
      raw = fs.readFileSync(logFilePath(), 'utf8');
    } catch {
      return [];
    }

    const out = [];
    const all = raw.trim().split('\n').slice(-limit);
    for (const line of all) {
      try {
        const r = JSON.parse(line);
        let detail = '';
        if (r.detail && Object.keys(r.detail).length > 0) {
          try {
            detail = JSON.stringify(r.detail, null, 1);
          } catch {
            detail = '[无法序列化]';
          }
        }
        out.push({ time: r.time, event: r.event, detail });
      } catch {
        /* 跳过损坏行 */
      }
    }
    return out;
  }

  /**
   * 未就绪时的工具调用：返回结构化错误，让 AI 知道该等还是该修环境。
   *
   * 这里故意用 isError 而不是协议级 error，因为按 MCP 规范，
   * 工具执行失败应以 result.isError 形式返回，客户端会展示给模型。
   */
  function handleToolCallBeforeReady(msg) {
    const name = msg.params && msg.params.name ? msg.params.name : '(unknown)';
    const known = tools.some((t) => t.name === name);

    let text;
    if (!known) {
      text =
        `未知工具：${name}。可用工具共 ${tools.length} 个，` +
        `请先调用 tools/list 获取准确清单。`;
    } else if (runtimeState === 'failed') {
      text =
        `ZWCAD MCP 服务的 Python 运行时准备失败，工具「${name}」不可用。\n\n` +
        `错误：${runtimeError}\n\n` +
        `请执行以下命令查看详细诊断：\n` +
        `  npx -y zwcad-mcp --doctor\n\n` +
        `常见原因：未安装 Python 3.10+、网络无法访问 PyPI、磁盘空间不足。\n` +
        `可设置环境变量 ZWCAD_MCP_PYTHON 指定解释器，或 ` +
        `ZWCAD_MCP_PIP_INDEX 指定 pip 镜像源。`;
    } else {
      text =
        `ZWCAD MCP 服务正在首次初始化（准备 Python 运行时并安装依赖，约需 30~60 秒）。\n\n` +
        `工具「${name}」暂不可用，请稍等片刻后重试。\n` +
        `初始化完成后无需重启客户端，直接重新调用即可。\n\n` +
        `若长时间保持此状态，可执行以下命令排查：\n` +
        `  npx -y zwcad-mcp --doctor`;
    }

    writeJson({
      jsonrpc: '2.0',
      id: msg.id,
      result: {
        content: [{ type: 'text', text }],
        isError: true,
      },
    });
  }

  /* ---------------- 优雅退出 ---------------- */

  const shutdown = (sig) => {
    if (child) {
      try {
        child.kill(sig);
      } catch {
        /* 忽略 */
      }
    }
    process.exit(0);
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
}

module.exports = { createProxy, loadToolManifest };
