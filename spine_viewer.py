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
from scsp_decoder import decompress_scsp, BinaryReader, ScspParser, numpy_to_python, clean_float

app = Flask(__name__)

# 缓存
_json_cache = {}
_model_dir = None

# ──── SCT2 纹理解码 ────

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

    # ── LZ4 decompress payload ──
    payload   = data[header_size:]
    dec_size  = struct.unpack_from('<I', payload, 0)[0]
    comp_size = struct.unpack_from('<I', payload, 4)[0]
    raw = lz4.block.decompress(payload[8:8+comp_size], uncompressed_size=dec_size)

    # ── Decode GPU texture based on detail field ──
    if detail == 40:
        # ASTC 4×4 compressed
        rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 19:
        # ETC2 RGBA8 (EAC) compressed
        rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 44:
        # Raw RGBA (4 bytes per pixel)
        if len(raw) >= w * h * 4:
            img = Image.frombytes('RGBA', (w, h), raw[:w*h*4])
        else:
            raise ValueError(f"detail=44 raw data too short: {len(raw)} < {w*h*4}")
    else:
        # Unknown detail — try ASTC 4x4 first, then ETC2A8
        try:
            rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
            img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
        except Exception:
            rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
            img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')

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


@app.route('/api/set_folder', methods=['POST'])
def set_folder():
    global _model_dir, _json_cache
    data = request.json
    folder = data.get('folder', '').strip()
    if not os.path.isdir(folder):
        return jsonify({"error": f"Folder not found: {folder}"}), 400
    _model_dir = folder
    _json_cache = {}
    return jsonify({"ok": True, "folder": folder})


@app.route('/api/list')
def list_models():
    if not _model_dir:
        return jsonify({"models": [], "error": "No folder set"})
    
    scsp_files = glob.glob(os.path.join(_model_dir, "*.scsp"))
    models = []
    for f in sorted(scsp_files):
        name = os.path.splitext(os.path.basename(f))[0]
        atlas_exists = os.path.exists(os.path.join(_model_dir, f"{name}.atlas"))
        tex_exists = (os.path.exists(os.path.join(_model_dir, f"{name}.sct")) or
                      os.path.exists(os.path.join(_model_dir, f"{name}.sct2")) or
                      os.path.exists(os.path.join(_model_dir, f"{name}.png")))
        models.append({
            "name": name,
            "hasAtlas": atlas_exists,
            "hasTexture": tex_exists,
        })
    return jsonify({"models": models, "folder": _model_dir})


@app.route('/api/skeleton/<name>')
def get_skeleton(name):
    """解码 SCSP 并返回 Spine JSON"""
    if not _model_dir:
        return jsonify({"error": "No folder set"}), 400
    
    if name in _json_cache:
        return Response(json.dumps(_json_cache[name]), mimetype='application/json')
    
    scsp_path = os.path.join(_model_dir, f"{name}.scsp")
    if not os.path.exists(scsp_path):
        return jsonify({"error": f"File not found: {name}.scsp"}), 404
    
    try:
        raw = decompress_scsp(scsp_path)
        reader = BinaryReader(raw)
        parser = ScspParser(reader)
        result = parser.parse()
        result = numpy_to_python(result)
        _json_cache[name] = result
        return Response(json.dumps(result), mimetype='application/json')
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route('/api/atlas/<name>')
def get_atlas(name):
    if not _model_dir:
        return "No folder set", 400
    atlas_path = os.path.join(_model_dir, f"{name}.atlas")
    if not os.path.exists(atlas_path):
        return "Atlas not found", 404
    
    # 读取 atlas 并修改纹理引用为我们的 API URL
    with open(atlas_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    # atlas 文件第一行是纹理文件名，替换为我们的 API
    lines = content.split('\n')
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.endswith('.png') or stripped.endswith('.sct') or stripped.endswith('.sct2'):
            # 纹理引用行 — 替换为我们的 texture API
            tex_name = os.path.splitext(stripped)[0]
            new_lines.append(f"{tex_name}.png")
        else:
            new_lines.append(line)
    
    return Response('\n'.join(new_lines), mimetype='text/plain')


@app.route('/api/texture/<name>.png')
def get_texture(name):
    if not _model_dir:
        return "No folder set", 400
    
    # 优先找 PNG
    png_path = os.path.join(_model_dir, f"{name}.png")
    if os.path.exists(png_path):
        return send_file(png_path, mimetype='image/png')
    
    # 然后找 SCT/SCT2
    for ext in ['.sct', '.sct2']:
        sct_path = os.path.join(_model_dir, f"{name}{ext}")
        if os.path.exists(sct_path):
            try:
                png_data = decode_sct2(sct_path)
                return Response(png_data, mimetype='image/png')
            except Exception as e:
                return f"Texture decode error: {e}", 500
    
    return "Texture not found", 404


def _extractor_process():
    """在独立进程中运行模型解包 GUI"""
    import model_extractor
    gui = model_extractor.ModelExtractorApp()
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
            print("⚠ 检测到旧程序还在运行，已自动结束")
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
