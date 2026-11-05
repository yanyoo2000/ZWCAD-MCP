"""
校验 Python wheel 结构：包内必须是 zwcad2d/ 而非 runtime/。

为什么要单独成文件：内联 python -c 在 Windows runner 上会因默认编码
（cp1252）无法输出中文而抛异常，表现成「构建失败」——实际构建是成功的。

用法：python scripts/check-wheel.py <wheel 路径>
"""
import sys
import zipfile

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

if len(sys.argv) < 2:
    print('用法: python scripts/check-wheel.py <wheel>')
    sys.exit(2)

wheel = sys.argv[1]
names = zipfile.ZipFile(wheel).namelist()

# 关键文件必须齐全。只查目录前缀不够：hatch 的 include 配置若被改坏，
# 可能保留 zwcad2d/ 却漏掉个别文件，前缀检查仍会通过，但 import 会失败。
REQUIRED_FILES = [
    'zwcad2d/__init__.py',
    'zwcad2d/__main__.py',
    'zwcad2d/server.py',
]

missing = [f for f in REQUIRED_FILES if f not in names]
if missing:
    print('FAIL: wheel 内缺少关键文件: ' + ', '.join(missing))
    sys.exit(1)

if not any(n.startswith('zwcad2d/') for n in names):
    print('FAIL: wheel 内缺少 zwcad2d/ 包目录')
    sys.exit(1)

if any(n.startswith('runtime/') for n in names):
    print('FAIL: wheel 内误含 runtime/ 目录，包路径映射有误')
    sys.exit(1)

if any('__pycache__' in n for n in names):
    print('FAIL: wheel 内混入 __pycache__')
    sys.exit(1)

print('wheel 结构正确: %d 个文件' % len(names))
