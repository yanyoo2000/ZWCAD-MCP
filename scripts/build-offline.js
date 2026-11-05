#!/usr/bin/env node
/**
 * 构建离线安装包。
 *
 * 为什么要单独的离线包
 * --------------------
 * 普通 npm 包依赖「首次运行时联网装依赖」。产线客户的机器：
 *   - 没有 Python 环境
 *   - 不允许 agent 自行安装东西
 *   - 网络隔离
 * 三个条件叠加，npm 版在这类机器上必然失败。
 *
 * 离线包把便携式 Python 和全部依赖一起打进去，解压即用。
 *
 * 产物结构
 * --------
 *   zwcad-mcp-<ver>-win-x64-offline/
 *   ├── bridge.js  lib/  tools.json      启动器（含离线模式判定）
 *   ├── runtime/zwcad2d/                 Python 源码
 *   ├── python/                          便携式 Python
 *   ├── site-packages/                   预装依赖
 *   ├── mcp.json                         客户端配置模板
 *   └── 使用说明.txt
 *
 * 用法
 * ----
 *   node scripts/build-offline.js                  # 用本机 Python 构建
 *   node scripts/build-offline.js --python <path>  # 指定 Python
 *   node scripts/build-offline.js --keep           # 保留中间目录便于调试
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..');
const OUT_DIR = path.join(ROOT, 'dist-offline');
const CACHE_DIR = path.join(ROOT, '.offline-cache');

/** 离线包内需要完整拷贝的文件/目录（相对仓库根） */
const COPY_ITEMS = [
  'bridge.js',
  'lib',
  'tools.json',
  'runtime',
  'package.json',
  'LICENSE',
  'THIRD_PARTY_NOTICES.md',
  'README-pypi.md',
];

/** 依赖清单（与 bridge.js 的 BASE_PACKAGES 保持一致） */
const PACKAGES = [
  'fastmcp>=2.10.3,<3',
  'pyzwcad>=0.1',
  'pyzwcadmech>=0.3.0',
  'comtypes>=1.1',
  'pywin32>=305',
];

/**
 * 部署时要剔除的路径。
 *
 * 只剔除明确无用的开发产物：编译缓存、测试用例、IDE 插件、示例脚本。
 * 刻意不删 win32comext 与 DLLs —— 它们在某些 COM 场景下会被间接引用，
 * 删掉省不了多少体积，却可能造成运行时才暴露的故障。
 */
const PRUNE_PATTERNS = [
  /[\\/]__pycache__[\\/]/,
  /[\\/]Lib[\\/]test[\\/]/,
  /[\\/]Lib[\\/]idlelib[\\/]/,
  /[\\/]Lib[\\/]tkinter[\\/]/,
  /[\\/]Lib[\\/]lib2to3[\\/]/,
  /[\\/]site-packages[\\/]pythonwin[\\/]/,
  /\.chm$/i,
  /\.pyc$/i,
];

function log(...a) {
  console.log('[offline]', ...a);
}

function fail(msg) {
  console.error('[offline] 失败：' + msg);
  process.exit(1);
}

/* ------------------------------------------------------------------ *
 * 参数
 * ------------------------------------------------------------------ */

const argv = process.argv.slice(2);
const keepTemp = argv.includes('--keep');
const pyIdx = argv.indexOf('--python');
const explicitPython = pyIdx >= 0 ? argv[pyIdx + 1] : null;

function pkgVersion() {
  return JSON.parse(fs.readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version;
}

/* ------------------------------------------------------------------ *
 * Python 解析
 * ------------------------------------------------------------------ */

/**
 * 找可用的 Python。
 *
 * 优先用显式指定，其次沿用 bridge.js 的探测顺序（WorkBuddy 自带优先），
 * 最后退回 PATH 上的 python。离线包的 Python 版本决定 site-packages 里的
 * 二进制兼容性，所以必须和打包时一致 —— 记进 bundle-info.json 供排查。
 */
function resolvePython() {
  if (explicitPython) {
    if (!fs.existsSync(explicitPython)) fail('指定的 Python 不存在: ' + explicitPython);
    return explicitPython;
  }

  const candidates = [];

  // WorkBuddy 自带
  if (process.env.LOCALAPPDATA) {
    candidates.push(
      path.join(process.env.LOCALAPPDATA, 'Programs', 'WorkBuddy', 'resources', 'vendor', 'python', 'python.exe')
    );
  }
  // uv / WorkBuddy 托管的版本
  const managed = path.join(os.homedir(), '.workbuddy', 'binaries', 'python', 'versions');
  if (fs.existsSync(managed)) {
    for (const v of fs.readdirSync(managed)) {
      if (v === 'current') continue;
      candidates.push(path.join(managed, v, 'python.exe'));
    }
  }
  // 当前运行时
  candidates.push(path.join(os.homedir(), '.workbuddy', 'zwcad-mcp-runtime', 'venv', 'Scripts', 'python.exe'));
  // PATH
  candidates.push('python', 'python3');

  for (const c of candidates) {
    const r = spawnSync(c, ['-c', 'import sys;print("%d.%d.%d"%sys.version_info[:3])'], {
      encoding: 'utf8',
      timeout: 30000,
    });
    if (r.status === 0 && r.stdout) {
      const ver = r.stdout.trim();
      log('使用 Python: ' + c + ' (' + ver + ')');
      return c;
    }
  }
  fail('未找到可用的 Python，请用 --python 指定');
}

/* ------------------------------------------------------------------ *
 * 构建步骤
 * ------------------------------------------------------------------ */

function copyItem(src, destRoot) {
  const dest = path.join(destRoot, path.basename(src));
  fs.cpSync(src, dest, { recursive: true });
}

function prune(dir) {
  let removed = 0;
  const walk = (d) => {
    let entries;
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      const p = path.join(d, e.name);
      const rel = path.relative(dir, p);
      if (PRUNE_PATTERNS.some((re) => re.test(path.sep + rel))) {
        fs.rmSync(p, { recursive: true, force: true });
        removed++;
        continue;
      }
      if (e.isDirectory()) walk(p);
    }
  };
  walk(dir);
  return removed;
}

function dirSize(dir) {
  let total = 0;
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else {
        try {
          total += fs.statSync(p).size;
        } catch {
          /* ignore */
        }
      }
    }
  };
  walk(dir);
  return total;
}

function mb(bytes) {
  return (bytes / 1024 / 1024).toFixed(1) + ' MB';
}

/** 用 zip 打包。Windows 上优先 PowerShell 的 Compress-Archive。 */
function makeZip(srcDir, zipPath) {
  fs.rmSync(zipPath, { force: true });
  if (process.platform === 'win32') {
    const r = spawnSync(
      'powershell',
      [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `Compress-Archive -Path '${srcDir}\\*' -DestinationPath '${zipPath}' -CompressionLevel Optimal`,
      ],
      { stdio: 'inherit', timeout: 600000 }
    );
    if (r.status === 0 && fs.existsSync(zipPath)) return;
  }
  // 非 Windows 或失败时用 tar 风格兜底（-r 无压缩 zip 兼容性差，这里直接报错）
  fail('zip 打包失败，请确认 PowerShell 可用');
}

/**
 * 生成客户端配置模板。
 *
 * 给两种：推荐用 bridge.js（自动处理 PYTHONPATH），备选用纯 Python
 * （客户环境没有 node 时）。两种都写全路径占位符，不让客户自己拼。
 */
function writeClientConfig(bundleDir, version) {
  const winPath = bundleDir.replace(/\//g, '\\');

  const viaNode = {
    mcpServers: {
      'zwcad-2d': {
        command: 'node',
        args: [winPath + '\\bridge.js'],
        env: { PYTHONUTF8: '1' },
      },
    },
  };

  fs.writeFileSync(
    path.join(bundleDir, 'mcp.json'),
    JSON.stringify(viaNode, null, 2) + '\n',
    'utf8'
  );

  const readme = `ZWCAD MCP 离线包 ${version}
=====================================

本目录自带 Python 与全部依赖，解压即可使用，不需要联网、不需要安装 Python。

一、给 MCP 客户端配置

推荐（需要机器上有 Node.js 18+）：

${JSON.stringify(viaNode, null, 2)}

把上面内容并入客户端的 MCP 配置（Claude Desktop 的 claude_desktop_config.json、
Cursor 的 .cursor/mcp.json、或 WorkBuddy 的连接器配置）。

注意：args 里的路径是本包解压后的实际位置，如果移动了目录，请同步修改。


二、如果机器上没有 Node.js

改用纯 Python 启动。把下面的内容填进客户端配置（PYTHONPATH 必须完整，
少了任何一段都会报 ModuleNotFoundError: No module named 'pywintypes'）：

{
  "mcpServers": {
    "zwcad-2d": {
      "command": "${winPath}\\\\python\\\\python.exe",
      "args": ["-m", "zwcad2d"],
      "env": {
        "PYTHONUTF8": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "${winPath}\\\\site-packages;${winPath}\\\\site-packages\\\\win32\\\\lib;${winPath}\\\\site-packages\\\\win32;${winPath}\\\\site-packages\\\\pywin32_system32;${winPath}\\\\python;${winPath}\\\\runtime"
      }
    }
  }
}


三、使用前检查

1. 中望CAD 或 中望机械CAD 已启动，并至少打开一张 DWG
2. 机器是 Windows 10/11 x64
3. 不需要安装 Python，也不需要 pip install 任何东西


四、出问题时

启动器会把失败事件写到：
  %USERPROFILE%\\.workbuddy\\zwcad-mcp-runtime\\logs\\error.log

把这个文件发给开发团队。它只记录失败事件，不含图纸内容。


五、版本

${version}
`;
  fs.writeFileSync(path.join(bundleDir, '使用说明.txt'), readme, 'utf8');
}

/* ------------------------------------------------------------------ *
 * 主流程
 * ------------------------------------------------------------------ */

async function main() {
  const version = pkgVersion();
  const bundleName = 'zwcad-mcp-' + version + '-win-x64-offline';
  const bundleDir = path.join(OUT_DIR, bundleName);
  const zipPath = path.join(OUT_DIR, bundleName + '.zip');

  if (process.platform !== 'win32') {
    fail('离线包目前只支持 Windows');
  }

  log('版本: ' + version);
  log('产物: ' + bundleDir);

  // 1. 准备目录
  fs.mkdirSync(OUT_DIR, { recursive: true });
  fs.rmSync(bundleDir, { recursive: true, force: true });
  fs.mkdirSync(bundleDir, { recursive: true });

  // 2. 拷文件
  log('复制文件…');
  for (const item of COPY_ITEMS) {
    const src = path.join(ROOT, item);
    if (!fs.existsSync(src)) {
      log('  跳过（不存在）: ' + item);
      continue;
    }
    copyItem(src, bundleDir);
  }

  // 3. 便携 Python
  const py = resolvePython();
  log('复制 Python 运行时…');
  const pyHome = path.dirname(py);
  const pyDest = path.join(bundleDir, 'python');
  fs.cpSync(pyHome, pyDest, { recursive: true });

  // 4. 安装依赖
  log('安装依赖（需要网络，约 30~90 秒）…');
  const spDir = path.join(bundleDir, 'site-packages');
  const installArgs = [
    '-m',
    'pip',
    'install',
    '--disable-pip-version-check',
    '--no-input',
    '--no-warn-script-location',
    '--target',
    spDir,
  ];
  const index = process.env.ZWCAD_MCP_PIP_INDEX;
  if (index) installArgs.push('-i', index);
  installArgs.push(...PACKAGES);

  const ir = spawnSync(path.join(pyDest, 'python.exe'), installArgs, {
    stdio: 'inherit',
    timeout: 900000,
  });
  if (ir.status !== 0) fail('依赖安装失败');

  // 5. 裁剪
  log('裁剪无用文件…');
  const removed = prune(bundleDir);
  log('  删除 ' + removed + ' 项');

  // 6. 生成配置与说明
  writeClientConfig(bundleDir, version);

  // 7. 记录构建信息
  const pyVer = spawnSync(py, ['-c', 'import sys;print("%d.%d.%d"%sys.version_info[:3])'], {
    encoding: 'utf8',
  }).stdout.trim();
  fs.writeFileSync(
    path.join(bundleDir, 'bundle-info.json'),
    JSON.stringify({ version, python: pyVer, builtAt: new Date().toISOString(), platform: 'win-x64' }, null, 2) + '\n',
    'utf8'
  );

  // 8. 打包
  log('压缩…');
  const sizeBefore = dirSize(bundleDir);
  makeZip(bundleDir, zipPath);
  const sizeZip = fs.statSync(zipPath).size;

  log('完成');
  log('  解压后: ' + mb(sizeBefore));
  log('  压缩包: ' + mb(sizeZip));
  log('  路径:   ' + zipPath);

  if (!keepTemp) {
    // 保留解压目录便于人工核查，只提示
    log('  （解压目录保留在 dist-offline/ 下，可手动删除）');
  }
}

main().catch((e) => fail(e && e.stack ? e.stack : String(e)));
