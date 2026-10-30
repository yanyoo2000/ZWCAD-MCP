/**
 * 轮转测试：写入超过 1MB 后，应生成 .1 备份并重置主文件。
 */
'use strict';

const fs = require('fs');
const path = require('path');
const { logError, logFilePath, resolveLogDir } = require('../lib/errorlog');

const file = logFilePath();
const dir = resolveLogDir();

// 清掉历史，从干净状态开始
for (const f of fs.readdirSync(dir).filter((x) => x.startsWith('error.log'))) {
  fs.rmSync(path.join(dir, f), { force: true });
}

console.log('起始状态: 无日志');

// 每条约 1KB，写 1200 条 => 约 1.2MB，必然触发轮转
const payload = 'y'.repeat(900);
const N = 1200;
for (let i = 0; i < N; i++) {
  logError('test.rotate', { seq: i, payload });
}

const files = fs.readdirSync(dir).filter((x) => x.startsWith('error.log')).sort();
console.log('\n轮转后的文件:');
for (const f of files) {
  const st = fs.statSync(path.join(dir, f));
  console.log(`  ${f}  ${(st.size / 1024).toFixed(1)} KB`);
}

const mainSize = fs.statSync(file).size;
const hasBackup = files.some((f) => f === 'error.log.1');

console.log('\n主文件大小      :', (mainSize / 1024).toFixed(1), 'KB（应 < 1024）');
console.log('存在 .1 备份    :', hasBackup ? '是' : '否');
console.log('总文件数        :', files.length, '（应 <= 4）');

const ok = mainSize <= 1024 * 1024 && hasBackup && files.length <= 4;
console.log('\n' + (ok ? '✅ 轮转机制正常' : '❌ 轮转异常'));
process.exit(ok ? 0 : 1);
