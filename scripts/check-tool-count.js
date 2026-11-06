#!/usr/bin/env node
/**
 * 校验文档与注释里声明的工具数，是否与代码实际一致。
 *
 * 为什么需要它
 * ------------
 * 「工具数」这个事实散落在 README、README-pypi、CONTRIBUTING、bridge.js
 * 注释和 GitHub 仓库描述里。任何一处改了没同步，就会互相矛盾。
 *
 * 实际发生过：
 *   - GitHub 描述写 39（0.3.0 之前的旧数字）
 *   - README-pypi 写「共计 41 个」，但明细 27+11+2 只有 40
 *   - bridge.js 注释写 39
 *
 * 靠人记必然漂移，所以让脚本从代码里算出权威值来比对。
 *
 * 两个口径
 * --------
 * 产品确实有两种形态，不是笔误：
 *   python  直接跑 Python（PyPI 版、python -m zwcad2d）→ tools.json 里的数量
 *   user    经 bridge.js 启动（npm、离线包）→ python 数量 + 代理层注入的数量
 *   因为启动器会额外注入 zwcad_wait_ready。
 *
 * 用法
 * ----
 *   node scripts/check-tool-count.js          # 校验，不一致则退出码 1
 *   node scripts/check-tool-count.js --fix    # 自动修正数字
 */

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const FIX = process.argv.includes('--fix');

/* ------------------------------------------------------------------ *
 * 权威值：从代码里算，不手写
 * ------------------------------------------------------------------ */

/** Python 侧工具数 = tools.json 的条目数 */
function pythonToolCount() {
  const tools = JSON.parse(fs.readFileSync(path.join(ROOT, 'tools.json'), 'utf8'));
  if (!Array.isArray(tools)) throw new Error('tools.json 不是数组');
  return tools.length;
}

/**
 * 代理层注入的工具数。
 *
 * 从 lib/proxy.js 源码里数 PROXY_TOOLS 数组的元素：
 * 该数组的元素都是 `{ name: 'zwcad_xxx', ... }`，所以数 `name: 'zwcad_` 出现次数。
 * 不用 require 加载，是为了避免执行 proxy.js 的副作用（它会起 stdin 监听）。
 */
function proxyToolCount() {
  const src = fs.readFileSync(path.join(ROOT, 'lib', 'proxy.js'), 'utf8');
  const m = src.match(/const PROXY_TOOLS\s*=\s*\[([\s\S]*?)\n\];/);
  if (!m) throw new Error('lib/proxy.js 里没找到 PROXY_TOOLS 数组');
  return (m[1].match(/name:\s*'zwcad_/g) || []).length;
}

const PY = pythonToolCount();
const PROXY = proxyToolCount();
const USER = PY + PROXY;

/* ------------------------------------------------------------------ *
 * 声明位置
 *
 * 每处标注它用哪个口径。加新位置时在这里补一行即可。
 * ------------------------------------------------------------------ */

const CLAIMS = [
  {
    file: 'README.md',
    // 例：**共计 41 个工具**：平台 27 个 + 机械 11 个 + 诊断 2 个 + 启动器侧 1 个。
    re: /\*\*共计 (\d+) 个工具\*\*/,
    expect: 'user',
    note: 'npm 用户视角，含启动器注入',
  },
  {
    file: 'README-pypi.md',
    // 例：**共计 40 个工具**：平台 27 个 + 机械 11 个 + 诊断 2 个。
    re: /\*\*共计 (\d+) 个工具\*\*/,
    expect: 'python',
    note: 'PyPI 版直接跑 Python，没有启动器层',
  },
  {
    file: 'CONTRIBUTING.md',
    // 例：│   └── server.py          MCP 服务与 40 个工具的实现
    re: /MCP 服务与 (\d+) 个工具的实现/,
    expect: 'python',
    note: '描述的是 server.py 里的实现数量',
  },
  {
    file: 'bridge.js',
    // 例： * 否则改为从 PyPI 安装 `zwcad-mcp`（它自带同样的 40 个工具）。
    re: /它自带同样的 (\d+) 个工具/,
    expect: 'python',
    note: '指从 PyPI 装到的包，不含本地启动器注入',
  },
];

/** 明细求和校验：README 里列了分类数，加起来必须等于总数 */
const SUM_CHECKS = [
  {
    file: 'README.md',
    // 抓 平台/机械/诊断/启动器侧 四个数字
    parts: [/平台 (\d+) 个/, /机械 (\d+) 个/, /诊断 (\d+) 个/, /启动器侧 (\d+) 个/],
  },
  {
    file: 'README-pypi.md',
    parts: [/平台 (\d+) 个/, /机械 (\d+) 个/, /诊断 (\d+) 个/],
  },
];

/* ------------------------------------------------------------------ *
 * 主流程
 * ------------------------------------------------------------------ */

const problems = [];
/** FIX 模式下已自动修好的项，不算遗留问题 */
const fixed = [];

function expectCount(kind) {
  if (kind === 'python') return PY;
  if (kind === 'user') return USER;
  throw new Error('未知口径: ' + kind);
}

console.log('[tool-count] 权威值: python=' + PY + '，代理层注入=' + PROXY + '，user=' + USER);
console.log('');

// 1. 逐处校验总数
for (const c of CLAIMS) {
  const file = path.join(ROOT, c.file);
  if (!fs.existsSync(file)) {
    problems.push(c.file + '：文件不存在');
    continue;
  }
  const src = fs.readFileSync(file, 'utf8');
  const m = src.match(c.re);
  if (!m) {
    problems.push(c.file + '：没找到声明处（正则不匹配）——若表述改了，请同步更新本脚本');
    continue;
  }
  const declared = parseInt(m[1], 10);
  const want = expectCount(c.expect);
  if (declared === want) {
    console.log('  [OK]   ' + c.file + '：' + declared + '（' + c.expect + ' 口径）');
    continue;
  }

  const msg = c.file + '：写的是 ' + declared + '，应为 ' + want + '（' + c.expect + ' 口径）';

  if (FIX) {
    // 只替换匹配到的那一处数字，避免误伤同文件里别的数字
    const matched = m[0];
    const patched = matched.replace(String(declared), String(want));
    fs.writeFileSync(file, src.replace(matched, patched), 'utf8');
    fixed.push(c.file + '：' + declared + ' -> ' + want);
    console.log('  [FIX]  ' + c.file + '：' + declared + ' -> ' + want);
  } else {
    problems.push(msg);
  }
}

// 2. 明细求和校验
console.log('');
for (const c of SUM_CHECKS) {
  const file = path.join(ROOT, c.file);
  const src = fs.readFileSync(file, 'utf8');
  const nums = [];
  for (const p of c.parts) {
    const m = src.match(p);
    if (!m) {
      problems.push(c.file + '：明细里没找到 ' + p + ' 对应的数字');
      break;
    }
    nums.push(parseInt(m[1], 10));
  }
  if (nums.length !== c.parts.length) continue;

  const total = nums.reduce((a, b) => a + b, 0);

  // 明细之和应当等于该文件声明的总数
  const claim = CLAIMS.find((x) => x.file === c.file);
  let declared;
  if (claim) {
    const m = src.match(claim.re);
    declared = m ? parseInt(m[1], 10) : null;
  }

  if (declared === null) {
    problems.push(c.file + '：无法取得声明的总数，明细求和校验跳过');
    continue;
  }
  if (total !== declared) {
    problems.push(
      c.file + '：明细相加 = ' + nums.join('+') + ' = ' + total + '，但声明的总数是 ' + declared
    );
  } else {
    console.log('  [OK]   ' + c.file + '：明细 ' + nums.join('+') + ' = ' + total + '，与总数一致');
  }
}

/* ------------------------------------------------------------------ *
 * 结果
 * ------------------------------------------------------------------ */

console.log('');
if (problems.length > 0) {
  console.error('[tool-count] 发现 ' + problems.length + ' 个问题：');
  for (const p of problems) console.error('  - ' + p);
  if (!FIX) {
    console.error('');
    console.error('执行 node scripts/check-tool-count.js --fix 可自动修正总数。');
    console.error('明细求和不一致需要手动改分类数字。');
  }
  process.exit(1);
}

if (fixed.length > 0) {
  // FIX 模式下已修好，此时进程状态是干净的，退出码必须是 0 ——
  // 否则调用方（或 CI 里误用 --fix）会以为修复失败。
  console.log('[tool-count] 已自动修正 ' + fixed.length + ' 处，现全部一致');
  process.exit(0);
}

console.log('[tool-count] 全部一致');
