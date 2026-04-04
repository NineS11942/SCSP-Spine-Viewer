#!/usr/bin/env python3
"""
Model Extractor — 从 data.pack 提取 Spine 模型文件
简易 GUI 界面，基于 customtkinter
只提取 model/ 文件夹下的 .scsp / .sct / .sct2 / .atlas 文件
"""
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox
import os, sys, threading, time, struct, string
import numpy as np

# ═══════════════════════════════════════════════════════════════════════
# 主题
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

GAME_FOLDER_NAME  = "ChaosZeroNightmare"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if hasattr(sys, 'frozen') or "__compiled__" in dir():
    EXE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
else:
    EXE_DIR = SCRIPT_DIR

# ═══════════════════════════════════════════════════════════════════════
# 复用 unpack_data.py 的核心解包逻辑 (内联精简版)
# ═══════════════════════════════════════════════════════════════════════

def generate_pack_xor_key():
    seed = 150812
    key = bytearray(129)
    for i in range(129):
        seed = (seed * 1103515245) & 0xFFFFFFFF
        key[i] = (seed >> 16) & 0xFF
    return bytes(key)

PACK_XOR_KEY = generate_pack_xor_key()
PACK_XOR_KEY_NP = np.frombuffer(PACK_XOR_KEY, dtype=np.uint8)

def np_xor_decrypt(data, offset, key_np):
    n = len(data)
    if n == 0: return b''
    key_len = len(key_np)
    data_np = np.frombuffer(data, dtype=np.uint8)
    phase = offset % key_len
    total = phase + n
    repeats = (total // key_len) + 1
    tiled = np.tile(key_np, repeats)
    key_stream = tiled[phase:phase + n]
    return np.bitwise_xor(data_np, key_stream).tobytes()

def pack_xor_decrypt(data, offset):
    return np_xor_decrypt(data, offset, PACK_XOR_KEY_NP)


class MultiVolumePack:
    def __init__(self, base_dir):
        self.volumes = []
        pack_base = os.path.join(base_dir, "data.pack")
        if not os.path.exists(pack_base):
            raise FileNotFoundError(f"data.pack not found in {base_dir}")
        sz = os.path.getsize(pack_base)
        self.volumes.append((pack_base, 0, sz))
        cumulative = sz
        n = 1
        while True:
            vpath = f"{pack_base}~{n}"
            if not os.path.exists(vpath): break
            sz = os.path.getsize(vpath)
            self.volumes.append((vpath, cumulative, sz))
            cumulative += sz
            n += 1
        self.total_size = cumulative
        self._handles = {}

    def _get_handle(self, vi):
        if vi not in self._handles:
            self._handles[vi] = open(self.volumes[vi][0], 'rb')
        return self._handles[vi]

    def read_raw(self, offset, size):
        result = bytearray()
        remaining = size
        cur = offset
        for i, (path, vol_start, vol_size) in enumerate(self.volumes):
            vol_end = vol_start + vol_size
            if cur >= vol_end: continue
            if cur < vol_start: continue
            local_off = cur - vol_start
            can_read = min(remaining, vol_size - local_off)
            fh = self._get_handle(i)
            fh.seek(local_off)
            chunk = fh.read(can_read)
            result.extend(chunk)
            remaining -= len(chunk)
            cur += len(chunk)
            if remaining <= 0: break
        return bytes(result)

    def read_xor(self, offset, size):
        raw = self.read_raw(offset, size)
        return pack_xor_decrypt(raw, offset)

    def close(self):
        for fh in self._handles.values(): fh.close()
        self._handles.clear()

    def __enter__(self): return self
    def __exit__(self, *a): self.close()


def collect_all_entries(pack, hash_count):
    ht_offset = 43
    ht_size = hash_count * 5
    ht_data = pack.read_xor(ht_offset, ht_size)
    entries = []
    seen = set()
    for bucket in range(hash_count):
        off5 = bucket * 5
        high = ht_data[off5]
        low = struct.unpack_from('<I', ht_data, off5 + 1)[0]
        chain = low + (high << 32)
        if chain == 0: continue
        safety = 0
        while chain > 0 and chain + 15 <= pack.total_size and safety < 500:
            safety += 1
            if chain in seen: break
            seen.add(chain)
            hdr = pack.read_xor(chain, 15)
            ds = struct.unpack_from('<I', hdr, 0)[0]
            kl = hdr[5]
            vs = struct.unpack_from('<I', hdr, 6)[0]
            nh = hdr[10]
            nl = struct.unpack_from('<I', hdr, 11)[0]
            nxt = nl + (nh << 32)
            if ds > 0 and kl > 0 and ds < 200_000_000:
                key = pack.read_xor(chain + 15, min(kl, 512))
                try: fn = key[:kl].decode('utf-8')
                except: fn = key[:kl].decode('latin-1')
                entries.append({
                    'filename': fn,
                    'content_offset': chain + 15 + kl,
                    'content_size': vs,
                })
            if nxt == 0 or nxt == chain: break
            chain = nxt
    return entries


def extract_file(pack, entry, out_dir):
    fn = entry['filename']
    off = entry['content_offset']
    sz = entry['content_size']
    if sz <= 0: return False
    out_path = os.path.join(out_dir, fn.replace('/', os.sep))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    CHUNK = 16 * 1024 * 1024
    with open(out_path, 'wb') as f:
        written = 0
        while written < sz:
            csz = min(CHUNK, sz - written)
            chunk = pack.read_xor(off + written, csz)
            f.write(chunk)
            written += len(chunk)
    return True


# ═══════════════════════════════════════════════════════════════════════
# GUI
# ═══════════════════════════════════════════════════════════════════════

class ModelExtractorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("📦 SCSP Model Extractor — 模型文件解包工具")
        self.geometry("700x620")
        self.minsize(600, 500)
        self.configure(fg_color=COLOR_BG)

        self.pack_dir = ctk.StringVar(value="")
        self.output_dir = ctk.StringVar(value=os.path.join(EXE_DIR, "models"))
        self.extract_mode = ctk.StringVar(value="models")
        self.is_running = False
        self._stop = False

        self._build_ui()

    def _build_ui(self):
        # ── 标题 ──
        title_frame = ctk.CTkFrame(self, fg_color="#11111B", corner_radius=0, height=55)
        title_frame.pack(fill="x")
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame, text="📦 SCSP Model Extractor",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=18, weight="bold"),
            text_color="#FFFFFF"
        ).pack(side="left", padx=20, pady=12)

        ctk.CTkLabel(
            title_frame, text="从 data.pack 提取 Spine 模型",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12),
            text_color=COLOR_TEXT_DIM
        ).pack(side="left", padx=8)

        # ── 路径选择 ──
        path_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDER)
        path_card.pack(fill="x", padx=16, pady=(12, 6))

        ctk.CTkLabel(
            path_card, text="📁 data.pack 所在目录",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=14, weight="bold"),
            text_color=COLOR_TEXT, anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 2))

        ctk.CTkLabel(
            path_card, text="选择包含 data.pack 的文件夹 (通常在 appdata/cznlive 目录下)",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=11),
            text_color=COLOR_TEXT_DIM, anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 6))

        row1 = ctk.CTkFrame(path_card, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(0, 10))

        ctk.CTkEntry(
            row1, textvariable=self.pack_dir,
            placeholder_text="data.pack 目录路径...",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12), height=34,
            fg_color="#181825", text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            row1, text="🔍 自动寻找", width=100, height=34,
            fg_color=COLOR_SUCCESS, hover_color="#059669",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12, weight="bold"),
            command=self._auto_find
        ).pack(side="right", padx=(0, 6))

        ctk.CTkButton(
            row1, text="📂 浏览", width=80, height=34,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12, weight="bold"),
            command=self._browse
        ).pack(side="right")

        # ── 输出路径 ──
        out_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                 border_width=1, border_color=COLOR_BORDER)
        out_card.pack(fill="x", padx=16, pady=(0, 6))

        row2 = ctk.CTkFrame(out_card, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=10)

        ctk.CTkLabel(
            row2, text="输出到:",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12),
            text_color=COLOR_TEXT_DIM
        ).pack(side="left", padx=(0, 8))

        ctk.CTkEntry(
            row2, textvariable=self.output_dir,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=12), height=34,
            fg_color="#181825", text_color=COLOR_TEXT,
            border_width=1, border_color=COLOR_BORDER
        ).pack(side="left", fill="x", expand=True, padx=(0, 8))

        ctk.CTkButton(
            row2, text="📂", width=40, height=34,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            command=self._browse_out
        ).pack(side="right")

        # ── 解包模式选择 ──
        mode_card = ctk.CTkFrame(self, fg_color=COLOR_BG_CARD, corner_radius=10,
                                  border_width=1, border_color=COLOR_BORDER)
        mode_card.pack(fill="x", padx=16, pady=(0, 6))

        ctk.CTkLabel(
            mode_card, text="📋 解包模式",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13, weight="bold"),
            text_color=COLOR_TEXT, anchor="w"
        ).pack(fill="x", padx=14, pady=(10, 6))

        mode_row = ctk.CTkFrame(mode_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(0, 10))

        self.mode_models_btn = ctk.CTkRadioButton(
            mode_row, text="🦴 仅提取模型文件 (model/ 下的 .scsp .sct .sct2 .atlas)",
            variable=self.extract_mode, value="models",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13),
            text_color=COLOR_TEXT,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            border_color=COLOR_BORDER
        )
        self.mode_models_btn.pack(fill="x", pady=2)

        self.mode_all_btn = ctk.CTkRadioButton(
            mode_row, text="📦 全量解包 (提取 data.pack 中所有文件, ~4GB+)",
            variable=self.extract_mode, value="all",
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13),
            text_color=COLOR_TEXT,
            fg_color=COLOR_WARNING, hover_color="#D97706",
            border_color=COLOR_BORDER
        )
        self.mode_all_btn.pack(fill="x", pady=2)

        # ── 操作按钮 ──
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 6))

        self.start_btn = ctk.CTkButton(
            btn_row, text="🚀 开始提取模型", height=40,
            fg_color=COLOR_ACCENT, hover_color=COLOR_ACCENT_HOVER,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=14, weight="bold"),
            command=self._start_extract
        )
        self.start_btn.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            btn_row, text="⏹ 停止", height=40, width=80,
            fg_color="#313244", hover_color="#45475A",
            border_width=1, border_color=COLOR_BORDER,
            font=ctk.CTkFont(family=GLOBAL_FONT[0], size=13, weight="bold"),
            command=self._stop_extract, state="disabled"
        )
        self.stop_btn.pack(side="right")

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

    # ───── 工具 ─────
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

    # ───── 路径 ─────
    def _browse(self):
        p = filedialog.askdirectory(title="选择 data.pack 所在目录")
        if p:
            # 智能解析
            if os.path.basename(p) == GAME_FOLDER_NAME:
                sub = os.path.join(p, "bin", "appdata", "cznlive")
                if os.path.isdir(sub): p = sub
            elif os.path.basename(p) == "bin":
                sub = os.path.join(p, "appdata", "cznlive")
                if os.path.isdir(sub): p = sub
            self.pack_dir.set(p)

    def _browse_out(self):
        p = filedialog.askdirectory(title="选择输出目录")
        if p: self.output_dir.set(p)

    def _auto_find(self):
        self._log("🔍 自动搜索 data.pack...")
        self.start_btn.configure(state="disabled")
        threading.Thread(target=self._auto_find_worker, daemon=True).start()

    def _auto_find_worker(self):
        found = None
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if not os.path.exists(drive): continue
            self._log(f"  扫描 {drive} ...")
            try:
                for root, dirs, files in os.walk(drive):
                    depth = root.replace(drive, '').count(os.sep)
                    if depth > 5:
                        dirs.clear()
                        continue
                    dirs[:] = [d for d in dirs if not d.startswith('.')
                               and d not in ('Windows', '$Recycle.Bin', 'System Volume Information',
                                             'ProgramData', 'Recovery', 'node_modules', '.git')]
                    if "data.pack" in files:
                        candidate = root
                        if os.path.getsize(os.path.join(root, "data.pack")) > 100_000_000:
                            found = candidate
                            self._log(f"  ✅ 找到: {found}")
                            break
                if found: break
            except PermissionError:
                continue

        if found:
            self._log(f"✅ 定位成功: {found}")
            self.after(0, lambda: self.pack_dir.set(found))
        else:
            self._log("❌ 未找到 data.pack")
            self.after(0, lambda: messagebox.showwarning("未找到", "未在任何磁盘找到 data.pack"))
        self.after(0, lambda: self.start_btn.configure(state="normal"))

    # ───── 提取 ─────
    def _start_extract(self):
        pack_dir = self.pack_dir.get().strip()
        out_dir = self.output_dir.get().strip()
        if not pack_dir:
            messagebox.showwarning("提示", "请先选择 data.pack 目录")
            return
        if not os.path.exists(os.path.join(pack_dir, "data.pack")):
            messagebox.showerror("错误", f"在 {pack_dir} 下未找到 data.pack")
            return

        self.is_running = True
        self._stop = False
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        threading.Thread(target=self._extract_worker, args=(pack_dir, out_dir), daemon=True).start()

    def _stop_extract(self):
        self._stop = True
        self._log("⚠ 正在停止...")

    def _extract_worker(self, pack_dir, out_dir):
        try:
            self._log("=" * 40)
            self._log("开始提取模型文件...")
            self._set_progress(0, "扫描文件...")

            with MultiVolumePack(pack_dir) as pack:
                vol_count = len(pack.volumes)
                total_gb = pack.total_size / 1024**3
                self._log(f"打开 {vol_count} 个分卷, 总计 {total_gb:.2f} GB")

                # 解析 PLPcK header
                hdr = pack.read_xor(0, 38)
                if hdr[:5] != b'PLPcK':
                    self._log("❌ 无效的 PLPcK 格式")
                    return
                hash_count = struct.unpack_from('<I', hdr, 21)[0]
                self._log(f"哈希桶: {hash_count:,}")

                # 收集所有文件
                self._set_progress(0.05, "扫描文件索引...")
                t0 = time.time()
                all_entries = collect_all_entries(pack, hash_count)
                self._log(f"扫描完成: {len(all_entries):,} 个文件 ({time.time()-t0:.1f}s)")

                # 根据模式过滤
                mode = self.extract_mode.get()
                if mode == "models":
                    model_ext = {'.scsp', '.sct', '.sct2', '.atlas'}
                    entries_to_extract = [
                        e for e in all_entries
                        if e['filename'].startswith('model/')
                        and any(e['filename'].endswith(ext) for ext in model_ext)
                    ]
                    self._log(f"模式: 仅模型 → {len(entries_to_extract):,} 个文件")
                else:
                    entries_to_extract = all_entries
                    total_sz = sum(e['content_size'] for e in entries_to_extract)
                    self._log(f"模式: 全量解包 → {len(entries_to_extract):,} 个文件 ({total_sz/1024**3:.2f} GB)")

                if not entries_to_extract:
                    self._log("⚠ 没有找到可提取的文件")
                    return

                # 提取
                self._set_progress(0.10, f"提取 {len(entries_to_extract)} 个文件...")
                os.makedirs(out_dir, exist_ok=True)

                extracted = 0
                failed = 0
                total = len(entries_to_extract)

                for i, entry in enumerate(entries_to_extract):
                    if self._stop:
                        self._log("⚠ 用户停止")
                        break
                    try:
                        extract_file(pack, entry, out_dir)
                        extracted += 1
                    except Exception as ex:
                        failed += 1
                        if failed <= 5:
                            self._log(f"  ❌ {entry['filename']}: {ex}")

                    if (i + 1) % 100 == 0 or i == total - 1:
                        pct = 0.10 + 0.85 * (i + 1) / total
                        self._set_progress(pct, f"提取中 {i+1}/{total}")

                self._set_progress(1.0, "完成！")
                self._log("=" * 40)
                self._log(f"✅ 提取完成!")
                self._log(f"   成功: {extracted:,}")
                self._log(f"   失败: {failed:,}")
                self._log(f"   输出: {out_dir}")
                self._log("=" * 40)

                # 提示完成
                self.after(0, lambda: messagebox.showinfo(
                    "提取完成",
                    f"成功提取 {extracted:,} 个模型文件\n\n"
                    f"输出目录:\n{out_dir}\n\n"
                    f"在 Spine Viewer 中加载 model 子目录即可查看"
                ))

        except Exception as e:
            self._log(f"❌ 错误: {e}")
            import traceback
            self._log(traceback.format_exc())
        finally:
            self.is_running = False
            self.after(0, lambda: self.start_btn.configure(state="normal"))
            self.after(0, lambda: self.stop_btn.configure(state="disabled"))


if __name__ == '__main__':
    app = ModelExtractorApp()
    app.mainloop()
