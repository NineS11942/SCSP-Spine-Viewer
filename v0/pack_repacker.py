#!/usr/bin/env python3
"""
pack_repacker.py — PLPcK 封包工具 (精简版)

功能：选择一个文件夹，将其中所有文件打包为 PLPcK 格式的 .pack 文件。
支持两种模式:
  1. 基于原始 pack 替换 (--from-original): 从原始 .pack 提取条目，用文件夹中同名文件覆盖
  2. 纯文件夹构建: 将文件夹所有内容直接打包

独立模块，不依赖外部项目。
"""
import struct
import os
import sys
import time
import numpy as np


# ═══════════════════════════════════════════════════════════════════════
# Pack XOR 密钥
# ═══════════════════════════════════════════════════════════════════════
def generate_pack_xor_key():
    """Yuna Engine PLPcK XOR key: seed=150812, LCG(1103515245), 129 bytes."""
    seed = 150812
    key = bytearray(129)
    for i in range(129):
        seed = (seed * 1103515245) & 0xFFFFFFFF
        key[i] = (seed >> 16) & 0xFF
    return bytes(key)

PACK_XOR_KEY = generate_pack_xor_key()
_PACK_XOR_NP = np.frombuffer(PACK_XOR_KEY, dtype=np.uint8)


def _fast_xor(data: bytes, offset: int) -> bytes:
    """XOR data with the repeating 129-byte key starting at file offset."""
    n = len(data)
    if n == 0:
        return data
    arr = np.frombuffer(data, dtype=np.uint8).copy()
    key_len = len(_PACK_XOR_NP)
    start_phase = offset % key_len
    repeats = (n + start_phase + key_len - 1) // key_len + 1
    key_stream = np.tile(_PACK_XOR_NP, repeats)[start_phase:start_phase + n]
    arr ^= key_stream
    return arr.tobytes()


def pack_xor(data: bytes, file_offset: int) -> bytes:
    """XOR encrypt/decrypt (symmetric)."""
    return _fast_xor(data, file_offset)


# ═══════════════════════════════════════════════════════════════════════
# PLPcK 哈希函数
# ═══════════════════════════════════════════════════════════════════════
def cdbm_hash(key_bytes: bytes) -> int:
    """Yuna Engine custom hash: lowercase letters, h = ch + 43*h."""
    h = 0
    for b in key_bytes:
        ch = b + 32 if 65 <= b <= 90 else b
        h = (ch + 43 * h) & 0xFFFFFFFF
    return h


# ═══════════════════════════════════════════════════════════════════════
# 从原始 .pack 提取全部条目
# ═══════════════════════════════════════════════════════════════════════
def extract_entries(pack_path):
    """
    从 PLPcK .pack 文件提取全部条目。
    返回 (entries, header_38, ver_5, hash_count, trailing_data)
    """
    with open(pack_path, 'rb') as f:
        raw_all = f.read()
    total_size = len(raw_all)

    dec_all = pack_xor(raw_all, 0)

    magic = dec_all[:5]
    if magic != b'PLPcK':
        raise ValueError(f"不是 PLPcK 格式! Magic: {magic}")

    hash_count = struct.unpack_from('<I', dec_all, 21)[0]
    header_38 = dec_all[:38]
    ver_5 = dec_all[38:43]

    ht_start = 43
    entries = []
    seen = set()
    max_end = ht_start + hash_count * 5

    for bi in range(hash_count):
        off = ht_start + bi * 5
        ptr_hi = dec_all[off]
        ptr_lo = struct.unpack_from('<I', dec_all, off + 1)[0]
        chain = ptr_lo + (ptr_hi << 32)
        if chain == 0:
            continue

        safety = 0
        while chain > 0 and chain + 15 <= total_size and safety < 1000:
            safety += 1
            if chain in seen:
                break
            seen.add(chain)

            data_size = struct.unpack_from('<I', dec_all, chain)[0]
            flags = dec_all[chain + 4]
            key_length = dec_all[chain + 5]
            value_size = struct.unpack_from('<I', dec_all, chain + 6)[0]
            next_hi = dec_all[chain + 10]
            next_lo = struct.unpack_from('<I', dec_all, chain + 11)[0]
            next_ptr = next_lo + (next_hi << 32)

            if data_size == 0 or key_length == 0:
                break

            cdata_off = chain + 15
            key_data = dec_all[cdata_off:cdata_off + key_length]
            value_data = dec_all[cdata_off + key_length:cdata_off + key_length + value_size]

            meta_size = data_size - key_length - value_size - 15
            meta_data = b''
            if meta_size > 0:
                meta_data = dec_all[cdata_off + key_length + value_size:
                                    cdata_off + key_length + value_size + meta_size]

            chunk_end = chain + 15 + key_length + value_size + max(0, meta_size)
            if chunk_end > max_end:
                max_end = chunk_end

            entries.append({
                'key': key_data,
                'value': value_data,
                'flags': flags,
                'meta': meta_data,
            })

            if next_ptr == 0 or next_ptr == chain:
                break
            chain = next_ptr

    trailing_data = dec_all[max_end:] if max_end < total_size else b''
    return entries, header_38, ver_5, hash_count, trailing_data


# ═══════════════════════════════════════════════════════════════════════
# 从文件夹收集文件
# ═══════════════════════════════════════════════════════════════════════
def collect_files(folder_path):
    """递归扫描文件夹，返回 [(relative_key, content_bytes), ...]"""
    file_list = []
    folder_path = os.path.normpath(folder_path)
    skip_prefixes = ('.vscode/', '.git/', '__pycache__/')
    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            full_path = os.path.join(root, fname)
            rel_path = os.path.relpath(full_path, folder_path).replace('\\', '/')
            if any(rel_path.startswith(p) for p in skip_prefixes):
                continue
            with open(full_path, 'rb') as f:
                content = f.read()
            file_list.append((rel_path, content))
    return file_list


# ═══════════════════════════════════════════════════════════════════════
# 构建 PLPcK pack
# ═══════════════════════════════════════════════════════════════════════
def build_pack(entries, header_38, ver_5, hash_count, output_path, trailing_data=b'',
               progress_callback=None):
    """
    将条目列表构建为 PLPcK 格式文件。
    
    Args:
        entries: list of dict with keys: key(bytes), value(bytes), flags(int), meta(bytes)
        header_38: 38-byte PLPcK header
        ver_5: 5-byte version block
        hash_count: hash table bucket count
        output_path: output file path
        trailing_data: optional trailing bytes
        progress_callback: optional fn(progress_float, status_text)
    """
    def _progress(val, text):
        if progress_callback:
            progress_callback(val, text)

    _progress(0.1, f"分桶计算 ({len(entries)} 条目)...")

    # Pass 1: 分桶 + 预计算偏移
    buckets = {}
    for i, entry in enumerate(entries):
        bucket = cdbm_hash(entry['key']) % hash_count
        buckets.setdefault(bucket, []).append(i)

    fixed_area = 38 + 5 + hash_count * 5
    hash_table = bytearray(hash_count * 5)
    chunk_plan = []
    running_offset = fixed_area

    for bi in sorted(buckets.keys()):
        entry_indices = buckets[bi]
        first_offset = running_offset

        ht_off = bi * 5
        hash_table[ht_off] = (first_offset >> 32) & 0xFF
        struct.pack_into('<I', hash_table, ht_off + 1, first_offset & 0xFFFFFFFF)

        for ci, ei in enumerate(entry_indices):
            entry = entries[ei]
            chunk_total = 15 + len(entry['key']) + len(entry['value']) + len(entry['meta'])
            current = running_offset
            next_off = (current + chunk_total) if ci < len(entry_indices) - 1 else 0
            chunk_plan.append((ei, current, next_off))
            running_offset += chunk_total

    total_size = running_offset + len(trailing_data)
    _progress(0.3, f"组装明文 ({total_size / 1024 / 1024:.1f} MB)...")

    # Pass 2: 组装明文
    plaintext = bytearray()
    plaintext.extend(header_38)
    plaintext.extend(ver_5)
    plaintext.extend(hash_table)

    for ei, expected_offset, next_off in chunk_plan:
        entry = entries[ei]
        kd = entry['key']
        vd = entry['value']
        md = entry['meta']
        kl = len(kd)
        vs = len(vd)
        ms = len(md)

        hdr = bytearray(15)
        struct.pack_into('<I', hdr, 0, kl + vs + 15 + ms)
        hdr[4] = entry['flags']
        hdr[5] = kl & 0xFF
        struct.pack_into('<I', hdr, 6, vs)
        hdr[10] = (next_off >> 32) & 0xFF
        struct.pack_into('<I', hdr, 11, next_off & 0xFFFFFFFF)

        plaintext.extend(hdr)
        plaintext.extend(kd)
        plaintext.extend(vd)
        if ms > 0:
            plaintext.extend(md)

    if trailing_data:
        plaintext.extend(trailing_data)

    _progress(0.7, "XOR 加密...")

    # Pass 3: XOR 加密
    encrypted = pack_xor(bytes(plaintext), 0)

    # 验证
    verify_head = pack_xor(encrypted[:5], 0)
    if verify_head != b'PLPcK':
        raise RuntimeError(f"加密验证失败: {verify_head}")

    _progress(0.9, "写入文件...")

    # Pass 4: 写出
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'wb') as f:
        f.write(encrypted)

    _progress(1.0, "完成!")
    return total_size


# ═══════════════════════════════════════════════════════════════════════
# 高层封装：选择文件夹 → 基于原始 pack 替换 → 输出
# ═══════════════════════════════════════════════════════════════════════
def repack_from_folder(original_pack_path, folder_path, output_path,
                       progress_callback=None, log_callback=None):
    """
    核心封包流程：基于原始 .pack，用文件夹内容替换同名条目后重建。
    
    Args:
        original_pack_path: 原始 .pack 文件路径
        folder_path: 替换文件所在文件夹
        output_path: 输出 .pack 路径
        progress_callback: fn(float, str) 进度回调
        log_callback: fn(str) 日志回调
    
    Returns:
        (total_size, replaced_count, new_count)
    """
    def _log(msg):
        if log_callback:
            log_callback(msg)
        else:
            print(msg)

    def _progress(val, text):
        if progress_callback:
            progress_callback(val, text)

    _log(f"读取原始 pack: {original_pack_path}")
    _progress(0.05, "提取原始 pack...")
    entries, header_38, ver_5, hash_count, trailing_data = extract_entries(original_pack_path)
    _log(f"  提取 {len(entries)} 个条目, hash_count={hash_count}")

    _log(f"扫描文件夹: {folder_path}")
    _progress(0.2, "扫描文件夹...")
    folder_files = collect_files(folder_path)
    _log(f"  找到 {len(folder_files)} 个文件")

    folder_map = {key.encode('utf-8'): content for key, content in folder_files}

    # 替换同名条目
    replaced = 0
    for entry in entries:
        if entry['key'] in folder_map:
            old_size = len(entry['value'])
            entry['value'] = folder_map[entry['key']]
            new_size = len(entry['value'])
            try:
                kn = entry['key'].decode('utf-8')
            except Exception:
                kn = entry['key'].hex()
            _log(f"  替换: {kn} ({old_size:,} → {new_size:,} bytes)")
            replaced += 1

    # 新增条目（文件夹中有但 pack 中没有的）
    pack_keys = {e['key'] for e in entries}
    new_count = 0
    for key_bytes, content in folder_map.items():
        if key_bytes not in pack_keys:
            try:
                kn = key_bytes.decode('utf-8')
            except Exception:
                kn = key_bytes.hex()
            _log(f"  新增: {kn} ({len(content):,} bytes)")
            entries.append({
                'key': key_bytes,
                'value': content,
                'flags': 2,
                'meta': b'',
            })
            new_count += 1

    _log(f"替换 {replaced} 个, 新增 {new_count} 个")

    _progress(0.4, "构建 pack...")
    total_size = build_pack(entries, header_38, ver_5, hash_count, output_path,
                            trailing_data, progress_callback)
    _log(f"✅ 输出: {output_path} ({total_size / 1024 / 1024:.1f} MB)")
    return total_size, replaced, new_count
