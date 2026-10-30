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
 * 各平台的构建规则。
 *
 * 目录约定：`connector/<platform>/<connector-name>/` 放连接器资源。
 * 一个平台下可以有多个连接器（例如未来新增 zwcad-mech-mcp），
 * 构建时逐个处理，产物输出到 `dist/<platform>/<connector-name>/`。
 *
 * exclude：不进入分发包的文件。开发用的 README 只留在仓库里，
 * 不应混进要提交给平台审核的产物中（审核只认规范列出的那几个文件）。
 */
const PLATFORMS = {
  workbuddy: {
    required: ['connector-meta.json', 'mcp.json', 'icon.png'],
    optional: ['icon.svg', 'skills/SKILL.md'],
    exclude: ['README.md'],
  },
  cursor: {
    // Cursor 不需要打包，用户手动把 mcp.json 内容贴进 .cursor/mcp.json
    required: ['mcp.json'],
    optional: ['README.md'],
    exclude: [],
  },
  claude: {
    // Claude Desktop 同理，贴进 claude_desktop_config.json
    required: ['mcp.json'],
    optional: ['README.md'],
    exclude: [],
  },
};

/**
 * 列出某个平台下的所有连接器目录。
 *
 * 只认含必需文件的子目录，其余（比如平台级 README.md）自然被忽略。
 */
function listConnectors(platform) {
  const base = path.join(CONNECTOR_DIR, platform);
  let entries;
  try {
    entries = fs.readdirSync(base, { withFileTypes: true });
  } catch {
    return [];
  }
  return entries
    .filter((e) => e.isDirectory())
    .map((e) => e.name)
    .sort();
}

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

/**
 * 校验单个连接器目录是否齐备。
 *
 * @param {string} platform 平台名，如 workbuddy
 * @param {string} connector 连接器名，如 zwcad-mcp
 */
function validateConnector(platform, connector, spec) {
  const rel = path.join(platform, connector);
  const dir = path.join(CONNECTOR_DIR, rel);
  if (!fs.existsSync(dir)) return { ready: false, reason: '目录不存在' };

  const missing = spec.required.filter((f) => !exists(path.join(rel, f)));
  if (missing.length > 0) {
    return { ready: false, reason: '缺少必需文件: ' + missing.join(', ') };
  }
  return {
    ready: true,
    missingOptional: spec.optional.filter((f) => !exists(path.join(rel, f))),
  };
}

function build(checkOnly) {
  const targets = process.argv.slice(2).filter((a) => !a.startsWith('-'));
  const platforms = targets.length > 0 ? targets : Object.keys(PLATFORMS);

  let failed = 0;
  let built = 0;
  let skipped = 0;

  for (const platform of platforms) {
    const spec = PLATFORMS[platform];
    if (!spec) {
      console.error(
        `[build-connector] 未知平台: ${platform}（可选: ${Object.keys(PLATFORMS).join(', ')}）`
      );
      failed++;
      continue;
    }

    const connectors = listConnectors(platform);
    if (connectors.length === 0) {
      console.warn(`[build-connector] ${platform} 下没有连接器目录，跳过`);
      if (platform === 'workbuddy') failed++;
      continue;
    }

    for (const connector of connectors) {
      const label = `${platform}/${connector}`;
      const result = validateConnector(platform, connector, spec);

      if (!result.ready) {
        console.warn(`[build-connector] 跳过 ${label}：${result.reason}`);
        skipped++;
        if (platform === 'workbuddy') failed++;
        continue;
      }

      if (result.missingOptional && result.missingOptional.length > 0) {
        console.warn(`[build-connector] ${label} 可选文件缺失: ${result.missingOptional.join(', ')}`);
      }

      if (checkOnly) {
        console.log(`[build-connector] ${label} 结构校验通过`);
        built++;
        continue;
      }

      const srcDir = path.join(CONNECTOR_DIR, platform, connector);
      const outDir = path.join(DIST_DIR, platform, connector);
      fs.rmSync(outDir, { recursive: true, force: true });
      copyRecursive(srcDir, outDir, spec.exclude || [], '');
      const count = countFiles(outDir);
      console.log(`[build-connector] ${label} → dist/${platform}/${connector}/（${count} 个文件）`);
      built++;
    }
  }

  if (failed > 0) {
    console.error(`[build-connector] ${failed} 个连接器构建失败`);
    process.exitCode = 1;
  } else if (!checkOnly) {
    console.log(`[build-connector] 完成：${built} 个连接器，产物在 dist/<platform>/<connector>/ 下`);
  } else {
    console.log(`[build-connector] 校验完成：${built} 个连接器通过`);
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
