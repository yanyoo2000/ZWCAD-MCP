"""
从运行中的 MCP 服务导出工具清单，供 Node 启动器在 Python 未就绪时使用。

Python 侧工具增删后必须重新执行本脚本，否则 AI 看到的清单会和实际不符。
CI 里会比对两者是否一致。
"""
import json
import os
import subprocess
import sys
import time

# Windows 控制台默认 GBK，输出中文会抛 UnicodeEncodeError 导致非零退出码。
# 本脚本在 CI 的 Windows runner 上跑，必须强制 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(ROOT)
OUT = os.path.join(PROJECT, 'tools.json')

env = dict(os.environ)
env['PYTHONUTF8'] = '1'
env['PYTHONPATH'] = os.path.join(PROJECT, 'runtime')

# 解释器选择顺序：
# 1. 已装好依赖的本地运行时（开发机最快）
# 2. 环境变量指定
# 3. 当前解释器（CI 里需自行 pip install，见 workflow）
py = os.path.join(os.environ.get('USERPROFILE', ''), '.workbuddy',
                  'zwcad-mcp-runtime', 'venv', 'Scripts', 'python.exe')
if not os.path.exists(py):
    py = os.environ.get('ZWCAD_MCP_PYTHON') or sys.executable

msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05", "capabilities": {},
        "clientInfo": {"name": "exporter", "version": "1"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]

proc = subprocess.Popen([py, '-m', 'zwcad2d'],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, env=env)

# 必须逐条发、按响应推进，不能一次性灌入整个 stdin 后立刻 close。
# 一次性 close 会让服务端在 initialize 之后就发现 stdin 结束而退出，
# tools/list 根本来不及处理——这曾导致本脚本首次跑通、再次执行就失败。
tools = None
try:
    def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        proc.stdin.flush()

    def read_until(target_id, timeout=180):
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                return None
            text = line.strip().decode('utf-8', 'replace')
            if not text.startswith('{'):
                continue
            try:
                o = json.loads(text)
            except Exception:
                continue
            if o.get('id') == target_id:
                return o
        return None

    send(msgs[0])
    if read_until(1) is None:
        print('initialize 无响应')
        sys.exit(1)

    send(msgs[1])
    send(msgs[2])
    resp = read_until(2)

    if resp and resp.get('result', {}).get('tools'):
        tools = resp['result']['tools']
finally:
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass

if tools:
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(tools, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f'导出 {len(tools)} 个工具 -> tools.json')
    print(f'文件大小: {os.path.getsize(OUT)} 字节')
    for t in tools:
        print('  ', t['name'])
    sys.exit(0)

print('未拿到 tools/list 响应')
sys.exit(1)
