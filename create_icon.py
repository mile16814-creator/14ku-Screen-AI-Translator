#!/usr/bin/env python3
"""
创建应用程序图标
将JPG图像转换为ICO格式
"""

from PIL import Image
import os
from pathlib import Path

def create_app_icon():
    """创建应用程序图标"""
    # 路径
    assets_dir = Path(__file__).parent / "assets" / "icons"
    jpg_path = assets_dir / "0c9ca942ab6fb4d165c25be3ca60e374.jpg"
    ico_path = assets_dir / "app_icon.ico"
    
    # 检查源文件是否存在
    if not jpg_path.exists():
        print(f"错误: 源图像文件不存在: {jpg_path}")
        return False
    
    try:
        # 打开图像
        img = Image.open(jpg_path)
        
        # 调整大小为常见的图标尺寸
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        
        # 创建图标
        img.save(ico_path, format='ICO', sizes=sizes)
        
        print(f"图标已创建: {ico_path}")
        print(f"原始图像尺寸: {img.size}")
        print(f"图标包含尺寸: {sizes}")
        
        return True
        
    except Exception as e:
        print(f"创建图标时出错: {e}")
        return False

if __name__ == "__main__":
    # 检查PIL是否已安装
    try:
        from PIL import Image
    except ImportError:
        print("错误: Pillow库未安装")
        print("请运行: pip install pillow")
        exit(1)
    
    success = create_app_icon()
    if success:
        print("图标创建成功！")
    else:
        print("图标创建失败。")
