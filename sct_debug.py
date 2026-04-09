import struct, os
import texture2ddecoder
from PIL import Image

def decode_sct_raw(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    if data[:4] != b'SCT2':
        raise ValueError(f"Not SCT2: {data[:4]}")
    header_size = struct.unpack_from('<I', data, 12)[0]
    detail = struct.unpack_from('<I', data, 20)[0]
    w = struct.unpack_from('<H', data, 24)[0]
    h = struct.unpack_from('<H', data, 26)[0]
    payload = data[header_size:]
    dec_size = struct.unpack_from('<I', payload, 0)[0]
    comp_size = struct.unpack_from('<I', payload, 4)[0]
    astc_expected = ((w + 3) // 4) * ((h + 3) // 4) * 16

    if 0 < comp_size < len(payload) and 0 < dec_size < 100_000_000:
        import lz4.block
        raw = lz4.block.decompress(payload[8:8+comp_size], uncompressed_size=dec_size)
        print(f"  LZ4 mode: {len(raw)} bytes")
    elif len(payload) == astc_expected:
        raw = payload
        print(f"  Raw ASTC mode: {len(raw)} bytes")
    else:
        raise ValueError(f"payload={len(payload)}, astc_expected={astc_expected}")

    if detail == 40:
        rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    elif detail == 19:
        rgba = texture2ddecoder.decode_etc2a8(raw, w, h)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    else:
        rgba = texture2ddecoder.decode_astc(raw, w, h, 4, 4)
        img = Image.frombytes('RGBA', (w, h), rgba, 'raw', 'BGRA')
    return img

for path in [
    r"G:\keasi\unpacked\face\character\face_character_30042.sct",
    r"G:\keasi\unpacked\face\character\face_character_1071_panic.sct",
]:
    name = os.path.basename(path)
    try:
        img = decode_sct_raw(path)
        out = path.replace('.sct', '_test.png')
        img.save(out, 'PNG')
        print(f"  {name} -> OK {img.size[0]}x{img.size[1]}")
    except Exception as e:
        print(f"  {name} -> FAIL: {e}")
