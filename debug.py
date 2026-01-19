#!/usr/bin/env python3
"""
调试模式快速启动脚本（跨平台）
启用调试模式后，OCR处理过程中的图像会保存到 "调试模式图像" 目录
"""

import os
import sys
from pathlib import Path

def main():
    """启动调试模式"""
    print("=" * 50)
    print("屏幕翻译工具 - 调试模式启动")
    print("=" * 50)
    print()
    
    # 设置调试模式环境变量
    os.environ['SCREEN_TRANSLATOR_DEBUG'] = '1'
    
    # 获取脚本所在目录
    script_dir = Path(__file__).parent.resolve()
    debug_image_dir = script_dir / "调试模式图像"
    
    # 显示调试信息
    print(f"[调试模式] 已启用")
    print(f"[调试信息] OCR图像将保存到: {debug_image_dir}")
    print(f"[工作目录] {script_dir}")
    print()
    
    # 切换到脚本所在目录
    os.chdir(script_dir)
    
    # 添加项目根目录到Python路径
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    
    # 启动主程序
    print("正在启动程序...")
    print()
    
    try:
        # 导入并运行主程序
        from main import ScreenTranslatorApp
        
        app = ScreenTranslatorApp()
        app.run()
        
    except KeyboardInterrupt:
        print("\n\n程序被用户中断")
        sys.exit(0)
    except Exception as e:
        print("\n" + "=" * 50)
        print("程序异常退出")
        print("=" * 50)
        print(f"错误信息: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()

