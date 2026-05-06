#!/usr/bin/env python3
"""
PNG → SCT 转换器 — 将 PNG 图片编码为 SCT 纹理格式
支持两种游戏格式:
  - 第七史诗 (Epic Seven)    → SCT v1 (3-plane RGB565+Alpha, LZ4)
  - 卡厄斯梦境 (ChaosZero)   → SCT2  (raw RGBA, LZ4, 72-byte header)

GUI 界面，基于 customtkinter
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os, sys, struct, time, threading
import numpy as np
from PIL import Image
import lz4.block
import io

# ═══════════════════════════════════════════════════════════════════════
# 主题 (复用解包工具同款)
# ═══════════════════════════════════════════════════════════════════════
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

COLOR_BG           = "#1E1E2E"
COLOR_BG_CARD      = "#27273A"
COLOR_ACCENT       = "#6c5ce7"
COLOR_ACCENT_HOVER = "#5b4bd6"
COLOR_SUCCESS      = "#10B981"
COLOR_WARNING      = "#F59E0B"
COLOR_TEXT         = "#F8FAFC"
COLOR_TEXT_DIM     = "#94A3B8"
COLOR_BORDER       = "#383854"
COLOR_LOG_BG       = "#11111B"

GLOBAL_FONT = ("Microsoft YaHei UI", "Segoe UI")

# ═══════════════════════════════════════════════════════════════════════
# SCT v1 编码 (第七史诗 / Epic Seven)
# ═══════════════════════════════════════════════════════════════════════

def encode_png_to_sct1(png_path, sct_path=None, log_fn=None):
    """
    将 PNG 编码为 SCT v1 格式。

    SCT v1 结构:
      Header (17 bytes):
        [0:3]  magic  b'SCT'
        [3]    0x00
        [4]    version  0x01
        [5:7]  width   (uint16 LE)
        [7:9]  height  (uint16 LE)
        [9:13] dec_size (uint32 LE)
        [13:17] comp_size (uint32 LE)
      Payload: LZ4 压缩的 3-plane 数据
        Plane 0: RGB565 低字节 (w*h bytes)
        Plane 1: RGB565 高字节 (w*h bytes)
        Plane 2: Alpha 通道 (w*h bytes)
    """
    def _log(msg):
        if log_fn: log_fn(msg)
        else: print(msg)

    _log(f"读取 PNG: {os.path.basename(png_path)}")
    img = Image.open(png_path).convert('RGBA')
    w, h = img.size
    _log(f"  尺寸: {w}x{h}")

    rgba = np.array(img, dtype=np.uint8)
    r = rgba[:, :, 0].flatten()
    g = rgba[:, :, 1].flatten()
    b = rgba[:, :, 2].flatten()
    a = rgba[:, :, 3].flatten()

    # RGBA → RGB565
    r5 = (r >> 3).astype(np.uint16)
    g6 = (g >> 2).astype(np.uint16)
    b5 = (b >> 3).astype(np.uint16)
    rgb565 = (r5 << 11) | (g6 << 5) | b5

    # RGB565 拆分为 2 个字节平面
    rgb565_bytes = rgb565.astype(np.uint16).tobytes()
    plane_size = w * h
    rgb_np = np.frombuffer(rgb565_bytes, dtype=np.uint8)
    plane0 = rgb_np[:plane_size]   # 低字节
    plane1 = rgb_np[plane_size:]   # 高字节
    plane2 = a.astype(np.uint8)    # Alpha

    # 拼接 3 平面
    raw_data = plane0.tobytes() + plane1.tobytes() + plane2.tobytes()
    dec_size = len(raw_data)

    # LZ4 压缩
    compressed = lz4.block.compress(raw_data, store_size=False)
    comp_size = len(compressed)
    _log(f"  压缩: {dec_size:,} → {comp_size:,} bytes ({comp_size/dec_size*100:.1f}%)")

    # 构建 SCT1 header (17 bytes)
    header = bytearray(17)
    header[0:3] = b'SCT'
    header[3] = 0x00
    header[4] = 0x01
    struct.pack_into('<HH', header, 5, w, h)
    struct.pack_into('<I', header, 9, dec_size)
    struct.pack_into('<I', header, 13, comp_size)

    if sct_path is None:
        sct_path = os.path.splitext(png_path)[0] + '.sct'

    with open(sct_path, 'wb') as f:
        f.write(header)
        f.write(compressed)

    total_size = 17 + comp_size
    _log(f"  ✅ SCT v1 → {os.path.basename(sct_path)} ({total_size:,} bytes)")
    return sct_path, total_size


# ═══════════════════════════════════════════════════════════════════════
# SCT2 编码 (卡厄斯梦境 / ChaosZero Nightmare)
# ═══════════════════════════════════════════════════════════════════════

def encode_png_to_sct2(png_path, sct_path=None, log_fn=None):
    """
    将 PNG 编码为 SCT2 格式 (detail=44, raw RGBA + LZ4)。

    SCT2 结构:
      Header (72 bytes):
        [0:4]   magic  b'SCT2'
        [4:8]   total_file_size  (uint32 LE)
        [8:12]  checksum / reserved
        [12:16] header_size = 72  (uint32 LE)
        [16:20] mip_levels = 1  (uint32 LE)
        [20:24] detail = 44  (uint32 LE) → raw RGBA 格式
        [24:26] width   (uint16 LE)
        [26:28] height  (uint16 LE)
        [28:30] width   (uint16 LE) (重复)
        [30:32] height  (uint16 LE) (重复)
        [32:72] metadata (40 bytes, 置零)
      Payload:
        [0:4]   dec_size  (uint32 LE)
        [4:8]   comp_size (uint32 LE)
        [8:]    LZ4 compressed RGBA data
    """
    def _log(msg):
        if log_fn: log_fn(msg)
        else: print(msg)

    _log(f"读取 PNG: {os.path.basename(png_path)}")
    img = Image.open(png_path).convert('RGBA')
    w, h = img.size
    _log(f"  尺寸: {w}x{h}")

    # 直接使用 raw RGBA 数据
    raw_data = img.tobytes()
    dec_size = len(raw_data)
    assert dec_size == w * h * 4

    # LZ4 压缩
    compressed = lz4.block.compress(raw_data, store_size=False)
    comp_size = len(compressed)
    _log(f"  压缩: {dec_size:,} → {comp_size:,} bytes ({comp_size/dec_size*100:.1f}%)")

    # 构建 SCT2 header (72 bytes)
    header = bytearray(72)
    header[0:4] = b'SCT2'
    # [4:8] total_file_size — 填入后再更新
    struct.pack_into('<I', header, 12, 72)       # header_size
    struct.pack_into('<I', header, 16, 1)        # mip_levels
    struct.pack_into('<I', header, 20, 44)       # detail = raw RGBA
    struct.pack_into('<H', header, 24, w)        # width
    struct.pack_into('<H', header, 26, h)        # height
    struct.pack_into('<H', header, 28, w)        # width (重复)
    struct.pack_into('<H', header, 30, h)        # height (重复)

    # Payload header (8 bytes)
    payload_hdr = bytearray(8)
    struct.pack_into('<I', payload_hdr, 0, dec_size)
    struct.pack_into('<I', payload_hdr, 4, comp_size)

    # 计算总大小并回填
    total_size = 72 + 8 + comp_size
    struct.pack_into('<I', header, 4, total_size)

    if sct_path is None:
        sct_path = os.path.splitext(png_path)[0] + '.sct'

    with open(sct_path, 'wb') as f:
        f.write(header)
        f.write(payload_hdr)
        f.write(compressed)

    _log(f"  ✅ SCT2 → {os.path.basename(sct_path)} ({total_size:,} bytes)")
    return sct_path, total_size


# ═══════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════

class PngToSctApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("🖼️ PNG → SCT 转换器")
        self.geometry("650x580")
        self.minsize(550, 480)
        self.configure(fg_color=COLOR_BG)

        self.png_files = []
        self.output_dir = ctk.StringVar(value="")
        self.game_mode = ctk.StringVar(value="e7")  # "e7" or "cz"
        self.is_running = False

        self._build_ui()

    def _build_ui(self):
        # ── 标题 ──
        title_frame = ctk.CTkFrame(self, fg_color="#11111B", corner_radius=0, height=55)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame, text="🖼️ PNG → SCT 转换器",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=18, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=20, pady=12)

        ctk.CTkLabel(
            title_frame, text="将 PNG 图片编码为游戏纹理格式",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12),
            text_color=COLOR_TEXT_DIM
        ).pack(side="left", padx=8)

        # ── 游戏选择 ──
        game_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDER)
        game_card.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            game_card, text="🎮 选择游戏格式",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=14, weight="bold"),
            text_color=COLOR_TEXT, anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 6))

        mode_row = ctk.CTkFrame(game_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(0, 10))

        self.rb_e7 = ctk.CTkRadioButton(
            mode_row, text="⚔ 第七史诗 (Epic Seven) — SCT v1 格式",
            variable=self.game_mode, value="e7",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13),
            text_color=COLOR_TEXT,
            fg_color="#3B82F6", hover_color="#2563EB",
            border_color=COLOR_BORDER
        )
        self.rb_e7.pack(fill="x", pady=2)

        self.rb_cz = ctk.CTkRadioButton(
            mode_row, text="🌑 卡厄斯梦境 (ChaosZero) — SCT2 格式",
            variable=self.game_mode, value="cz",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13),
            text_color=COLOR_TEXT,
            fg_color="#E17055", hover_color="#D63031",
            border_color=COLOR_BORDER
        )
        self.rb_cz.pack(fill="x", pady=2)

        # ── 文件选择 ──
        file_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDER)
        file_card.pack(fill="x", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            file_card, text="📂 选择 PNG 文件",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=14, weight="bold"),
            text_color=COLOR_TEXT, anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 2))

        self.file_label = ctk.CTkLabel(
            file_card, text="未选择文件",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=11),
            text_color=COLOR_TEXT_DIM, anchor="w"
        )
        self.file_label.pack(fill="x", padx=16, pady=(0, 6))

        row1 = ctk.CTkFrame(file_card, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkButton(
            row1, text="📄 选择文件", width=100, height=34,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12, weight="bold"),
            command=self._browse_files
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            row1, text="📁 选择文件夹 (批量)", width=140, height=34,
            fg_color=COLOR_SUCCESS, hover_color="#059669",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12, weight="bold"),
            command=self._browse_folder
        ).pack(side="left")

        # ── 输出路径 ──
        out_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                 border_width=1, border_color=COLOR_BORDER)
        out_card.pack(fill="x", padx=16, pady=(0, 6))

        row2 = ctk.CTkFrame(out_card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(
            row2, text="输出到 (留空=源文件同目录):",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12),
            text_color=COLOR_TEXT_DIM
        ).pack(side="left", padx=(0, 8))

        ctk.CTkEntry(
            row2, textvariable=self.output_dir,
            placeholder_text="留空 = 源文件同目录",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12), height=34,
            fg_color="#181825", text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            row2, text="📂", width=40, height=34,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._browse_out
        ).pack(side="right")

        # ── 操作按钮 ──
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 6))

        self.start_btn = ctk.CTkButton(
            btn_row, text="🚀 开始转换", height=40,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=14, weight="bold"),
            command=self._start_convert
        )
        self.start_btn.pack(fill="x")

        # ── 进度 ──
        prog_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDER)
        prog_card.pack(fill="x", padx=16, pady=(0, 6))

        prog_row = ctk.CTkFrame(prog_card, fg_color="transparent")
        prog_row.pack(fill="x", padx=14, pady=(8, 2))

        self.progress_label = ctk.CTkLabel(
            prog_row, text="等待操作",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12),
            text_color=COLOR_TEXT, anchor="w"
        )
        self.progress_label.pack(side="left")

        self.progress_pct = ctk.CTkLabel(
            prog_row, text="0%",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12, weight="bold"),
            text_color=COLOR_SUCCESS, anchor="e"
        )
        self.progress_pct.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(
            prog_card, height=8, corner_radius=4,
            fg_color="#313244", progress_color=COLOR_SUCCESS
        )
        self.progress_bar.pack(fill="x", padx=14, pady=(2, 10))
        self.progress_bar.set(0)

        # ── 日志 ──
        log_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                 border_width=1, border_color=COLOR_BORDER)
        log_card.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        ctk.CTkLabel(
            log_card, text="📋 日志",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13, weight="bold"),
            text_color=COLOR_TEXT, anchor="w"
        ).pack(fill="x", padx=14, pady=(8, 4))

        self.log_text = ctk.CTkTextbox(
            log_card, font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLOR_LOG_BG, text_color="#A6ACCD",
            corner_radius=6, wrap="word", state="disabled"
        )
        self.log_text.pack(fill="both", expand=True, padx=14, pady=(0, 12))

    # ── 工具方法 ──
    def _log(self, msg):
        def _a():
            self.log_text.configure(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log_text.insert("end", f"[{ts}] {msg}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.after(0, _a)

    def _set_progress(self, val, text=""):
        def _u():
            self.progress_bar.set(val)
            self.progress_pct.configure(text=f"{int(val*100)}%")
            if text: self.progress_label.configure(text=text)
        self.after(0, _u)

    # ── 文件选择 ──
    def _browse_files(self):
        files = filedialog.askopenfilenames(
            title="选择 PNG 文件",
            filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")]
        )
        if files:
            self.png_files = list(files)
            self.file_label.configure(text=f"已选择 {len(self.png_files)} 个文件")
            self._log(f"选择了 {len(self.png_files)} 个文件")

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="选择包含 PNG 的文件夹")
        if folder:
            pngs = [os.path.join(folder, f) for f in os.listdir(folder)
                    if f.lower().endswith('.png')]
            if pngs:
                self.png_files = pngs
                self.file_label.configure(text=f"文件夹: {len(pngs)} 个 PNG")
                self._log(f"从 {folder} 找到 {len(pngs)} 个 PNG")
            else:
                messagebox.showinfo("提示", "该文件夹中没有 PNG 文件")

    def _browse_out(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p: self.output_dir.set(p)

    # ── 转换 ──
    def _start_convert(self):
        if not self.png_files:
            messagebox.showwarning("提示", "请先选择 PNG 文件")
            return
        self.is_running = True
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._convert_worker, daemon=True).start()

    def _convert_worker(self):
        try:
            mode = self.game_mode.get()
            mode_name = "第七史诗 (SCT v1)" if mode == "e7" else "卡厄斯梦境 (SCT2)"
            encode_fn = encode_png_to_sct1 if mode == "e7" else encode_png_to_sct2

            total = len(self.png_files)
            self._log("=" * 40)
            self._log(f"格式: {mode_name}")
            self._log(f"开始转换 {total} 个文件...")
            self._log("=" * 40)
            success = 0; failed = 0

            for i, png_path in enumerate(self.png_files):
                try:
                    out_dir = self.output_dir.get().strip()
                    if out_dir:
                        os.makedirs(out_dir, exist_ok=True)
                        sct_name = os.path.splitext(os.path.basename(png_path))[0] + '.sct'
                        sct_path = os.path.join(out_dir, sct_name)
                    else:
                        sct_path = None  # same dir as source

                    encode_fn(png_path, sct_path, log_fn=self._log)
                    success += 1
                except Exception as ex:
                    failed += 1
                    self._log(f"❌ {os.path.basename(png_path)}: {ex}")

                pct = (i + 1) / total
                self._set_progress(pct, f"转换中 {i+1}/{total}")

            self._set_progress(1.0, "完成!")
            self._log("=" * 40)
            self._log(f"✅ 转换完成! 成功: {success}, 失败: {failed}")
            self._log("=" * 40)

            self.after(0, lambda: messagebox.showinfo(
                "转换完成",
                f"格式: {mode_name}\n成功: {success} 个\n失败: {failed} 个"
            ))
        except Exception as e:
            self._log(f"❌ 错误: {e}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            self.is_running = False
            self.after(0, lambda: self.start_btn.configure(state="normal"))


if __name__ == '__main__':
    app = PngToSctApp()
    app.mainloop()
