#!/usr/bin/env node
/**
 * 构建各平台的连接器分发包。
 *
 * 背景
 * ----
 * 各平台对「连接器包」的目录要求不同，其中 WorkBuddy 要求**文件必须在包根目录
 * 扁平放置**（connector-meta.json / mcp.json / icon / skills 都在第一层）。
 * 而本仓库为了多平台共存，把它们放在 `connector/<platform>/` 下。
 *
 * 所以发布前需要一次「扁平化」转换，这就是本脚本的职责。
 *
 * 用法
 * ----
 *   node scripts/build-connector.js            # 构建全部平台到 dist/
 *   node scripts/build-connector.js workbuddy  # 只构建指定平台
 *   node scripts/build-connector.js --check    # 只校验源目录结构，不产出
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '..');
const CONNECTOR_DIR = path.join(ROOT, 'connector');
const DIST_DIR = path.join(ROOT, 'dist');

/**
 * 各平台的必需文件。缺失即视为该平台尚未就绪，构建时跳过并提示。
 *
 * exclude：不进入分发包的文件。开发用的 README 只留在仓库里，
 * 不应混进要提交给平台审核的产物中（审核只认规范列出的那几个文件）。
 */
const PLATFORMS = {
  workbuddy: {
    required: ['connector-meta.json', 'mcp.json', 'icon.png'],
    optional: ['icon.svg', 'skills/SKILL.md'],
    exclude: ['README.md'],
    // WorkBuddy 要求扁平结构，源目录本身已扁平（只是多套了一层 platform 目录），
    // 因此只需整体拷贝。
    flatten: true,
  },
  cursor: {
    // Cursor 不需要打包，用户手动把 mcp.json 内容贴进 .cursor/mcp.json
    required: ['mcp.json'],
    optional: ['README.md'],
    exclude: [],
    flatten: true,
  },
  claude: {
    // Claude Desktop 同理，贴进 claude_desktop_config.json
    required: ['mcp.json'],
    optional: ['README.md'],
    exclude: [],
    flatten: true,
  },
};

function copyRecursive(src, dest, exclude, relative) {
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src)) {
      const rel = relative ? relative + '/' + entry : entry;
      copyRecursive(path.join(src, entry), path.join(dest, entry), exclude, rel);
    }
  } else {
    if (relative && exclude.includes(relative)) return;
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    fs.copyFileSync(src, dest);
  }
}

function exists(rel) {
  return fs.existsSync(path.join(CONNECTOR_DIR, rel));
}

/** 校验某个平台的源目录是否齐备，返回缺失项列表 */
function validatePlatform(name, spec) {
  const dir = path.join(CONNECTOR_DIR, name);
  if (!fs.existsSync(dir)) return { ready: false, reason: '目录不存在' };

  const missing = spec.required.filter((f) => !exists(path.join(name, f)));
  if (missing.length > 0) {
    return { ready: false, reason: '缺少必需文件: ' + missing.join(', ') };
  }
  return { ready: true, missingOptional: spec.optional.filter((f) => !exists(path.join(name, f))) };
}

function build(checkOnly) {
  const targets = process.argv.slice(2).filter((a) => !a.startsWith('-'));
  const names = targets.length > 0 ? targets : Object.keys(PLATFORMS);

  let failed = 0;

  for (const name of names) {
    const spec = PLATFORMS[name];
    if (!spec) {
      console.error(`[build-connector] 未知平台: ${name}（可选: ${Object.keys(PLATFORMS).join(', ')}）`);
      failed++;
      continue;
    }

    const srcDir = path.join(CONNECTOR_DIR, name);
    const result = validatePlatform(name, spec);

    if (!result.ready) {
      console.warn(`[build-connector] 跳过 ${name}：${result.reason}`);
      if (spec.required.length > 0 && name === 'workbuddy') failed++;
      continue;
    }

    if (result.missingOptional && result.missingOptional.length > 0) {
      console.warn(`[build-connector] ${name} 可选文件缺失: ${result.missingOptional.join(', ')}`);
    }

    if (checkOnly) {
      console.log(`[build-connector] ${name} 结构校验通过`);
      continue;
    }

    const outDir = path.join(DIST_DIR, name);
    fs.rmSync(outDir, { recursive: true, force: true });
    copyRecursive(srcDir, outDir, spec.exclude || [], '');
    const count = countFiles(outDir);
    console.log(`[build-connector] ${name} → dist/${name}/（${count} 个文件）`);
  }

  if (failed > 0) {
    console.error(`[build-connector] ${failed} 个平台构建失败`);
    process.exitCode = 1;
  } else if (!checkOnly) {
    console.log('[build-connector] 构建完成，产物在 dist/ 下');
  }
}

function countFiles(dir) {
  let n = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) n += countFiles(path.join(dir, entry.name));
    else n++;
  }
  return n;
}

build(process.argv.includes('--check'));
