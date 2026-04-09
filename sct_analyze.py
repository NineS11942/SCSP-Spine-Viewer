"""SCT/SCT2 → PNG converter"""
import struct, io, sys, os
import lz4.block
import texture2ddecoder
from PIL import Image

def decode_sct(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()

    if data[:4] != b'SCT2':
        raise ValueError(f"Not SCT2 format, magic: {data[:4]}")

    header_size = struct.unpack_from('<I', data, 12)[0]
    detail      = struct.unpack_from('<I', data, 20)[0]
    w           = struct.unpack_from('<H', data, 24)[0]
    h           = struct.unpack_from('<H', data, 26)[0]

    print(f"  Header size: {header_size}")
    print(f"  Detail/format: {detail}")
    print(f"  Dimensions: {w}x{h}")

    if w == 0 or h == 0 or w > 16384 or h > 16384:
        raise ValueError(f"Invalid dimensions: {w}x{h}")

    payload   = data[header_size:]
    dec_size  = struct.unpack_from('<I', payload, 0)[0]
    comp_size = struct.unpack_from('<I', payload, 4)[0]
    print(f"  LZ4: compressed={comp_size}, decompressed={dec_size}")
    raw = lz4.block.decompress(payload[8:8+comp_size], uncompressed_size=dec_size)
    print(f"  Raw pixel data: {len(raw)} bytes")

    if detail == 40:
        print("  Format: ASTC 4x4")
        rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 47:
        print("  Format: ASTC 8x8")
        rgba = texture2ddecoder.decode_astc(raw, w, h, 8, 8)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 19:
        print("  Format: ETC2 RGBA8")
        rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 44:
        print("  Format: Raw RGBA")
        img = Image.frombytes('RGBA', (w, h), raw[:w*h*4])
    else:
        print(f"  Format: Unknown ({detail}), auto-detecting by data size...")
        img = None
        for bw, bh in [(4,4),(5,5),(6,6),(8,8),(10,10),(12,12)]:
            expected = ((w+bw-1)//bw) * ((h+bh-1)//bh) * 16
            if expected == len(raw):
                print(f"  → Matched ASTC {bw}x{bh}")
                try:
                    rgba = texture2ddecoder.decode_astc(raw, w, h, bw, bh)
                    img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
                    break
                except:
                    continue
        if img is None:
            print("  → Trying ETC2A8 fallback...")
            try:
                rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
                img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
            except:
                rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
                img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')

    return img


filepath = r"G:\keasi\unpacked\item\relics\relic_1061.sct"
print(f"Converting: {filepath}")
img = decode_sct(filepath)

out_path = os.path.splitext(filepath)[0] + ".png"
img.save(out_path, "PNG")
print(f"\n✅ Saved: {out_path}")
print(f"   Size: {img.size[0]}x{img.size[1]}")
