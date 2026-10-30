#!/usr/bin/env node
/**
 * ZWCAD-2D MCP — Node 启动器（bridge）
 *
 * 背景
 * ----
 * WorkBuddy 等客户端默认只有 Node 运行时，没有 uv/uvx，也无法保证用户机器装了
 * 可用的 Python。而本服务是 Python 实现（依赖 COM，无法在 Node 侧等价重写）。
 *
 * 这个脚本就是两者之间的桥梁：用 Node 做“运行时准备 + 进程托管”，
 * 把真正的 MCP 服务作为子进程拉起，并双向转发 stdio。
 *
 *     MCP 客户端 ──stdio──► bridge.js ──spawn──► python -m zwcad2d
 *
 * 职责
 * ----
 * 1. 定位可用的 Python（优先 WorkBuddy 自带，其次系统 Python）
 * 2. 首次运行时创建隔离 venv 并安装依赖（约 30~60 秒），之后复用
 * 3. 以子进程方式启动 MCP Server，原样转发 stdin/stdout
 *
 * 关键约束
 * --------
 * stdout 是 MCP 协议通道，**任何日志都不能写 stdout**。
 * 本文件内所有输出一律走 stderr。
 *
 * 用法
 * ----
 *   node bridge.js              # 正常启动 MCP 服务（供客户端调用）
 *   node bridge.js --prepare    # 只准备运行时（建 venv + 装依赖），然后退出
 *   node bridge.js --doctor     # 打印环境诊断信息，然后退出
 */

'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');
const crypto = require('crypto');
const { spawn, spawnSync } = require('child_process');

/* ------------------------------------------------------------------ *
 * 常量
 * ------------------------------------------------------------------ */

/** 连接器目录（本文件所在目录） */
const CONNECTOR_DIR = __dirname;

/** 随连接器一起分发的 Python 源码目录（若有，则免去从 PyPI 安装本包） */
const VENDORED_SRC = path.join(CONNECTOR_DIR, 'runtime');

/** 运行时状态目录：虚拟环境与安装标记都放这里，卸载时删掉即可 */
const STATE_DIR = path.join(os.homedir(), '.workbuddy', 'zwcad-mcp-runtime');
const VENV_DIR = path.join(STATE_DIR, 'venv');
const MARKER_FILE = path.join(STATE_DIR, 'installed.json');

/** MCP Server 的 Python 入口模块 */
const ENTRY_MODULE = 'zwcad2d';

/** 第三方依赖（与 pyproject.toml 保持一致） */
const BASE_PACKAGES = [
  'fastmcp>=2.10.3,<3',
  'pyzwcad>=0.1',
  'pyzwcadmech>=0.3.0',
  'comtypes>=1.1',
  'pywin32>=305',
];

/** 依赖自检时关注的模块名（供 --doctor 展示与排障参考） */
const VERIFY_IMPORTS = ['fastmcp', 'pyzwcad', 'pyzwcadmech', 'comtypes', 'pythoncom'];

/** 最低 Python 版本 */
const MIN_PYTHON = [3, 10];

/* ------------------------------------------------------------------ *
 * 日志
 * ------------------------------------------------------------------ */

function log(...parts) {
  process.stderr.write('[zwcad-mcp] ' + parts.join(' ') + '\n');
}

function fatal(err) {
  const msg = err && err.stack ? err.stack : String(err);
  log('启动失败：' + msg);
  process.exit(1);
}

/* ------------------------------------------------------------------ *
 * 通用子进程工具
 * ------------------------------------------------------------------ */

/**
 * 同步执行并捕获 stdout，失败返回 null（用于探测，不抛异常）。
 *
 * 超时给得比较宽松：Windows 上首次启动 python.exe 可能被杀毒软件扫描，
 * 实测出现过同一路径偶发 20 秒超时被误判为「不可用」的情况。
 * 探测失败会直接导致挑不到可用解释器，代价远大于多等几秒。
 */
function tryRun(exe, args, options) {
  try {
    const r = spawnSync(exe, args, {
      encoding: 'utf8',
      timeout: 60000,
      windowsHide: true,
      ...options,
    });
    if (r.error || r.status !== 0) return null;
    return (r.stdout || '').trim();
  } catch {
    return null;
  }
}

/**
 * 流式执行，输出全部转到 stderr（避免污染 stdout 的 MCP 通道）。
 */
function runStreaming(exe, args, label) {
  return new Promise((resolve, reject) => {
    log('执行：' + path.basename(exe) + ' ' + args.join(' '));
    let child;
    try {
      child = spawn(exe, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    } catch (e) {
      reject(new Error(label + ' 无法启动：' + e.message));
      return;
    }
    child.stdout.on('data', (d) => process.stderr.write(d));
    child.stderr.on('data', (d) => process.stderr.write(d));
    child.on('error', reject);
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error(label + ' 失败（退出码 ' + code + '）'));
    });
  });
}

/* ------------------------------------------------------------------ *
 * Python 探测
 * ------------------------------------------------------------------ */

/**
 * 返回 Python 主次版本号，例如 [3, 13]；不可用返回 null。
 *
 * 失败时重试一次：Windows 上 python.exe 首次启动偶尔会异常缓慢
 * （杀毒软件扫描、磁盘繁忙），直接判死会让本可用的解释器被跳过。
 */
function pythonVersion(exe) {
  const probe = () => {
    const out = tryRun(exe, ['-c', 'import sys;print("%d.%d"%sys.version_info[:2])']);
    if (!out) return null;
    const m = /^(\d+)\.(\d+)$/.exec(out);
    return m ? [Number(m[1]), Number(m[2])] : null;
  };

  const first = probe();
  if (first) return first;

  // 一次失败不足以断定不可用，再试一次
  return probe();
}

function versionAtLeast(ver, min) {
  if (!ver) return false;
  if (ver[0] > min[0]) return true;
  if (ver[0] < min[0]) return false;
  return ver[1] >= min[1];
}

/**
 * 按优先级列出候选 Python 解释器。
 *
 * 顺序：显式环境变量 → WorkBuddy 托管 Python → WorkBuddy 安装目录自带 → 系统 PATH
 */
function pythonCandidates() {
  const list = [];
  const add = (p) => {
    if (p && !list.includes(p)) list.push(p);
  };

  if (process.env.ZWCAD_MCP_PYTHON) add(process.env.ZWCAD_MCP_PYTHON);

  // WorkBuddy 托管的 Python：~/.workbuddy/binaries/python/versions/<ver>/python.exe
  const wbPythonRoot = path.join(os.homedir(), '.workbuddy', 'binaries', 'python');
  try {
    const current = fs
      .readFileSync(path.join(wbPythonRoot, 'versions', 'current'), 'utf8')
      .trim();
    if (current) add(path.join(wbPythonRoot, 'versions', current, 'python.exe'));
  } catch {
    /* 没有 current 文件就跳过 */
  }
  try {
    for (const name of fs.readdirSync(path.join(wbPythonRoot, 'versions'))) {
      if (/^\d+\.\d+/.test(name)) add(path.join(wbPythonRoot, 'versions', name, 'python.exe'));
    }
  } catch {
    /* 目录不存在就跳过 */
  }

  // WorkBuddy 安装目录自带的 Python
  const installRoots = [
    process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Programs', 'WorkBuddy'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'WorkBuddy'),
    process.env['ProgramFiles(x86)'] && path.join(process.env['ProgramFiles(x86)'], 'WorkBuddy'),
  ].filter(Boolean);
  for (const root of installRoots) {
    add(path.join(root, 'resources', 'vendor', 'python', 'python.exe'));
  }

  // 系统 Python
  add('python');
  add('python3');

  return list;
}

/**
 * 选出第一个满足最低版本要求的 Python。
 */
function resolvePython() {
  // 显式指定了就尊重用户意图：不可用时报错，而不是静默回退到别的解释器，
  // 否则用户以为自己指定成功了，实际用的是另一个 Python，排查会很困惑。
  const explicit = process.env.ZWCAD_MCP_PYTHON;
  if (explicit) {
    if (!fs.existsSync(explicit)) {
      throw new Error(
        'ZWCAD_MCP_PYTHON 指定的解释器不存在：' + explicit + '。请修正该环境变量或删除它改用自动探测。'
      );
    }
    const ver = pythonVersion(explicit);
    if (!ver) {
      throw new Error('ZWCAD_MCP_PYTHON 指定的解释器无法运行：' + explicit);
    }
    if (!versionAtLeast(ver, MIN_PYTHON)) {
      throw new Error(
        'ZWCAD_MCP_PYTHON 指定的 Python 版本为 ' +
          ver.join('.') +
          '，低于要求的 ' +
          MIN_PYTHON.join('.') +
          '：' +
          explicit
      );
    }
    log('使用 Python ' + ver.join('.') + '（由 ZWCAD_MCP_PYTHON 指定）：' + explicit);
    return { exe: explicit, version: ver };
  }

  const tried = [];
  for (const exe of pythonCandidates()) {
    if (exe !== 'python' && exe !== 'python3' && !fs.existsSync(exe)) continue;
    const ver = pythonVersion(exe);
    if (!ver) {
      tried.push(path.basename(exe) + '（不可用）');
      continue;
    }
    if (!versionAtLeast(ver, MIN_PYTHON)) {
      tried.push(exe + '（' + ver.join('.') + '，版本过低）');
      continue;
    }
    log('使用 Python ' + ver.join('.') + '：' + exe);
    return { exe, version: ver };
  }
  throw new Error(
    '未找到可用的 Python（需要 >= ' +
      MIN_PYTHON.join('.') +
      '）。已尝试：' +
      (tried.join('、') || '无') +
      '。可通过环境变量 ZWCAD_MCP_PYTHON 指定解释器路径。'
  );
}

/* ------------------------------------------------------------------ *
 * 虚拟环境与依赖
 * ------------------------------------------------------------------ */

function venvPythonPath() {
  return process.platform === 'win32'
    ? path.join(VENV_DIR, 'Scripts', 'python.exe')
    : path.join(VENV_DIR, 'bin', 'python');
}

/**
 * 依赖清单 + Python 版本的指纹，用于判断是否需要重装。
 */
function installFingerprint(pythonVersionArr) {
  const h = crypto.createHash('sha256');
  h.update(requiredPackages().join('|'));
  h.update('@');
  h.update(pythonVersionArr ? pythonVersionArr.join('.') : 'unknown');
  return h.digest('hex').slice(0, 16);
}

function readMarker() {
  try {
    return JSON.parse(fs.readFileSync(MARKER_FILE, 'utf8'));
  } catch {
    return null;
  }
}

function writeMarker(fingerprint, pyVer) {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  fs.writeFileSync(
    MARKER_FILE,
    JSON.stringify(
      { fingerprint, python: pyVer ? pyVer.join('.') : null, installedAt: new Date().toISOString() },
      null,
      2
    )
  );
}

/**
 * 轻量健康检查：直接看 venv 里关键包是否还在（毫秒级，不启动 Python）。
 *
 * 为什么不 import 校验：导入 fastmcp 等全部依赖约需 2.5 秒，
 * 而这段时间会直接叠加到每次启动的延迟上。文件级检查足够发现
 * 「用户误删 / 清理软件删了依赖 / 半途失败的安装」这几类真实故障。
 */
function quickHealthCheck(venvPython) {
  const sitePackages = path.join(path.dirname(path.dirname(venvPython)), 'Lib', 'site-packages');
  if (!fs.existsSync(sitePackages)) return false;

  // 这些是缺一不可的关键包（目录名前缀即可判定）
  const critical = ['fastmcp-', 'pyzwcad-', 'pyzwcadmech-', 'comtypes-', 'pywin32-'];
  let entries;
  try {
    entries = fs.readdirSync(sitePackages);
  } catch {
    return false;
  }
  const has = (prefix) =>
    entries.some((e) => e.startsWith(prefix) && e.endsWith('.dist-info'));

  return critical.every(has);
}

function vendoredSrcOrNull() {
  return fs.existsSync(path.join(VENDORED_SRC, ENTRY_MODULE)) ? VENDORED_SRC : null;
}

/**
 * 本次实际需要安装的包清单。
 *
 * 若连接器内随包带了 Python 源码，则通过 PYTHONPATH 引入，无需再装 zwcad-mcp 本体；
 * 否则改为从 PyPI 安装 `zwcad-mcp`（它自带同样的 39 个工具）。
 */
function requiredPackages() {
  return vendoredSrcOrNull() ? BASE_PACKAGES.slice() : BASE_PACKAGES.concat('zwcad-mcp');
}

/**
 * 确保 venv 存在且可用，返回 venv 内的 python 路径。
 */
async function ensureVenv(basePython) {
  const venvPython = venvPythonPath();

  if (fs.existsSync(venvPython)) {
    const ver = pythonVersion(venvPython);
    if (ver) return venvPython;
    log('检测到虚拟环境不可用，重建中…');
    fs.rmSync(VENV_DIR, { recursive: true, force: true });
  }

  fs.mkdirSync(STATE_DIR, { recursive: true });
  log('首次运行，创建隔离虚拟环境：' + VENV_DIR);
  await runStreaming(basePython.exe, ['-m', 'venv', VENV_DIR], '创建虚拟环境');

  if (!fs.existsSync(venvPython)) {
    throw new Error('虚拟环境创建后缺少 python 可执行文件：' + venvPython);
  }
  return venvPython;
}

/**
 * 安装依赖。支持通过 ZWCAD_MCP_PIP_INDEX 指定镜像源。
 */
async function installDeps(venvPython) {
  const args = ['-m', 'pip', 'install', '--disable-pip-version-check', '--no-input'];
  const index = process.env.ZWCAD_MCP_PIP_INDEX;
  if (index) args.push('-i', index);

  // 若连接器内已带源码，则本包本身无需从 PyPI 安装（通过 PYTHONPATH 引入）
  args.push(...requiredPackages());

  log('首次运行，安装依赖（约 30~60 秒，请稍候）…');
  await runStreaming(venvPython, args, '安装依赖');
}

/**
 * 准备运行时：确保 venv + 依赖就绪，返回 venv python 路径。
 */
async function prepareRuntime() {
  const base = resolvePython();
  const fingerprint = installFingerprint(base.version);
  const venvPython = await ensureVenv(base);

  const marker = readMarker();
  const upToDate = marker && marker.fingerprint === fingerprint;

  // 标记有效仍需确认依赖文件还在：用户可能手工删过 venv 里的包
  if (upToDate && quickHealthCheck(venvPython)) {
    return venvPython;
  }

  if (upToDate) {
    log('检测到依赖缺失或损坏，重新安装…');
  }
  await installDeps(venvPython);
  writeMarker(fingerprint, base.version);
  log('依赖安装完成。');
  return venvPython;
}

/* ------------------------------------------------------------------ *
 * 启动（代理模式）
 * ------------------------------------------------------------------ */

/**
 * 以代理模式启动：立即完成 MCP 握手，Python 运行时在后台准备。
 *
 * 这样首次使用（需安装约 90 个 Python 依赖，实测 40~70 秒）也不会
 * 触发客户端的 initialize 超时（通常 30 秒）。
 *
 * 具体协议处理见 lib/proxy.js。
 */
function startWithProxy() {
  const { createProxy } = require('./lib/proxy');
  createProxy({
    cwd: CONNECTOR_DIR,
    version: pkgVersion(),
    childArgs: ['-m', ENTRY_MODULE],
    log,
    ensureRuntime: async () => {
      // 每次都要重新探测：用户可能刚装了 Python 或修好了环境
      const py = await prepareRuntime();
      return py;
    },
  });
}

function pkgVersion() {
  try {
    return JSON.parse(fs.readFileSync(path.join(CONNECTOR_DIR, 'package.json'), 'utf8')).version;
  } catch {
    return '0.0.0';
  }
}

/* ------------------------------------------------------------------ *
 * 诊断
 * ------------------------------------------------------------------ */

function doctor() {
  log('连接器目录：' + CONNECTOR_DIR);
  log('运行时状态目录：' + STATE_DIR);
  log('随包源码：' + (vendoredSrcOrNull() || '（无，将使用已安装的 zwcad-mcp 包）'));
  log('--- Python 候选 ---');
  for (const exe of pythonCandidates()) {
    if (exe !== 'python' && exe !== 'python3' && !fs.existsSync(exe)) {
      log('  [缺失] ' + exe);
      continue;
    }
    const ver = pythonVersion(exe);
    log('  ' + (ver ? '[可用 ' + ver.join('.') + ']' : '[不可用] ') + ' ' + exe);
  }
  const venvPython = venvPythonPath();
  if (fs.existsSync(venvPython)) {
    const ver = pythonVersion(venvPython);
    log('--- 虚拟环境 ---');
    log('  路径：' + venvPython + '（Python ' + (ver ? ver.join('.') : '未知') + '）');
    log('  依赖文件完整：' + (quickHealthCheck(venvPython) ? '是' : '否'));
    const m = readMarker();
    log('  安装标记：' + (m ? JSON.stringify(m) : '无'));

    // 逐个 import 验证，给出具体是哪个依赖坏了（仅诊断模式，慢一些无妨）
    log('  --- 逐项导入验证 ---');
    const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
    const src = vendoredSrcOrNull();
    if (src) env.PYTHONPATH = env.PYTHONPATH ? src + path.delimiter + env.PYTHONPATH : src;
    for (const mod of VERIFY_IMPORTS) {
      const r = spawnSync(venvPython, ['-c', 'import ' + mod], {
        encoding: 'utf8',
        timeout: 60000,
        windowsHide: true,
        env,
      });
      log('    ' + (r.status === 0 ? '[OK]   ' : '[失败] ') + mod);
    }
  } else {
    log('--- 虚拟环境 --- 尚未创建（首次启动时会自动创建）');
  }
}

/* ------------------------------------------------------------------ *
 * 入口
 * ------------------------------------------------------------------ */

async function main() {
  const argv = process.argv.slice(2);

  if (argv.includes('--doctor')) {
    doctor();
    return;
  }

  if (argv.includes('--prepare')) {
    await prepareRuntime();
    log('运行时准备完毕。');
    return;
  }

  // 正常启动：走代理模式，先握手再后台准备运行时
  startWithProxy();
}

main().catch(fatal);
