#!/usr/bin/env python3
"""
一键编译脚本 - 选择图片自动生成带图标的可执行文件
"""

import os
import sys
import subprocess
from pathlib import Path
from tkinter import Tk, filedialog, messagebox
from PIL import Image
import shutil

def select_image_file():
    """让用户选择图片文件"""
    root = Tk()
    root.withdraw()
    root.attributes('-topmost', True)

    # 支持的图片格式
    filetypes = [
        ('图片文件', '*.png *.jpg *.jpeg *.bmp *.gif *.ico'),
        ('PNG文件', '*.png'),
        ('JPEG文件', '*.jpg *.jpeg'),
        ('BMP文件', '*.bmp'),
        ('GIF文件', '*.gif'),
        ('ICO文件', '*.ico'),
        ('所有文件', '*.*')
    ]

    image_path = filedialog.askopenfilename(
        title="选择图标图片",
        filetypes=filetypes
    )

    root.destroy()
    return image_path

def convert_to_ico(input_path, output_path):
    """将图片转换为ICO格式"""
    try:
        print(f"正在转换图片: {input_path}")

        # 打开原始图片
        with Image.open(input_path) as img:
            # 转换为RGBA模式（支持透明度）
            if img.mode != 'RGBA':
                img = img.convert('RGBA')

            # 创建不同尺寸的图标
            sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
            icons = []

            for size in sizes:
                # 缩放图片
                resized = img.resize(size, Image.LANCZOS)
                icons.append(resized)

            # 保存为.ico格式
            icons[0].save(
                output_path,
                format='ICO',
                sizes=sizes,
                append_images=icons[1:]
            )

        print(f"✅ 图标转换成功: {output_path}")
        return True

    except Exception as e:
        print(f"❌ 图标转换失败: {str(e)}")
        messagebox.showerror("错误", f"图标转换失败:\n{str(e)}")
        return False

def run_pyinstaller(icon_path):
    """运行PyInstaller编译"""
    try:
        print("正在编译可执行文件...")

        # 直接使用pyinstaller.exe的完整路径
        pyinstaller_path = r"E:\Scripts\pyinstaller.exe"
        cmd = [
            pyinstaller_path,
            '--onefile',
            '--windowed',
            f'--icon={icon_path}',
            '--name=image_replacer',
            'image_replacer.py'
        ]

        print(f"执行命令: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')

        if result.returncode != 0:
            print("❌ 编译失败")
            print("错误信息:")
            print(result.stderr)
            messagebox.showerror("编译失败", f"PyInstaller编译失败:\n{result.stderr}")
            return False

        print("✅ 编译成功")

        # 重命名文件
        exe_path = Path("dist/image_replacer.exe.notanexecutable")
        final_exe_path = Path("dist/image_replacer.exe")

        if exe_path.exists():
            exe_path.rename(final_exe_path)
            print(f"✅ 可执行文件已生成: {final_exe_path}")
        elif final_exe_path.exists():
            print(f"✅ 可执行文件已生成: {final_exe_path}")

        return True

    except Exception as e:
        print(f"❌ 编译过程出错: {str(e)}")
        messagebox.showerror("错误", f"编译过程出错:\n{str(e)}")
        return False

def create_release_package():
    """创建发布包"""
    try:
        print("正在创建发布包...")

        # 确保发布包目录存在
        release_dir = Path("发布包")
        release_dir.mkdir(exist_ok=True)

        # 复制文件
        exe_src = Path("dist/image_replacer.exe")
        exe_dst = release_dir / "image_replacer.exe"

        if exe_src.exists():
            shutil.copy2(exe_src, exe_dst)
            print(f"✅ 复制可执行文件: {exe_dst}")

        # 复制其他必要文件
        files_to_copy = [
            ("run_image_replacer.bat", "发布包/run_image_replacer.bat"),
            ("README.md", "发布包/README.txt"),
            ("使用说明.txt", "发布包/使用说明.txt")
        ]

        for src, dst in files_to_copy:
            if Path(src).exists():
                shutil.copy2(src, dst)
                print(f"✅ 复制文件: {dst}")

        print("✅ 发布包创建成功")
        return True

    except Exception as e:
        print(f"❌ 创建发布包失败: {str(e)}")
        messagebox.showerror("错误", f"创建发布包失败:\n{str(e)}")
        return False

def check_requirements():
    """检查必要的依赖"""
    try:
        import PyInstaller
        print("✅ PyInstaller 已安装")
    except ImportError:
        print("❌ PyInstaller 未安装，正在安装...")
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'], check=True)
            print("✅ PyInstaller 安装成功")
        except subprocess.CalledProcessError:
            print("❌ PyInstaller 安装失败")
            messagebox.showerror("错误", "无法安装 PyInstaller，请手动安装:\npip install pyinstaller")
            return False

    try:
        from PIL import Image
        print("✅ PIL/Pillow 已安装")
    except ImportError:
        print("❌ PIL/Pillow 未安装，正在安装...")
        try:
            subprocess.run([sys.executable, '-m', 'pip', 'install', 'pillow'], check=True)
            print("✅ PIL/Pillow 安装成功")
        except subprocess.CalledProcessError:
            print("❌ PIL/Pillow 安装失败")
            messagebox.showerror("错误", "无法安装 Pillow，请手动安装:\npip install pillow")
            return False

    return True

def main():
    """主函数"""
    print("=" * 60)
    print("   图片批量替换工具 - 一键编译脚本")
    print("=" * 60)
    print()

    # 检查依赖
    if not check_requirements():
        return

    # 让用户选择图片
    print("请选择要用作图标的图片文件...")
    image_path = select_image_file()

    if not image_path:
        print("❌ 未选择图片文件")
        return

    print(f"已选择图片: {image_path}")

    # 检查图片格式
    image_ext = Path(image_path).suffix.lower()
    if image_ext not in ['.png', '.jpg', '.jpeg', '.bmp', '.gif', '.ico']:
        messagebox.showerror("错误", f"不支持的图片格式: {image_ext}\n\n支持的格式: PNG, JPG, BMP, GIF, ICO")
        return

    # 清理旧文件
    print("\n正在清理旧文件...")
    for item in ["build", "dist", "image_replacer.spec"]:
        path = Path(item)
        if path.exists():
            if path.is_file():
                path.unlink()
            else:
                shutil.rmtree(path)
    print("✅ 清理完成")

    # 转换图标
    icon_path = "temp_icon.ico"
    if not convert_to_ico(image_path, icon_path):
        return

    # 编译exe
    if not run_pyinstaller(icon_path):
        return

    # 创建发布包
    if not create_release_package():
        return

    # 清理临时文件
    try:
        Path(icon_path).unlink()
        print("✅ 临时文件已清理")
    except:
        pass

    print("\n" + "=" * 60)
    print("🎉 一键编译完成！")
    print("=" * 60)
    print("\n生成的文件:")
    print("  - dist/image_replacer.exe (主程序)")
    print("  - 发布包/ (完整的发布包)")
    print("\n使用方法:")
    print("  1. 双击 发布包/run_image_replacer.bat 启动程序")
    print("  2. 或直接双击 发布包/image_replacer.exe")
    print("\n图标已设置为你选择的图片！")

    # 显示成功消息
    messagebox.showinfo("编译成功",
                       "一键编译完成！\n\n"
                       "生成的文件:\n"
                       "• dist/image_replacer.exe (主程序)\n"
                       "• 发布包/ (完整的发布包)\n\n"
                       "图标已设置为你选择的图片！")

if __name__ == "__main__":
    main()
