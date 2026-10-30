#!/usr/bin/env node
/**
 * 错误日志：把启动器与 Python 侧的失败事件写到本地文件，便于事后排查。
 *
 * 设计取舍
 * --------
 * 1. **只记失败**，不记成功。成功事件写日志只会淹没真正有用的信息，
 *    而且调用量上来后文件会迅速膨胀。
 * 2. **只记本地，不联网**。图纸内容、文件路径都可能涉密，
 *    用户遇到问题时可以把文件发给我们，由人工判断是否适合上报。
 * 3. **写入绝不抛异常**。日志本身出问题不能影响 MCP 服务运行——
 *    这是日志系统最常见的自伤方式。
 * 4. **自动轮转**。单文件上限 1 MB，超过后转成 `.1`，最多保留 3 个历史文件，
 *    总量控制在 4 MB 以内，不会占满用户磁盘。
 *
 * 存放位置
 * --------
 *   %USERPROFILE%\.workbuddy\zwcad-mcp-runtime\logs\
 *
 * 放在 runtime 目录下，用户执行「删掉 runtime 目录重置」时会一并清理，
 * 不会留下孤儿文件。
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

/** 单文件上限：1 MB */
const MAX_BYTES = 1024 * 1024;
/** 保留的历史文件数 */
const MAX_BACKUPS = 3;

/**
 * 解析日志目录。
 *
 * 允许用环境变量覆盖，便于排查时把日志指到更方便的位置。
 */
function resolveLogDir() {
  if (process.env.ZWCAD_MCP_LOG_DIR) {
    return process.env.ZWCAD_MCP_LOG_DIR;
  }
  return path.join(os.homedir(), '.workbuddy', 'zwcad-mcp-runtime', 'logs');
}

/**
 * 轮转：当前文件超过上限时，依次改名 .1 / .2 / .3，最老的被丢弃。
 *
 * 用 rename 而不是复制，避免大文件复制带来的 IO 抖动。
 */
function rotateIfNeeded(file) {
  let size;
  try {
    size = fs.statSync(file).size;
  } catch {
    return; // 文件不存在，无需轮转
  }
  if (size < MAX_BYTES) return;

  // 从最老的开始往后挪，腾出 .1 的位置
  for (let i = MAX_BACKUPS; i >= 1; i--) {
    const src = i === 1 ? file : file + '.' + (i - 1);
    const dst = file + '.' + i;
    try {
      if (fs.existsSync(src)) fs.renameSync(src, dst);
    } catch {
      /* 轮转失败不影响主流程 */
    }
  }
}

/** 把值安全地转成可 JSON 序列化的形式 */
function sanitize(value, depth = 0) {
  if (value === null || value === undefined) return value;
  if (depth > 4) return '[深度超限]';

  const t = typeof value;
  if (t === 'string') {
    // 抑制超长字符串（例如整段 Python 堆栈）
    return value.length > 4000 ? value.slice(0, 4000) + '…[截断]' : value;
  }
  if (t === 'number' || t === 'boolean') return value;
  if (t === 'function') return '[Function]';

  if (value instanceof Error) {
    return {
      name: value.name,
      message: value.message,
      stack: value.stack ? value.stack.slice(0, 4000) : undefined,
    };
  }

  if (Array.isArray(value)) {
    return value.slice(0, 50).map((v) => sanitize(v, depth + 1));
  }

  if (t === 'object') {
    const out = {};
    let n = 0;
    for (const k of Object.keys(value)) {
      if (n++ >= 50) {
        out['…'] = '[字段过多，已截断]';
        break;
      }
      try {
        out[k] = sanitize(value[k], depth + 1);
      } catch {
        out[k] = '[无法序列化]';
      }
    }
    return out;
  }

  return String(value);
}

/** 进程级上下文，每条日志都会带上 */
const baseContext = {
  pid: process.pid,
  node: process.version,
  platform: process.platform + '/' + process.arch,
};

/**
 * 记录一条错误事件。
 *
 * @param {string} event   事件名，用点号分层，如 `runtime.install.failed`
 * @param {object} detail  事件细节，任意可序列化对象
 */
function logError(event, detail) {
  try {
    const dir = resolveLogDir();
    fs.mkdirSync(dir, { recursive: true });

    const file = path.join(dir, 'error.log');
    rotateIfNeeded(file);

    const record = {
      time: new Date().toISOString(),
      event: String(event),
      ...baseContext,
      detail: sanitize(detail || {}),
    };

    fs.appendFileSync(file, JSON.stringify(record) + '\n', 'utf8');

    // 同时打印一行到 stderr，让开发者在终端也能立刻看到
    process.stderr.write(`[zwcad-mcp][错误] ${record.event}\n`);
  } catch {
    // 日志失败绝不能影响主流程
  }
}

/** 返回当前日志文件路径，供 --doctor 展示 */
function logFilePath() {
  return path.join(resolveLogDir(), 'error.log');
}

module.exports = { logError, logFilePath, resolveLogDir };
