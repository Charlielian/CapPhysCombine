# -*- mode: python ; coding: utf-8 -*-

# PyInstaller 入口与运行期资源清单。app/static 以及 GeoJSON 目录必须一并打包，
# 否则 exe 启动后虽然能导入 Python 模块，却无法提供页面或完成空间查询。
block_cipher = None

a = Analysis(
    ['CapPhysCombine.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('app', 'app'),
        ('static', 'static'),
        ('区域', '区域'),
        ('网格', '网格'),
        ('路测网格', '路测网格'),
        ('乡镇', '乡镇'),
    ],
    hiddenimports=[
        'duckdb',
        'fastapi',
        'uvicorn',
        'python_multipart',
        'openpyxl',
        'pandas',
        'numpy',
        'yaml',
        'loguru',
        'pyarrow',
        'python_calamine',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='CapPhysCombine',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='CapPhysCombine',
)
