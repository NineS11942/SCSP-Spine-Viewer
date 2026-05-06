# ⚡ SCSP Spine Viewer

**Yuna Engine 自定义 Spine 动画 Web 查看器**

实时解码并渲染游戏引擎的私有格式骨骼动画资源（`.scsp` + `.sct`/`.sct2` + `.atlas`）。
支持 **SCSP V0**（Spine 3.8）和 **SCSP V1**（Spine 2.1.27 → 3.8 自动转换）两种二进制格式。

---

## 📸 效果预览

![预览1](images/preview1.png)
![预览2](images/preview2.png)

---

## 🚀 普通用户（直接运行）

### 下载

从 [Releases](../../releases) 下载最新版本压缩包。

### 使用方法

1. 下载 `SCSP_Spine_Viewer_vX.X.zip`
2. 解压到任意文件夹
3. 双击运行 `SCSP_Spine_Viewer.exe`
4. 浏览器会自动打开（或手动访问 `http://localhost:5000`）
5. 在顶部输入解包后的模型文件夹路径（如 `G:\keasi\unpacked\model`），点击 **加载**
6. 左侧列表选择模型，右侧实时预览动画

### 快捷键

| 快捷键 | 功能 |
|--------|------|
| `空格` | 播放 / 暂停 |
| `↑` `↓` | 切换动画 |
| `鼠标滚轮` | 缩放模型 |

---

## 🛠 开发者（源码运行）

### 环境要求

- Python 3.10+
- 依赖安装：

```bash
pip install flask lz4 pillow numpy texture2ddecoder
```

### 运行

```bash
python spine_viewer.py
```

浏览器访问 `http://localhost:5000`

### 打包为 EXE

运行 `N_build.bat`，使用 pyinstaller打包 编译为单文件可执行程序。

---

## 📁 项目结构

```
SCSP-Spine-Viewer/
├── spine_viewer.py        ← Flask 主程序入口 (API + SCT 解码)
├── index.html             ← 前端页面 (PixiJS 5 + pixi-spine 2.x)
├── N_build.bat            ← PyInstaller 打包脚本
├── release.bat            ← GitHub Release 发布脚本
├── CHANGELOG.md           ← 版本更新日志
├── v0/                    ← 解码器模块
│   ├── scsp_decoder.py    ← V0 解析器 (Spine 3.8 二进制)
│   ├── scsp_v1_decoder.py ← V1 解析入口 + 2.1→3.8 JSON 转换
│   └── model_extractor.py ← 模型解包 GUI (data.pack 提取)
└── v1/                    ← E7 核心解析库
    ├── scsp_common.py     ← 共享基础设施 (数据类/Reader/常量)
    ├── scsp_v2.py         ← V2 二进制解析 (Spine 2.1.27)
    ├── scsp_v3.py         ← V3 二进制解析 (Spine 3.8.99)
    └── scsp2json.py       ← 调度 + JSON 生成
```

---

## ✨ 功能特性

- 🦴 **双版本 SCSP 解析**：V0 (Spine 3.8 原生) + V1 (Spine 2.1.27 → 3.8 自动转换)
- 🎨 自动解码 SCT/SCT2 GPU 压缩纹理（ASTC 4×4/8×8 / ETC2 RGBA8 / Raw RGBA / RGB565+Alpha）
- 🎬 浏览器内 PixiJS + pixi-spine 实时动画播放
- 🔍 支持 1000+ 模型快速搜索、切换、预览
- 📂 **递归子文件夹扫描**：自动发现嵌套目录中的模型文件
- 🔎 **鼠标滚轮缩放**：交互式查看模型细节
- 🛡 前端 JSON sanitizer 自动容错（空帧/乱码动画/Deform 异常）

---

## 🔧 技术细节

### SCSP V0 骨骼格式 (Spine 3.8)

```
SCSP Header → LZ4 压缩 → 自定义二进制结构
  ├─ Skeleton 元数据 (版本/宽高/FPS)
  ├─ Bones / Slots / IK / Transform / Path
  ├─ Skins (附件：Region / Mesh / BoundingBox)
  ├─ Events
  └─ Animations (骨骼/插槽/约束/绘制顺序)
```

### SCSP V1 骨骼格式 (Spine 2.1.27)

```
SCSP Header (magic="scsp") → LZ4 压缩 → V2 二进制结构
  ├─ Skeleton 元数据 + 字符串池
  ├─ Bones / IK / Slots (u32 字段，非 varint)
  ├─ Skins (Region / Mesh / SkinnedMesh / BoundingBox)
  ├─ Events
  ├─ Animations (数据流扫描 + 字符串池匹配定位)
  └─ DrawOrder (预计算排列数组 → 逾向推导)
→ 输出 Spine 2.1.27 JSON → 自动转换为 3.8.99 格式:
  ├─ skins: dict → list
  ├─ skinnedmesh → mesh
  ├─ flipX/Y → scaleX/Y, inheritScale/Rotation → transform
  └─ ffd → deform
```

### SCT2 纹理格式

```
SCT2 Header (72 bytes)
  ├─ Magic: "SCT2"
  ├─ detail 字段 → 压缩格式
  │   ├─ 40: ASTC 4×4
  │   ├─ 47: ASTC 8×8
  │   ├─ 19: ETC2 RGBA8
  │   └─ 44: Raw RGBA
  ├─ Width/Height: uint16 @ offset 24/26
  └─ Payload: LZ4(GPU纹理) → BGRA → RGBA
```

### SCT v1 纹理格式

```
SCT v1 Header (17 bytes)
  ├─ Magic: "SCT" + version(1B)
  ├─ Width/Height: uint16
  └─ Payload: LZ4 → 3平面 (RGB565 ×2 + Alpha 8bit)
```

---

## ⚠ 已知限制

- V1 的 Deform/FFD 动画在转换为 3.8 后可能存在边缘案例
- 部分模型动画名可能出现乱码，自动过滤
- 极少数复杂模型 fallback 到静态姿态显示

> ℹ 后端解析本身没有问题，以上限制均来自前端 pixi-spine 兼容性适配

---

## ⚠ 免责声明

- 本项目仅供**学习研究与技术交流**使用，不得用于任何商业用途
- 本工具不包含、不分发任何游戏资源文件，所有资源需用户自行从本地客户端获取
- 本项目与游戏官方无任何关联，所有游戏资源版权归原公司所有
- **如有侵权，请联系删除**，收到通知后将第一时间配合处理

---

## 📄 License

[MIT License](LICENSE)

---

## 🙏 致谢

- V0 引擎格式分析基于 IDA Pro 逆向工程
- **V1 解析器核心基于 [E7SpineAssetConverter](https://github.com/gamefreak130/E7SpineAssetConverter)**（第七史诗 SCSP 解析）
- Spine 运行时：[pixi-spine](https://github.com/nicknelson/pixi-spine) + [PixiJS](https://pixijs.com/)
- 纹理解码：[texture2ddecoder](https://github.com/nicknelson/texture2ddecoder)

> ⭐ 如果这个工具对你有帮助，请给个 Star！
