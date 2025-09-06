#!/usr/bin/env python3
"""
图片批量替换工具
遍历文件夹下所有图片，使用命令行界面，用户选择一张图片后将其余图片替换为选中图片
保持原文件名不变
"""

import os
import sys
from pathlib import Path
from PIL import Image, ImageTk
from tkinter import Tk, Label, Button, Frame, Scrollbar, Canvas, messagebox, ttk, StringVar, Entry
from tkinter.filedialog import askdirectory
from tkinter import filedialog


class ImageSelectorGUI:
    """图片选择GUI界面"""

    def __init__(self, image_files, folder_path):
        self.image_files = image_files
        self.folder_path = folder_path
        self.selected_image = None
        self.selected_path = None
        self.thumbnails = []  # 存储缩略图引用

        # 创建主窗口
        self.root = Tk()
        self.root.title("选择要用作模板的图片")
        self.root.geometry("900x700")
        self.root.resizable(True, True)
        self.root.attributes('-topmost', True)  # 窗口置顶
        self.root.lift()  # 提升窗口
        self.root.focus_force()  # 强制获得焦点

        # 创建主框架
        main_frame = Frame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)

        # 标题
        title_label = Label(main_frame, text=f"文件夹: {folder_path}", font=("Arial", 12, "bold"))
        title_label.pack(pady=(0, 10))

        # 初始化搜索状态（必须在其他组件之前）
        self.all_images = self.image_files.copy()  # 保存所有图片
        self.filtered_images = self.image_files.copy()

        # 搜索框
        search_frame = Frame(main_frame)
        search_frame.pack(fill="x", pady=(0, 10))

        search_label = Label(search_frame, text="搜索图片:", font=("Arial", 10))
        search_label.pack(side="left", padx=(0, 5))

        self.search_var = StringVar()
        self.search_var.trace("w", self.on_search_change)

        search_entry = Entry(search_frame, textvariable=self.search_var, font=("Arial", 10), width=30)
        search_entry.pack(side="left", padx=(0, 10))
        search_entry.focus()

        clear_button = Button(search_frame, text="清除", command=self.clear_search, font=("Arial", 9))
        clear_button.pack(side="left")

        self.match_count_label = Label(search_frame, text="", font=("Arial", 9), fg="blue")
        self.match_count_label.pack(side="right")

        # 创建滚动框架
        self.create_scrollable_frame(main_frame)

        # 确认按钮
        button_frame = Frame(main_frame)
        button_frame.pack(fill="x", pady=10)

        self.confirm_button = Button(button_frame, text="确认选择", command=self.confirm_selection,
                                   font=("Arial", 12), bg="#4CAF50", fg="white", height=2, state="disabled")
        self.confirm_button.pack(side="right", padx=(10, 0))

        cancel_button = Button(button_frame, text="取消", command=self.cancel_selection,
                             font=("Arial", 12), bg="#f44336", fg="white", height=2)
        cancel_button.pack(side="right")

        # 状态标签
        self.status_label = Label(button_frame, text="请点击选择一张图片", font=("Arial", 10))
        self.status_label.pack(side="left")

        # 更新匹配数量
        self.update_match_count()

        # 绑定鼠标滚轮
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def create_scrollable_frame(self, parent):
        """创建可滚动的图片网格"""
        # 创建Canvas和滚动条
        self.canvas = Canvas(parent, height=500)
        scrollbar = Scrollbar(parent, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 创建图片网格
        self.create_image_grid(self.scrollable_frame)

    def create_image_grid(self, parent):
        """创建图片网格显示"""
        # 计算每行显示的图片数量
        images_per_row = 4

        for i, image_path in enumerate(self.filtered_images):
            row = i // images_per_row
            col = i % images_per_row

            # 创建图片框架
            image_frame = Frame(parent, relief="solid", borderwidth=2, bg="white")
            image_frame.grid(row=row, column=col, padx=8, pady=8, sticky="nsew")

            try:
                # 加载和缩放图片
                img = Image.open(image_path)
                img.thumbnail((150, 150))  # 缩略图大小
                photo = ImageTk.PhotoImage(img)

                # 图片标签
                img_label = Label(image_frame, image=photo, bg="white")
                img_label.image = photo  # 保持引用
                img_label.pack(padx=5, pady=5)

                # 文件名标签
                filename = Path(image_path).name
                name_label = Label(image_frame, text=filename, wraplength=140, font=("Arial", 9), bg="white")
                name_label.pack(pady=(0, 5))

                # 文件大小标签
                file_size = os.path.getsize(image_path)
                if file_size < 1024:
                    size_str = f"{file_size}B"
                elif file_size < 1024 * 1024:
                    size_str = f"{file_size / 1024:.1f}KB"
                else:
                    size_str = f"{file_size / (1024 * 1024):.1f}MB"

                size_label = Label(image_frame, text=size_str, font=("Arial", 8), fg="gray", bg="white")
                size_label.pack(pady=(0, 5))

                # 选择按钮
                select_btn = Button(image_frame, text="选择",
                                  command=lambda path=image_path: self.select_image(path),
                                  bg="#2196F3", fg="white", width=8)
                select_btn.pack(fill="x", padx=5, pady=(0, 5))

                # 存储缩略图引用
                self.thumbnails.append(photo)

            except Exception as e:
                # 如果图片无法加载，显示错误信息
                error_label = Label(image_frame, text=f"无法加载\n{Path(image_path).name}",
                                  fg="red", wraplength=140, bg="white")
                error_label.pack(padx=5, pady=5)

    def select_image(self, image_path):
        """选择图片"""
        self.selected_path = image_path
        filename = Path(image_path).name

        # 更新所有按钮状态
        self._update_buttons(self.root, image_path)

        # 更新状态
        self.status_label.config(text=f"已选择: {filename}", fg="green")
        self.confirm_button.config(state="normal")

    def _update_buttons(self, widget, selected_path):
        """递归更新按钮状态"""
        if isinstance(widget, Button) and widget.cget("text") == "选择":
            # 检查这个按钮是否对应选中的图片
            if hasattr(widget, '_path') and widget._path == selected_path:
                widget.config(text="已选择", bg="#FF9800")
            else:
                widget.config(text="选择", bg="#2196F3")
        else:
            for child in widget.winfo_children():
                self._update_buttons(child, selected_path)

    def confirm_selection(self):
        """确认选择"""
        if self.selected_path:
            filename = Path(self.selected_path).name
            result = messagebox.askyesno("确认选择",
                                       f"确定要用 '{filename}' 作为模板图片吗？\n\n这将用此图片替换文件夹中的其他所有图片。")
            if result:
                self.root.quit()
        else:
            messagebox.showwarning("未选择", "请先选择一张图片！")

    def cancel_selection(self):
        """取消选择"""
        self.selected_path = None
        self.root.quit()

    def _on_mousewheel(self, event):
        """处理鼠标滚轮"""
        self.canvas.yview_scroll(int(-1*(event.delta/120)), "units")

    def on_search_change(self, *args):
        """搜索框内容改变时的回调函数"""
        search_text = self.search_var.get().strip().lower()
        self.filter_images(search_text)
        self.update_display()

    def clear_search(self):
        """清除搜索"""
        self.search_var.set("")
        self.filtered_images = self.image_files.copy()
        self.update_display()
        self.update_match_count()

    def filter_images(self, search_text):
        """根据搜索文本过滤图片"""
        if not search_text:
            self.filtered_images = self.image_files.copy()
        else:
            self.filtered_images = []
            for image_path in self.image_files:
                filename = Path(image_path).name.lower()
                if search_text in filename:
                    self.filtered_images.append(image_path)

    def update_display(self):
        """更新显示"""
        # 清除当前显示
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        # 重新创建图片网格
        self.create_image_grid(self.scrollable_frame)

        # 更新匹配数量
        self.update_match_count()

    def update_match_count(self):
        """更新匹配数量显示"""
        total = len(self.image_files)
        filtered = len(self.filtered_images)

        if total == filtered:
            self.match_count_label.config(text=f"共 {total} 张图片")
        else:
            self.match_count_label.config(text=f"匹配 {filtered}/{total} 张图片")

    def confirm_selection(self):
        """确认选择"""
        if self.selected_path:
            filename = Path(self.selected_path).name

            # 获取要替换的图片数量（排除选中的图片）
            current_filtered = len(self.filtered_images) - 1

            result = messagebox.askyesno("确认选择",
                                       f"确定要用 '{filename}' 作为模板图片吗？\n\n"
                                       f"将在当前搜索结果中替换 {current_filtered} 张图片。")
            if result:
                self.root.quit()
        else:
            messagebox.showwarning("未选择", "请先选择一张图片！")

    def run(self):
        """运行GUI"""
        self.root.mainloop()
        # 在窗口关闭前保存当前过滤状态
        self.final_filtered_images = self.filtered_images.copy()
        return self.selected_path


def get_image_files(folder_path, recursive=True):
    """获取文件夹中的所有图片文件

    Args:
        folder_path: 文件夹路径
        recursive: 是否递归遍历子文件夹，默认为True
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.webp'}
    image_files = []

    folder = Path(folder_path)

    if not folder.exists():
        raise FileNotFoundError(f"文件夹不存在: {folder_path}")

    # 遍历文件夹获取图片文件
    if recursive:
        # 递归遍历所有子文件夹
        for file_path in folder.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                image_files.append(str(file_path))
    else:
        # 只遍历当前文件夹
        for file_path in folder.glob('*'):
            if file_path.is_file() and file_path.suffix.lower() in image_extensions:
                image_files.append(str(file_path))

    return sorted(image_files)




def replace_images(selected_image_path, target_image_paths):
    """替换图片文件，保持目标图片的原始尺寸"""
    replaced_count = 0
    errors = []

    try:
        # 读取选中的图片
        with Image.open(selected_image_path) as selected_img:
            # 获取选中图片的格式
            selected_format = selected_img.format or 'JPEG'

            for target_path in target_image_paths:
                try:
                    # 读取目标图片以获取其尺寸
                    with Image.open(target_path) as target_img:
                        target_size = target_img.size
                        target_format = target_img.format or selected_format

                    # 缩放选中图片到目标尺寸
                    resized_img = selected_img.resize(target_size, Image.LANCZOS)

                    # 保存缩放后的图片到目标路径
                    resized_img.save(target_path, target_format)
                    replaced_count += 1
                    print(f"已替换: {Path(target_path).name}")

                except Exception as e:
                    errors.append(f"替换失败 {Path(target_path).name}: {str(e)}")

    except Exception as e:
        errors.append(f"读取选中图片失败: {str(e)}")

    return replaced_count, errors


def main():
    """主函数"""
    print("图片批量替换工具")
    print("=" * 50)

    # 获取文件夹路径
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
    else:
        print("正在打开文件夹选择对话框...")
        # 使用Windows自带的文件夹选择对话框
        root = Tk()
        root.withdraw()  # 隐藏主窗口
        root.attributes('-topmost', True)  # 让对话框显示在最前面
        folder_path = askdirectory(title="选择包含图片的文件夹", mustexist=True)
        root.destroy()

        if not folder_path:
            print("❌ 未选择文件夹，程序退出")
            return

    try:
        # 获取图片文件
        print(f"\n正在扫描文件夹: {folder_path}")
        image_files = get_image_files(folder_path)

        if len(image_files) < 2:
            print("❌ 错误: 文件夹中至少需要2张图片才能进行替换操作")
            print(f"当前找到 {len(image_files)} 张图片")
            return

        print(f"\n✅ 找到 {len(image_files)} 张图片")

        # 使用GUI选择图片
        print("\n正在打开图片选择界面...")
        gui = ImageSelectorGUI(image_files, folder_path)
        selected_image = gui.run()

        if not selected_image:
            print("❌ 未选择图片，程序退出")
            return

        # 获取过滤后的图片列表（搜索结果）
        # 注意：如果没有搜索，final_filtered_images应该等于image_files
        filtered_images = getattr(gui, 'final_filtered_images', None)
        if filtered_images is None:
            print("调试: 使用默认图片列表")
            filtered_images = image_files

        # 检查选中的图片是否在过滤结果中
        if selected_image not in filtered_images:
            print("❌ 选中的图片不在搜索结果中，请重新选择")
            return

        # 过滤掉选中的图片，得到要替换的目标图片
        target_images = [img for img in filtered_images if img != selected_image]

        if not target_images:
            print("❌ 搜索结果中只有一张图片，无法进行替换操作")
            return

        print(f"\n✓ 已选择模板图片: {Path(selected_image).name}")
        print(f"🔄 在搜索结果中将替换 {len(target_images)} 张图片")

        # 执行替换
        print("\n🔄 正在执行替换操作...")
        replaced_count, errors = replace_images(selected_image, target_images)

        # 显示结果
        print("\n" + "=" * 50)
        print("替换完成!")
        print("=" * 50)
        print(f"✅ 成功替换: {replaced_count} 张图片")

        if errors:
            print(f"❌ 错误数量: {len(errors)}")
            for error in errors:
                print(f"   - {error}")
        else:
            print("🎉 所有图片替换成功！")

    except Exception as e:
        import traceback
        error_msg = f"❌ 程序执行出错: {str(e)}"
        print(error_msg)
        print("详细错误信息:")
        traceback.print_exc()


if __name__ == "__main__":
    main()
