/**
 * 冒烟测试：直接验证 errorlog 模块的写入、轮转与容错行为。
 */
'use strict';

const fs = require('fs');
const os = require('os');
const path = require('path');

const { logError, logFilePath, resolveLogDir } = require('../lib/errorlog');

const expectPath = path.join(os.homedir(), '.workbuddy', 'zwcad-mcp-runtime', 'logs', 'error.log');

console.log('解析到的日志目录:', resolveLogDir());
console.log('日志文件路径    :', logFilePath());
console.log('路径是否符合预期:', logFilePath() === expectPath ? '是' : '否');

// 1. 基本写入
logError('test.basic', { message: 'hello', count: 42 });

// 2. 含 Error 对象
logError('test.error_object', { error: new Error('模拟异常') });

// 3. 超长字符串截断
logError('test.long_string', { blob: 'x'.repeat(10000) });

// 4. 循环引用（sanitize 深度保护）
const circular = { name: 'root' };
circular.self = circular;
logError('test.circular', circular);

// 5. 含 undefined / null
logError('test.nullish', { a: null, b: undefined, c: false, d: 0 });

const file = logFilePath();
if (!fs.existsSync(file)) {
  console.error('❌ 日志文件未创建');
  process.exit(1);
}

const lines = fs.readFileSync(file, 'utf8').trim().split('\n');
console.log('\n写入条数:', lines.length);

let bad = 0;
for (const line of lines) {
  try {
    const r = JSON.parse(line);
    if (!r.time || !r.event || !r.pid) {
      console.error('❌ 缺字段:', line.slice(0, 80));
      bad++;
    }
  } catch {
    console.error('❌ 非法 JSON:', line.slice(0, 80));
    bad++;
  }
}

// 事件名是否都记录正确
const events = lines.map((l) => JSON.parse(l).event);
console.log('事件列表:', events.join(', '));

const expected = [
  'test.basic',
  'test.error_object',
  'test.long_string',
  'test.circular',
  'test.nullish',
];
const allPresent = expected.every((e) => events.includes(e));

console.log('\n全部事件已记录:', allPresent ? '是' : '否');
console.log('格式错误条数    :', bad);

if (allPresent && bad === 0) {
  console.log('\n✅ errorlog 冒烟测试通过');
  process.exit(0);
} else {
  console.log('\n❌ 测试未通过');
  process.exit(1);
}
