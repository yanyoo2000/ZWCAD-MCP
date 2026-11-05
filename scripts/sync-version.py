"""
同步版本号：以 package.json 为唯一源头，写回 pyproject.toml 与连接器元数据。

为什么需要它
-----------
本仓库同时发布到两个渠道，包名相同（都叫 zwcad-mcp）：
- npm：Node 启动器 + Python 源码，用户 `npx zwcad-mcp`
- PyPI：仅 Python 源码，用户 `uvx zwcad-mcp`

两者版本号必须一致，否则用户会以为某一个版本落后了。
人工维护三处版本极易漏改，所以收敛成一处：改 package.json，
其余由本脚本与 CI 保证同步。

用法
----
    python scripts/sync-version.py           # 同步
    python scripts/sync-version.py --check   # 只检查，不一致则退出码 1
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(ROOT)

PKG_JSON = os.path.join(PROJECT, 'package.json')
PYPROJECT = os.path.join(PROJECT, 'pyproject.toml')
PKG_INIT = os.path.join(PROJECT, 'runtime', 'zwcad2d', '__init__.py')
CONNECTOR_META = os.path.join(
    PROJECT, 'connector', 'workbuddy', 'zwcad-mcp', 'connector-meta.json'
)

check_only = '--check' in sys.argv

# package.json 是唯一源头
with open(PKG_JSON, encoding='utf-8') as f:
    version = json.load(f)['version']

if not re.fullmatch(r'\d+\.\d+\.\d+', version):
    print(f'package.json 的 version 不是 x.y.z 格式: {version}')
    sys.exit(1)

diffs = []

# 1. pyproject.toml：用正则替换 [project] 段里的 version
with open(PYPROJECT, encoding='utf-8') as f:
    py = f.read()

new_py, n = re.subn(
    r'^(\[project\][\s\S]*?^version\s*=\s*)"[^"]*"',
    lambda m: m.group(1) + '"' + version + '"',
    py,
    count=1,
    flags=re.M,
)
if n == 0:
    print('pyproject.toml 中未找到 [project] 段落的 version 字段')
    sys.exit(1)
if new_py != py:
    diffs.append(('pyproject.toml', version))
    if not check_only:
        with open(PYPROJECT, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_py)

# 2. runtime/zwcad2d/__init__.py：__version__ 供 import 时读取
#    这一处最容易漏——它不影响 pip 安装，但会让 `import zwcad2d` 报出旧版本号
if os.path.exists(PKG_INIT):
    with open(PKG_INIT, encoding='utf-8') as f:
        init_src = f.read()
    new_init, n = re.subn(
        r'^__version__\s*=\s*"[^"]*"',
        f'__version__ = "{version}"',
        init_src,
        count=1,
        flags=re.M,
    )
    if n == 0:
        print('runtime/zwcad2d/__init__.py 中未找到 __version__')
        sys.exit(1)
    if new_init != init_src:
        diffs.append(('runtime/zwcad2d/__init__.py', version))
        if not check_only:
            with open(PKG_INIT, 'w', encoding='utf-8', newline='\n') as f:
                f.write(new_init)

# 3. 连接器元数据：数据文件，直接改字典
if os.path.exists(CONNECTOR_META):
    with open(CONNECTOR_META, encoding='utf-8') as f:
        meta = json.load(f)
    if meta.get('version') != version:
        diffs.append(('connector-meta.json', version))
        if not check_only:
            meta['version'] = version
            with open(CONNECTOR_META, 'w', encoding='utf-8', newline='\n') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
                f.write('\n')

if check_only:
    if diffs:
        print('以下文件的版本号与 package.json 不一致：')
        for name, _ in diffs:
            print(f'  {name} 应改为 {version}')
        print('\n执行 python scripts/sync-version.py 修复')
        sys.exit(1)
    print(f'版本号一致：{version}')
    sys.exit(0)

if diffs:
    for name, _ in diffs:
        print(f'已更新 {name} -> {version}')
else:
    print(f'版本号已一致：{version}')
