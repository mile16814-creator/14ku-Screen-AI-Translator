#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
检查exe文件的架构（32位还是64位）
"""

import sys
import os
import struct
from pathlib import Path

def check_exe_architecture(exe_path):
    """检查exe文件的架构"""
    exe_path = Path(exe_path)
    
    if not exe_path.exists():
        print(f"错误: 文件不存在: {exe_path}")
        return None
    
    try:
        # 读取PE文件头来检查架构
        with open(exe_path, 'rb') as f:
            # 读取DOS头
            dos_header = f.read(64)
            if dos_header[:2] != b'MZ':
                print(f"错误: 不是有效的PE文件")
                return None
            
            # 跳转到PE头
            pe_offset = struct.unpack('<I', dos_header[60:64])[0]
            f.seek(pe_offset)
            
            # 读取PE签名
            pe_sig = f.read(4)
            if pe_sig != b'PE\x00\x00':
                print(f"错误: 不是有效的PE文件")
                return None
            
            # 读取COFF头
            coff_header = f.read(20)
            machine = struct.unpack('<H', coff_header[0:2])[0]
            
            # 机器类型代码
            # 0x014c = IMAGE_FILE_MACHINE_I386 (32位)
            # 0x8664 = IMAGE_FILE_MACHINE_AMD64 (64位)
            if machine == 0x014c:
                return '32位'
            elif machine == 0x8664:
                return '64位'
            else:
                return f'未知架构 (机器代码: 0x{machine:04x})'
                
    except Exception as e:
        print(f"检查文件时出错: {e}")
        return None

def _resolve_exe_path(arg: str | None):
    if not arg:
        return Path(__file__).parent / 'dist' / 'ScreenTranslator' / 'ScreenTranslator.exe'
    p = Path(arg)
    if p.is_dir():
        return p / 'ScreenTranslator' / 'ScreenTranslator.exe'
    return p


if __name__ == '__main__':
    # 检查指定exe文件（或默认dist目录）
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    dist_exe = _resolve_exe_path(arg)
    
    print("=" * 60)
    print("检查exe文件架构")
    print("=" * 60)
    
    if dist_exe.exists():
        print(f"\n检查文件: {dist_exe}")
        arch = check_exe_architecture(dist_exe)
        if arch:
            print(f"架构: {arch}")
            if arch == '32位':
                print("\n⚠️  警告: exe文件是32位的！")
                print("   这会导致4.2GB的文件大小限制。")
                print("   请确保:")
                print("   1. 使用64位Python")
                print("   2. 在ScreenTranslator.spec中正确配置")
                print("   3. 清理build和dist目录后重新构建")
            elif arch == '64位':
                print("\n✓ exe文件是64位的，应该没有4.2GB限制")
            else:
                print(f"\n? 无法确定架构: {arch}")
    else:
        print(f"\n文件不存在: {dist_exe}")
        print("请先构建项目: python -m PyInstaller ScreenTranslator.spec")
    
    print("\n" + "=" * 60)

