"""
SCSP Spine 动画 Web 播放器
- Flask 后端: 解析 SCSP/SCT2/Atlas 文件
- 前端: index.html (PixiJS + pixi-spine 渲染 Spine 3.8 动画)
"""
import os
import sys
import json
import struct
import io
import glob
import threading
from flask import Flask, jsonify, send_file, request, Response
import lz4.block

# ──── 导入解码器 ────
sys.path.insert(0, os.path.dirname(__file__))
from v0 import SCSPV3Parser


if getattr(sys, 'frozen', False):
    _meipass = getattr(sys, '_MEIPASS', '')
    app = Flask(__name__, static_folder=os.path.join(_meipass, 'static'))
else:
    app = Flask(__name__, static_folder='static')

_model_dir = None

# ──── SCT 纹理解码 ────

def _unpremultiply_alpha(img):
    """将预乘Alpha (PMA) RGBA 图像转换为标准直通Alpha (Straight Alpha)。

    GPU 压缩纹理 (ASTC/ETC2) 在 Yuna Engine 中以预乘 Alpha 形式存储:
        R_pma = R_straight × (A / 255)
    还原公式:
        R_straight = R_pma × 255 / A

    不做此转换的话，半透明边缘像素在 pixi-spine 的 SRC_ALPHA 混合模式下
    会被 Alpha 二次乘算 → 尸块衔接处出现暗色阴影。

    使用 float64 运算最大限度减少量化误差。
    """
    import numpy as np
    from PIL import Image
    arr = np.array(img, dtype=np.float64)
    alpha = arr[:, :, 3]

    # 仅处理 alpha > 0 的像素 (全透明像素 RGB 归零即可)
    mask = alpha > 0.5
    safe_alpha = np.where(mask, alpha, 1.0)  # 避免除零

    for c in range(3):  # R, G, B
        arr[:, :, c] = np.where(
            mask,
            np.clip(arr[:, :, c] * 255.0 / safe_alpha, 0.0, 255.0),
            0.0
        )

    return Image.fromarray(np.round(arr).astype(np.uint8), 'RGBA')


def decode_sct1(filepath):
    """SCT v1 → PNG bytes (3-plane: RGB565 top/bottom + raw alpha)"""
    import numpy as np
    from PIL import Image

    with open(filepath, 'rb') as f:
        data = f.read()

    # SCT v1 header: 4B magic + 1B version + 2B width + 2B height + 4B dec_size + 4B comp_size = 17 bytes
    w, h = struct.unpack_from('<HH', data, 5)
    dec_size = struct.unpack_from('<I', data, 9)[0]
    comp_size = struct.unpack_from('<I', data, 13)[0]

    if w == 0 or h == 0 or w > 16384 or h > 16384:
        raise ValueError(f"Invalid SCT1 dimensions: {w}x{h}")

    raw = lz4.block.decompress(data[17:17 + comp_size], uncompressed_size=dec_size)
    raw_np = np.frombuffer(raw, dtype=np.uint8)

    plane_size = w * h
    planes = [raw_np[i * plane_size:(i + 1) * plane_size] for i in range(3)]

    # Planes 0+1 concatenated = contiguous RGB565 byte stream (2 bytes/pixel)
    # Plane 2: raw 8-bit alpha for the full image
    rgb_bytes = np.concatenate([planes[0], planes[1]])
    p16 = np.frombuffer(rgb_bytes.tobytes(), dtype=np.uint16)
    r = ((p16 >> 11) & 0x1F).astype(np.uint8)
    g = ((p16 >> 5) & 0x3F).astype(np.uint8)
    b = (p16 & 0x1F).astype(np.uint8)
    r8 = (r << 3) | (r >> 2)
    g8 = (g << 2) | (g >> 4)
    b8 = (b << 3) | (b >> 2)
    rgb = np.stack([r8, g8, b8], axis=-1).reshape(h, w, 3)

    alpha = planes[2].reshape(h, w)
    rgba = np.dstack([rgb, alpha])

    img = Image.fromarray(rgba)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def decode_sct2(filepath):
    """SCT2 → PNG bytes (supports ASTC 4x4, ETC2 RGBA8, raw RGBA)"""
    import texture2ddecoder
    from PIL import Image

    with open(filepath, 'rb') as f:
        data = f.read()

    if data[:4] != b'SCT2':
        raise ValueError("Not SCT2 format")

    # ── Parse SCT2 header (72 bytes) ──
    header_size = struct.unpack_from('<I', data, 12)[0]  # typically 72
    detail      = struct.unpack_from('<I', data, 20)[0]  # format discriminator
    w           = struct.unpack_from('<H', data, 24)[0]  # width  (uint16)
    h           = struct.unpack_from('<H', data, 26)[0]  # height (uint16)

    if w == 0 or h == 0 or w > 16384 or h > 16384:
        raise ValueError(f"Invalid SCT2 dimensions: {w}x{h}")

    # ── Decompress payload (LZ4 or raw) ──
    payload   = data[header_size:]
    dec_size  = struct.unpack_from('<I', payload, 0)[0]
    comp_size = struct.unpack_from('<I', payload, 4)[0]
    
    if 0 < comp_size < len(payload) and 0 < dec_size < 100_000_000:
        # Standard LZ4 compressed payload
        raw = lz4.block.decompress(payload[8:8+comp_size], uncompressed_size=dec_size)
    else:
        # Raw texture data (no LZ4) — face textures etc.
        raw = payload

    # ── Decode GPU texture based on detail field ──
    # ASTC/ETC2 解码出的 BGRA 数据为 premultiplied alpha (PMA) 格式，
    # 需要 _unpremultiply_alpha() 转换为 straight alpha 后再存 PNG，
    # 否则 pixi-spine 的 SRC_ALPHA 混合会导致半透明边缘二次乘算 → 暗色接缝。
    if detail == 40:
        # ASTC 4×4 compressed (PMA)
        rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
        img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))
    elif detail == 47:
        # ASTC 8×8 compressed (PMA)
        rgba = texture2ddecoder.decode_astc(raw, w, h, 8, 8)
        img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))
    elif detail == 19:
        # ETC2 RGBA8 (EAC) compressed (PMA)
        rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
        img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))
    elif detail == 44:
        # Raw RGBA (4 bytes per pixel)
        if len(raw) >= w * h * 4:
            img = Image.frombytes('RGBA', (w, h), raw[:w*h*4])
        else:
            raise ValueError(f"detail=44 raw data too short: {len(raw)} < {w*h*4}")
    else:
        # Unknown detail — auto-detect ASTC block size by matching data length
        img = None
        astc_sizes = [(4,4), (5,5), (6,6), (8,8), (10,10), (12,12)]
        for bw, bh in astc_sizes:
            expected = ((w + bw - 1) // bw) * ((h + bh - 1) // bh) * 16
            if expected == len(raw):
                try:
                    rgba = texture2ddecoder.decode_astc(raw, w, h, bw, bh)
                    img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))
                    break
                except Exception:
                    continue
        if img is None:
            # Fallback: try ETC2A8, then ASTC 4x4 (all PMA)
            try:
                rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
                img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))
            except Exception:
                rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
                img = _unpremultiply_alpha(Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA'))

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


# ──── API 路由 ────

@app.route('/')
def index():
    # 兼容 PyInstaller / Nuitka onefile / 普通运行
    search = [getattr(sys, '_MEIPASS', ''), os.path.dirname(sys.executable), os.path.dirname(__file__), os.getcwd()]
    for base in search:
        p = os.path.join(base, 'index.html')
        if os.path.exists(p):
            return send_file(p, mimetype='text/html')
    return 'index.html not found', 404


@app.route('/spine_test')
def spine_test():
    """对比测试页面: 使用官方 Spine Canvas 3.8 运行时"""
    search = [getattr(sys, '_MEIPASS', ''), os.path.dirname(sys.executable), os.path.dirname(__file__), os.getcwd()]
    for base in search:
        p = os.path.join(base, 'spine_test.html')
        if os.path.exists(p):
            return send_file(p, mimetype='text/html')
    return 'spine_test.html not found', 404


@app.route('/api/set_folder', methods=['POST'])
def set_folder():
    global _model_dir
    data = request.json
    folder = data.get('folder', '').strip()
    if not os.path.isdir(folder):
        return jsonify({"error": f"Folder not found: {folder}"}), 400
    _model_dir = folder
    return jsonify({"ok": True, "folder": folder})


@app.route('/api/list')
def list_models():
    if not _model_dir:
        return jsonify({"models": [], "error": "No folder set"})
    
    try:
        # 递归扫描所有子目录
        items = {}  # key = "subdir/basename" or "basename"
        for root, dirs, files in os.walk(_model_dir):
            rel_root = os.path.relpath(root, _model_dir)
            if rel_root == '.':
                rel_root = ''
            for f in files:
                base, ext = os.path.splitext(f)
                ext = ext.lower()
                if ext in ['.scsp', '.sct', '.sct2', '.png', '.atlas']:
                    full_key = f"{rel_root}/{base}" if rel_root else base
                    full_key = full_key.replace('\\', '/')
                    if full_key not in items:
                        items[full_key] = {'scsp': False, 'tex': False, 'atlas': False}
                    if ext == '.scsp':
                        items[full_key]['scsp'] = True
                    elif ext == '.atlas':
                        items[full_key]['atlas'] = True
                    elif ext in ['.sct', '.sct2', '.png']:
                        items[full_key]['tex'] = True
    except Exception as e:
        return jsonify({"models": [], "error": str(e)})

    models = []
    for name in sorted(items.keys()):
        item = items[name]
        has_skel = item['scsp']
        has_tex = item['tex']
        if not has_skel and not has_tex:
            continue
        models.append({
            "name": name,
            "hasAtlas": item['atlas'],
            "hasTexture": has_tex,
            "type": "model" if has_skel else "image"
        })
    return jsonify({"models": models, "folder": _model_dir})


@app.route('/api/skeleton/<path:name>')
def get_skeleton(name):
    """解码 SCSP 并返回 Spine JSON (每次重新读取，无缓存)"""
    if not _model_dir:
        return jsonify({"error": "No folder set"}), 400
    
    scsp_path = os.path.join(_model_dir, f"{name}.scsp")
    if not os.path.exists(scsp_path):
        return jsonify({"error": f"File not found: {name}.scsp"}), 404
    
    try:
        parser = SCSPV3Parser(scsp_path)
        parser.parse()
        json_str = parser.export_json()
        return Response(json_str, mimetype='application/json')
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/atlas/<path:name>')
def get_atlas(name):
    if not _model_dir:
        return "No folder set", 400
    atlas_path = os.path.join(_model_dir, f"{name}.atlas")
    if not os.path.exists(atlas_path):
        return "Atlas not found", 404
    
    # 获取此文件所在的子目录前缀
    name_dir = '/'.join(name.replace('\\', '/').split('/')[:-1])
    
    with open(atlas_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.endswith('.png') or stripped.endswith('.sct') or stripped.endswith('.sct2'):
            tex_name = os.path.splitext(stripped)[0]
            tex_path = f"{name_dir}/{tex_name}" if name_dir else tex_name
            new_lines.append(f"{tex_path}.png")
        else:
            new_lines.append(line)
    
    return Response('\n'.join(new_lines), mimetype='text/plain')


@app.route('/api/texture/<path:name>.png')
def get_texture(name):
    if not _model_dir:
        return "No folder set", 400
    
    def _find_texture(name):
        """在 _model_dir 及其父级/兄弟目录中搜索纹理文件"""
        # 1) 当前目录
        for ext in ['.png', '.sct', '.sct2']:
            p = os.path.join(_model_dir, f"{name}{ext}")
            if os.path.exists(p):
                return p
        
        # 2) 向上搜索父目录和兄弟目录 (最多向上2级)
        search_root = _model_dir
        for _ in range(2):
            parent = os.path.dirname(search_root)
            if parent == search_root:
                break
            # 搜索父目录下所有子目录 (深度2)
            for root, dirs, files in os.walk(parent):
                depth = root.replace(parent, '').count(os.sep)
                if depth > 2:
                    dirs.clear()
                    continue
                for ext in ['.png', '.sct', '.sct2']:
                    if f"{name}{ext}" in files:
                        return os.path.join(root, f"{name}{ext}")
            search_root = parent
        
        return None
    
    found = _find_texture(name)
    if not found:
        return "Texture not found", 404
    
    ext = os.path.splitext(found)[1].lower()
    
    # PNG 直接发送
    if ext == '.png':
        return send_file(found, mimetype='image/png')
    
    # SCT/SCT2 解码为 PNG — 根据文件头魔数自动选择解码器
    try:
        with open(found, 'rb') as f:
            magic = f.read(4)
        if magic == b'SCT2':
            png_data = decode_sct2(found)
        elif magic[:3] == b'SCT':
            png_data = decode_sct1(found)
        else:
            return f"Unknown texture format: {magic!r}", 400
        return Response(png_data, mimetype='image/png')
    except Exception as e:
        return f"Texture decode error ({os.path.basename(found)}): {e}", 500


def _extractor_process():
    """在独立进程中运行模型解包 GUI"""
    from v0 import model_extractor
    gui = model_extractor.ModelExtractorApp()
    gui.mainloop()


def _repacker_process():
    """在独立进程中运行封包 GUI (精简版)"""
    import customtkinter as ctk
    from tkinter import filedialog, messagebox
    import threading
    from v0.pack_repacker import repack_from_folder

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    class RepackerApp(ctk.CTk):
        def __init__(self):
            super().__init__()
            self.title("PLPcK 封包工具")
            self.geometry("650x480")
            self.minsize(550, 400)
            self.configure(fg_color="#1E1E2E")

            self.pack_path = ctk.StringVar(value="")
            self.folder_path = ctk.StringVar(value="")
            self.output_path = ctk.StringVar(value="")
            self._build_ui()

        def _build_ui(self):
            # 标题
            title = ctk.CTkLabel(self, text="📦 PLPcK 封包工具",
                                 font=ctk.CTkFont(size=20, weight="bold"),
                                 text_color="#F8FAFC")
            title.pack(pady=(15, 5))
            ctk.CTkLabel(self, text="选择原始 .pack 和替换文件夹，一键重建封包",
                         font=ctk.CTkFont(size=12), text_color="#94A3B8").pack(pady=(0, 10))

            # 原始 pack 文件
            self._build_path_row("原始 .pack 文件:", self.pack_path,
                                 lambda: self._browse_file(self.pack_path))
            # 替换文件夹
            self._build_path_row("替换文件夹:", self.folder_path,
                                 lambda: self._browse_folder(self.folder_path))
            # 输出路径
            self._build_path_row("输出路径:", self.output_path,
                                 lambda: self._browse_save(self.output_path))

            # 开始按钮
            self.start_btn = ctk.CTkButton(self, text="🚀 开始封包", height=40,
                                           corner_radius=8, fg_color="#3B82F6",
                                           hover_color="#2563EB",
                                           font=ctk.CTkFont(size=14, weight="bold"),
                                           command=self._start_repack)
            self.start_btn.pack(fill="x", padx=20, pady=(10, 5))

            # 进度条
            self.progress_bar = ctk.CTkProgressBar(self, height=8, corner_radius=4,
                                                    fg_color="#313244",
                                                    progress_color="#10B981")
            self.progress_bar.pack(fill="x", padx=20, pady=(5, 5))
            self.progress_bar.set(0)

            self.progress_label = ctk.CTkLabel(self, text="等待操作",
                                               font=ctk.CTkFont(size=12),
                                               text_color="#94A3B8")
            self.progress_label.pack(pady=(0, 5))

            # 日志
            self.log_text = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas", size=12),
                                            fg_color="#11111B", text_color="#A6ACCD",
                                            corner_radius=6, wrap="word", state="disabled")
            self.log_text.pack(fill="both", expand=True, padx=20, pady=(0, 15))

        def _build_path_row(self, label_text, var, browse_cmd):
            frame = ctk.CTkFrame(self, fg_color="transparent")
            frame.pack(fill="x", padx=20, pady=3)
            ctk.CTkLabel(frame, text=label_text, font=ctk.CTkFont(size=12),
                         text_color="#F8FAFC", width=110, anchor="w").pack(side="left")
            ctk.CTkEntry(frame, textvariable=var, font=ctk.CTkFont(size=12),
                         height=32, fg_color="#181825", text_color="#F8FAFC",
                         border_width=1, border_color="#383854"
                         ).pack(side="left", fill="x", expand=True, padx=(0, 8))
            ctk.CTkButton(frame, text="浏览", width=60, height=32,
                          corner_radius=6, fg_color="#3B82F6",
                          command=browse_cmd).pack(side="right")

        def _browse_file(self, var):
            path = filedialog.askopenfilename(title="选择 .pack 文件",
                                              filetypes=[("Pack files", "*.pack"), ("All", "*.*")])
            if path:
                var.set(path)
                if not self.output_path.get():
                    base, ext = os.path.splitext(path)
                    self.output_path.set(f"{base}_rebuilt{ext}")

        def _browse_folder(self, var):
            path = filedialog.askdirectory(title="选择替换文件夹")
            if path:
                var.set(path)

        def _browse_save(self, var):
            path = filedialog.asksaveasfilename(title="输出路径",
                                                defaultextension=".pack",
                                                filetypes=[("Pack files", "*.pack")])
            if path:
                var.set(path)

        def _log(self, msg):
            def _append():
                self.log_text.configure(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.configure(state="disabled")
            self.after(0, _append)

        def _set_progress(self, val, text):
            def _update():
                self.progress_bar.set(val)
                self.progress_label.configure(text=text)
            self.after(0, _update)

        def _start_repack(self):
            pack_p = self.pack_path.get().strip()
            folder_p = self.folder_path.get().strip()
            output_p = self.output_path.get().strip()

            if not pack_p or not os.path.isfile(pack_p):
                messagebox.showerror("错误", "请选择有效的原始 .pack 文件")
                return
            if not folder_p or not os.path.isdir(folder_p):
                messagebox.showerror("错误", "请选择有效的替换文件夹")
                return
            if not output_p:
                messagebox.showerror("错误", "请指定输出路径")
                return

            self.start_btn.configure(state="disabled")
            thread = threading.Thread(target=self._run_repack,
                                      args=(pack_p, folder_p, output_p), daemon=True)
            thread.start()

        def _run_repack(self, pack_p, folder_p, output_p):
            try:
                t0 = time.time()
                self._log(f"{'=' * 50}")
                self._log(f"开始封包...")
                total, replaced, new = repack_from_folder(
                    pack_p, folder_p, output_p,
                    progress_callback=self._set_progress,
                    log_callback=self._log
                )
                elapsed = time.time() - t0
                self._log(f"{'=' * 50}")
                self._log(f"✅ 完成! 耗时 {elapsed:.1f}s, 替换 {replaced} 个, 新增 {new} 个")
                self._log(f"   输出: {output_p} ({total / 1024 / 1024:.1f} MB)")
                self.after(0, lambda: messagebox.showinfo("完成",
                    f"封包完成！\n替换 {replaced} 个文件, 新增 {new} 个\n输出: {output_p}"))
            except Exception as e:
                import traceback
                self._log(f"❌ 错误: {e}")
                self._log(traceback.format_exc())
                self.after(0, lambda: messagebox.showerror("错误", str(e)))
            finally:
                self.after(0, lambda: self.start_btn.configure(state="normal"))

    app = RepackerApp()
    app.mainloop()


def _png2sct_process():
    """在独立进程中运行 PNG→SCT 转换 GUI"""
    from v0.png_to_sct import PngToSctApp
    gui = PngToSctApp()
    gui.mainloop()


@app.route('/api/launch_extractor', methods=['POST'])
def launch_extractor():
    """启动模型解包 GUI (独立进程，兼容 Nuitka onefile)"""
    try:
        import multiprocessing
        p = multiprocessing.Process(target=_extractor_process, daemon=True)
        p.start()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/launch_repacker', methods=['POST'])
def launch_repacker():
    """启动封包 GUI (ChaosZero Toolkit)"""
    try:
        import multiprocessing
        p = multiprocessing.Process(target=_repacker_process, daemon=True)
        p.start()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@app.route('/api/launch_png2sct', methods=['POST'])
def launch_png2sct():
    """启动 PNG→SCT 转换 GUI"""
    try:
        import multiprocessing
        p = multiprocessing.Process(target=_png2sct_process, daemon=True)
        p.start()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


if __name__ == '__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    import webbrowser
    import socket
    import subprocess

    EXE_NAME = "SCSP_Spine_Viewer.exe"

    # ──── 检测并结束旧实例 ────
    def kill_old_instances():
        """检测并结束旧的 SCSP_Spine_Viewer.exe 进程"""
        my_pid = os.getpid()
        killed = False
        try:
            # 用 tasklist 查找同名进程
            result = subprocess.run(
                ['tasklist', '/FI', f'IMAGENAME eq {EXE_NAME}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, creationflags=0x08000000  # CREATE_NO_WINDOW
            )
            for line in result.stdout.strip().split('\n'):
                line = line.strip().strip('"')
                if not line or EXE_NAME.lower() not in line.lower():
                    continue
                parts = line.split('","')
                if len(parts) >= 2:
                    try:
                        pid = int(parts[1].strip('"'))
                        if pid != my_pid:
                            subprocess.run(
                                ['taskkill', '/F', '/PID', str(pid)],
                                capture_output=True, creationflags=0x08000000
                            )
                            killed = True
                    except (ValueError, IndexError):
                        continue
        except Exception:
            pass

        # 也检测端口 5000 是否被占用
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1)
            s.connect(('127.0.0.1', 5000))
            s.close()
            # 端口被占用，尝试释放
            if not killed:
                subprocess.run(
                    ['powershell', '-Command',
                     f"Get-NetTCPConnection -LocalPort 5000 -ErrorAction SilentlyContinue | "
                     f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
                    capture_output=True, creationflags=0x08000000
                )
                killed = True
        except (ConnectionRefusedError, OSError):
            pass  # 端口没被占用，正常

        if killed:
            print("[WARN] Detected an old instance running, auto-terminated it.")
            import time
            time.sleep(0.5)  # 等待端口释放

        return killed

    kill_old_instances()

    print("\n" + "="*50)
    print("  SCSP Spine Viewer v1.05")
    print("  http://localhost:5000")
    print("="*50 + "\n")

    # 启动 1.5 秒后自动打开浏览器
    threading.Timer(1.5, lambda: webbrowser.open('http://localhost:5000')).start()

    try:
        app.run(host='0.0.0.0', port=5000, debug=False)
    except OSError as e:
        if 'address already in use' in str(e).lower() or '10048' in str(e):
            print(f"\n[ERROR] 端口 5000 被占用！请关闭占用该端口的程序后重试。")
        else:
            print(f"\n[ERROR] {e}")
        input("按回车键退出...")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        input("按回车键退出...")
