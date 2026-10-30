/**
 * 验证 zwcad_launcher_diagnose 能返回有用信息。
 */
'use strict';

const { spawn } = require('child_process');
const path = require('path');

const BRIDGE = process.argv[2] || path.join(__dirname, '..', 'bridge.js');
const child = spawn('node', [BRIDGE], {
  env: { ...process.env, PYTHONUTF8: '1' },
  stdio: ['pipe', 'pipe', 'pipe'],
});

let buf = '';
function send(o) {
  child.stdin.write(JSON.stringify(o) + '\n');
}

child.stderr.on('data', () => {});

child.stdout.on('data', (d) => {
  buf += d.toString('utf8');
  let i;
  while ((i = buf.indexOf('\n')) !== -1) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line.startsWith('{')) continue;
    let o;
    try {
      o = JSON.parse(line);
    } catch {
      continue;
    }

    if (o.id === 1) {
      send({ jsonrpc: '2.0', method: 'notifications/initialized' });
      send({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
    } else if (o.id === 2) {
      const names = o.result.tools.map((t) => t.name);
      console.log('工具总数:', names.length);
      console.log('含 launcher_diagnose:', names.includes('zwcad_launcher_diagnose'));
      console.log('含 wait_ready:', names.includes('zwcad_wait_ready'));
      console.log('');
      send({
        jsonrpc: '2.0',
        id: 3,
        method: 'tools/call',
        params: { name: 'zwcad_launcher_diagnose', arguments: { limit: 5 } },
      });
    } else if (o.id === 3) {
      console.log('=== 诊断报告 ===');
      console.log(o.result.content[0].text);
      child.kill();
      process.exit(0);
    }
  }
});

send({
  jsonrpc: '2.0',
  id: 1,
  method: 'initialize',
  params: {
    protocolVersion: '2024-11-05',
    capabilities: {},
    clientInfo: { name: 'diag', version: '1' },
  },
});

setTimeout(() => {
  console.log('超时');
  child.kill();
  process.exit(1);
}, 60000);
