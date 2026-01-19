#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
诊断脚本：检查Python和PyInstaller的架构
用于排查4.2GB exe文件大小限制问题
"""

import sys
import platform
import struct

print("=" * 60)
print("Python 架构诊断")
print("=" * 60)

# 检查Python架构
print(f"\n1. Python版本: {sys.version}")
print(f"2. Python架构: {platform.architecture()[0]}")
print(f"3. 平台信息: {platform.platform()}")
print(f"4. 机器类型: {platform.machine()}")

# 检查sys.maxsize（判断32位还是64位）
if sys.maxsize > 2**32:
    print(f"5. sys.maxsize: {sys.maxsize} (64位)")
else:
    print(f"5. sys.maxsize: {sys.maxsize} (32位 - 这是问题所在！)")

# 检查struct大小
print(f"6. 指针大小: {struct.calcsize('P') * 8} 位")

# 检查PyInstaller
try:
    import PyInstaller
    print(f"\n7. PyInstaller版本: {PyInstaller.__version__}")
    
    # 检查bootloader路径（适配PyInstaller 6.x）
    try:
        from PyInstaller.building.build_main import EXE
        import PyInstaller.utils.win32.versioninfo
        # 尝试查找bootloader目录
        import os
        pyinstaller_path = os.path.dirname(PyInstaller.__file__)
        bootloader_dir = os.path.join(pyinstaller_path, 'bootloader')
        
        if os.path.exists(bootloader_dir):
            # 查找Windows bootloader目录
            for item in os.listdir(bootloader_dir):
                if 'Windows' in item:
                    bootloader_subdir = os.path.join(bootloader_dir, item)
                    if os.path.isdir(bootloader_subdir):
                        print(f"8. Bootloader目录: {bootloader_subdir}")
                        if '64bit' in item or '64' in item or 'x86_64' in item:
                            print("   ✓ Bootloader是64位的")
                        elif '32bit' in item or '32' in item or 'x86' in item:
                            print("   ✗ Bootloader是32位的 - 这会导致4.2GB限制！")
                        else:
                            print("   ? 无法确定bootloader架构")
                        break
            else:
                print("8. 无法找到Windows bootloader目录")
        else:
            print("8. 无法找到bootloader目录")
    except Exception as e:
        print(f"8. 检查bootloader时出错: {e}")
        print("   (这可能是PyInstaller版本差异导致的)")
        
except ImportError:
    print("\n7. PyInstaller未安装")
except Exception as e:
    print(f"\n7. 检查PyInstaller时出错: {e}")

print("\n" + "=" * 60)
print("诊断结果:")
print("=" * 60)

if sys.maxsize <= 2**32:
    print("⚠️  警告: 您使用的是32位Python！")
    print("   这会导致生成的exe文件有4.2GB的大小限制。")
    print("   解决方案: 请安装64位Python并重新安装所有依赖。")
else:
    print("✓ Python是64位的")

print("\n建议:")
print("1. 确保使用64位Python")
print("2. 在ScreenTranslator.spec中设置 target_arch='x86_64'")
print("3. 重新构建项目: python -m PyInstaller ScreenTranslator.spec")

