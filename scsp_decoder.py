"""
SCSP → Spine JSON 完整解码器 (含动画)
基于 IDA 逆向分析 + 二进制数据格式分析

SCSP 格式 (Yuna Engine, ChaosZero Nightmare):
- 文件: [4B dec_size][4B comp_verify][LZ4 data]
- 解压后: [SCSP Header][Spine Data][String Pool]
- 所有整数: Little-Endian
- 字符串: 通过 uint32 偏移引用末尾的字符串常量池
- 颜色: 4x float32 (RGBA, 0~1)
"""
import struct
import os
import sys
import json
import math
import numpy as np
import lz4.block
from collections import defaultdict


class ScspOffsets:
    """SCSP 头部固定偏移"""
    HEADER_WIDTH    = 22
    HEADER_HEIGHT   = 26
    IK_COUNT        = 54
    SLOTS_COUNT     = 58
    TRANSFORM_COUNT = 62
    PATH_COUNT      = 66
    SKINS_COUNT     = 70
    EVENTS_COUNT    = 74
    ANIMATIONS_COUNT = 78
    HASH_PTR        = 82
    SPINE_PTR       = 86
    BONES_COUNT     = 106


class BinaryReader:
    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.strings_offset = struct.unpack_from('<I', data, 0)[0] + 8
        self.strings_length = struct.unpack_from('<I', data, 4)[0]
        self.strings_data = data[self.strings_offset:self.strings_offset + self.strings_length]
    
    def seek(self, pos):
        self.pos = pos
    
    def skip(self, n):
        self.pos += n
    
    def int8(self, read_pos=-1):
        p = read_pos if read_pos != -1 else self.pos
        v = struct.unpack_from('<b', self.data, p)[0]
        if read_pos == -1:
            self.pos = p + 1
        return v
    
    def uint8(self, read_pos=-1):
        p = read_pos if read_pos != -1 else self.pos
        v = self.data[p]
        if read_pos == -1:
            self.pos = p + 1
        return v
    
    def int16(self, read_pos=-1, peek=False):
        p = read_pos if read_pos != -1 else self.pos
        v = struct.unpack_from('<h', self.data, p)[0]
        if not peek:
            self.pos = p + 2
        return v
    
    def uint32(self, read_pos=-1, peek=False):
        p = read_pos if read_pos != -1 else self.pos
        v = struct.unpack_from('<I', self.data, p)[0]
        if not peek:
            self.pos = p + 4
        return v
    
    def float32(self, read_pos=-1, peek=False):
        p = read_pos if read_pos != -1 else self.pos
        v = struct.unpack_from('<f', self.data, p)[0]
        if not peek:
            self.pos = p + 4
        return clean_float(v)
    
    def bool8(self, read_pos=-1):
        p = read_pos if read_pos != -1 else self.pos
        v = self.data[p]
        if read_pos == -1:
            self.pos = p + 1
        if v == 0xFF:
            return None
        return v == 1
    
    def bool16(self, read_pos=-1):
        p = read_pos if read_pos != -1 else self.pos
        v = struct.unpack_from('<h', self.data, p)[0]
        if read_pos == -1:
            self.pos = p + 2
        return v == 1
    
    def bool_float32(self, read_pos=-1):
        p = read_pos if read_pos != -1 else self.pos
        f = self.float32(p)
        if f == -1:
            return False
        return f == 1
    
    def string(self, read_pos=-1, peek=False):
        offset = self.uint32(read_pos, peek)
        return self.get_string(offset)
    
    def get_string(self, offset_in_strings):
        if offset_in_strings >= len(self.strings_data):
            return ""
        end = self.strings_data.find(b'\x00', offset_in_strings)
        if end == -1:
            return self.strings_data[offset_in_strings:].decode('utf-8', errors='ignore')
        return self.strings_data[offset_in_strings:end].decode('utf-8', errors='ignore')
    
    def color(self, need_alpha=True):
        r = self.float32()
        g = self.float32()
        b = self.float32()
        if need_alpha:
            a = self.float32()
            return ''.join(f"{int(max(0,min(255,c*255))):02X}" for c in (r, g, b, a))
        return ''.join(f"{int(max(0,min(255,c*255))):02X}" for c in (r, g, b))


def clean_float(value, precision=6):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return 0
    if value == int(value):
        return int(value)
    formatted = f"{value:.{precision}f}".rstrip('0').rstrip('.')
    try:
        return float(formatted) if '.' in formatted else int(formatted)
    except:
        return value


def decompress_scsp(filepath):
    with open(filepath, 'rb') as f:
        data = f.read()
    dec_size = struct.unpack_from('<I', data, 0)[0]
    return lz4.block.decompress(data[8:], uncompressed_size=dec_size)


class ScspParser:
    def __init__(self, reader):
        self.reader = reader
        self.bones_lookup = {}
        self.slots_lookup = {}
        self.ik_lookup = {}
        self.transform_lookup = {}
        self.path_lookup = {}
        self.events_lookup = {}
        self.skins_lookup = {}
        self.skins = []
    
    # ─── 贝塞尔曲线解析 ─────────────────────────────────
    
    @staticmethod
    def hex_curve(hex_data):
        floats = np.frombuffer(hex_data, dtype=np.dtype('<f4'))
        return floats.reshape(-1, 2)
    
    def calculate_curve_params(self, points, curve_type_hex=None):
        if curve_type_hex:
            if curve_type_hex == b'\x00\x00\x80\x3f':  # 1.0 = stepped
                return {"curve": "stepped"}
            elif curve_type_hex == b'\x00\x00\x00\x00':  # 0.0 = linear
                return {"curve": 0, "c2": 0, "c3": 1, "c4": 1}
        
        if len(points) != 9:
            return None
        
        xs = np.array([p[0] for p in points])
        ys = np.array([p[1] for p in points])
        t_values = np.linspace(0.1, 0.9, 9)
        
        A = np.zeros((9, 2))
        for i, t in enumerate(t_values):
            A[i, 0] = 3 * (1 - t)**2 * t
            A[i, 1] = 3 * (1 - t) * t**2
        
        try:
            cx1, cx2 = np.linalg.lstsq(A, xs - t_values**3, rcond=None)[0]
            cy1, cy2 = np.linalg.lstsq(A, ys - t_values**3, rcond=None)[0]
        except:
            return None
        
        cx1 = np.clip(cx1, 0.0, 1.0)
        cx2 = np.clip(cx2, 0.0, 1.0)
        cy1 = np.clip(cy1, 0.0, 1.0)
        cy2 = np.clip(cy2, 0.0, 1.0)
        
        return {
            "curve": clean_float(float(cx1), 6),
            "c2": clean_float(float(cy1), 6),
            "c3": clean_float(float(cx2), 6),
            "c4": clean_float(float(cy2), 6),
        }
    
    # ─── Skeleton / Bones / Slots / Constraints ──────────
    
    def parse_skeleton(self):
        r = self.reader
        width = r.float32(ScspOffsets.HEADER_WIDTH)
        height = r.float32(ScspOffsets.HEADER_HEIGHT)
        hash_str = r.string(ScspOffsets.HASH_PTR)
        spine_ver = r.string(ScspOffsets.SPINE_PTR)
        return {
            "hash": hash_str, "spine": spine_ver,
            "x": 0, "y": 0,
            "width": round(width, 2) if isinstance(width, float) else width,
            "height": round(height, 2) if isinstance(height, float) else height,
        }
    
    def parse_bones(self):
        r = self.reader
        count = r.int16(ScspOffsets.BONES_COUNT)
        bones = []
        for i in range(count):
            bone_id = r.int16()
            name = r.string()
            self.bones_lookup[i] = name
            parent_id = r.int16()
            length = r.float32()
            x = r.float32()
            y = r.float32()
            rotation = r.float32()
            scale_x = r.float32()
            scale_y = r.float32()
            shear_x = r.float32()
            shear_y = r.float32()
            transform_mode = r.int8()
            skin_required = r.bool8()
            r.skip(1)
            
            bone = {"name": name}
            if parent_id != -1:
                bone["parent"] = self.bones_lookup.get(parent_id, "root")
            if abs(float(length)) > 0.001: bone["length"] = length
            if abs(float(x)) > 0.001: bone["x"] = x
            if abs(float(y)) > 0.001: bone["y"] = y
            if abs(float(rotation)) > 0.001: bone["rotation"] = rotation
            if abs(float(scale_x) - 1.0) > 0.001: bone["scaleX"] = scale_x
            if abs(float(scale_y) - 1.0) > 0.001: bone["scaleY"] = scale_y
            if abs(float(shear_x)) > 0.001: bone["shearX"] = shear_x
            if abs(float(shear_y)) > 0.001: bone["shearY"] = shear_y
            
            mode_map = {0:"normal",1:"onlyTranslation",2:"noRotationOrReflection",3:"noScale",4:"noScaleOrReflection"}
            bone["transform"] = mode_map.get(transform_mode, "normal")
            if skin_required:
                bone["skin"] = True
            bones.append(bone)
        r.skip(2)
        return bones
    
    def parse_ik(self):
        r = self.reader
        count = r.int16(ScspOffsets.IK_COUNT, True)
        iks = []
        for i in range(count):
            name = r.string()
            self.ik_lookup[i] = name
            order = r.int16()
            r.skip(3)
            bend = r.bool16()
            r.skip(2)
            compress = r.bool16()
            r.skip(7)
            stretch = r.bool16()
            target_id = r.int16()
            target = self.bones_lookup.get(target_id, "")
            bone_count = r.int16()
            bones = [self.bones_lookup.get(r.int16(), "") for _ in range(bone_count)]
            ik = {"name": name, "order": order, "bones": bones, "target": target, "mix": 1, "softness": 0}
            if bend is not None: ik["bendPositive"] = bend
            if compress: ik["compress"] = compress
            if stretch: ik["stretch"] = stretch
            iks.append(ik)
        return iks
    
    def parse_slots(self):
        r = self.reader
        count = r.int16(ScspOffsets.SLOTS_COUNT, True)
        r.skip(2)
        slots = []
        for i in range(count):
            r.int16()
            name = r.string()
            bone_id = r.int16()
            self.slots_lookup[i] = name
            color = r.color()
            dark = r.color()
            r.skip(1)
            attachment = r.string()
            blend = r.int16()
            
            slot = {"name": name, "bone": self.bones_lookup.get(bone_id, "root")}
            if color != "FFFFFFFF": slot["color"] = color
            if dark != "FFFFFFFF" and dark != "00000000":
                slot["dark"] = dark[:-2] if dark.endswith("FF") else dark
            if attachment: slot["attachment"] = attachment
            if blend > 0:
                slot["blend"] = {1:"additive",2:"multiply",3:"screen"}.get(blend, "normal")
            slots.append(slot)
        return slots
    
    def parse_transform(self):
        r = self.reader
        count = r.int16()
        transforms = []
        for i in range(count):
            name = r.string()
            self.transform_lookup[i] = name
            order = r.int16()
            skin = r.bool8()
            r.skip(2)
            rotateMix = r.float32(); translateMix = r.float32()
            scaleMix = r.float32(); shearMix = r.float32()
            rotation = r.float32(); x = r.float32(); y = r.float32()
            scaleX = r.float32(); scaleY = r.float32(); shearY = r.float32()
            relative = r.bool8(); local = r.bool8()
            target_id = r.int16()
            bone_count = r.int16()
            bones = [self.bones_lookup.get(r.int16(), "") for _ in range(bone_count)]
            transforms.append({
                "name": name, "order": order, "bones": bones,
                "target": self.bones_lookup.get(target_id, ""),
                "rotation": rotation, "x": x, "y": y,
                "scaleX": scaleX, "scaleY": scaleY, "shearY": shearY,
                "rotateMix": rotateMix, "translateMix": translateMix,
                "scaleMix": scaleMix, "shearMix": shearMix,
                "local": local, "relative": relative,
            })
        return transforms
    
    def parse_path(self):
        r = self.reader
        count = r.int16()
        paths = []
        for i in range(count):
            name = r.string()
            self.path_lookup[i] = name
            order = r.int16()
            skin = r.bool8(); r.skip(2)
            pos_mode = "fixed" if r.int16() == 0 else "percent"
            spacing_mode = {0:"length",1:"fixed",2:"percent",3:"proportional"}.get(r.int16(), "length")
            rotate_mode = {0:"tangent",1:"chain",2:"chainScale"}.get(r.int16(), "tangent")
            rotation = r.float32(); position = r.float32(); spacing = r.float32()
            rotateMix = r.float32(); translateMix = r.float32()
            target_slot_id = r.int16()
            bone_count = r.int16()
            bones = [self.bones_lookup.get(r.int16(), "") for _ in range(bone_count)]
            paths.append({
                "name": name, "order": order, "bones": bones,
                "target": self.slots_lookup.get(target_slot_id, ""),
                "positionMode": pos_mode, "spacingMode": spacing_mode, "rotateMode": rotate_mode,
                "rotation": rotation, "position": position, "spacing": spacing,
                "rotateMix": rotateMix, "translateMix": translateMix,
            })
        return paths
    
    def parse_events(self):
        r = self.reader
        count = r.int16(ScspOffsets.EVENTS_COUNT, True)
        r.skip(2)
        events = {}
        for i in range(count):
            name = r.string()
            int_val = r.int16(); float_val = r.float32(); r.skip(2)
            string_val = r.string(); audio = r.string()
            event = {"int": int_val, "float": float_val, "string": string_val}
            if audio:
                event["audio"] = audio
                event["volume"] = r.float32()
                event["balance"] = r.float32()
            else:
                r.skip(8)
            events[name] = event
            self.events_lookup[i] = name
        return events
    
    # ─── Vertices ────────────────────────────────────────
    
    def read_vertices(self):
        r = self.reader
        vertices = []
        vertexCount = 0
        bone_info_count = r.int16()
        curr_coord_weight = r.pos + bone_info_count * 2
        coord_weight_count = r.int16(curr_coord_weight, True)
        bone_info_list = []
        count = 0
        for _ in range(bone_info_count):
            bc = r.int16()
            bone_info_list.append(bc)
            vertexCount += 1
            for _ in range(bc):
                bone_info_list.append(r.int16())
            count += bc + 1
            if count >= bone_info_count:
                break
        r.skip(2)
        bi = 0
        while bi < len(bone_info_list):
            bc = bone_info_list[bi]; bi += 1
            vertices.append(bc)
            for _ in range(bc):
                bone_id = bone_info_list[bi]; bi += 1
                x = r.float32(); y = r.float32(); w = r.float32()
                vertices.extend([bone_id, x, y, w])
        if bone_info_count == 0 and coord_weight_count != 0:
            vertexCount = int(coord_weight_count / 2)
            for _ in range(coord_weight_count):
                vertices.append(r.float32())
        return vertices, vertexCount
    
    # ─── Skins ────────────────────────────────────────────
    
    def parse_skins(self):
        r = self.reader
        count = r.int16()
        skins = []
        for k in range(count):
            name = r.string()
            self.skins_lookup[k] = name
            skip_count = r.int16()
            r.skip(2 + skip_count * 2)
            att_count = r.int16()
            attachments = {}
            for j in range(att_count):
                slot_id = r.int16()
                slot_name = self.slots_lookup.get(slot_id, f"slot_{slot_id}")
                value = r.string()
                type_id = r.int8()
                type_map = {0:"region",1:"boundingbox",2:"mesh",3:"linkedmesh",4:"path",5:"point",6:"clipping"}
                att_type = type_map.get(type_id, "region")
                r.skip(1)
                path_ptr = r.uint32()
                path = r.get_string(path_ptr)
                
                if slot_name not in attachments:
                    attachments[slot_name] = {}
                att_data = {"type": att_type}
                if path: att_data["path"] = path
                
                if att_type == "region":
                    att_data["x"] = r.float32(); att_data["y"] = r.float32()
                    att_data["rotation"] = r.float32()
                    att_data["scaleX"] = r.float32(); att_data["scaleY"] = r.float32()
                    att_data["width"] = r.float32(); att_data["height"] = r.float32()
                    r.skip(6 + 86)
                    att_data["path"] = r.string()
                    color = r.color()
                    if color != "FFFFFFFF": att_data["color"] = color
                elif att_type in ("mesh", "linkedmesh"):
                    vertices, vertexCount = self.read_vertices()
                    unknown_count = r.int16()
                    r.skip(unknown_count * 4 + 4 * 6 + 8)
                    uvs = [r.float32() for _ in range(r.int16())]
                    triangles = [r.int16() for _ in range(r.int16())]
                    edges_count = r.int16()
                    edges = [r.int16() for _ in range(edges_count)]
                    mesh_path = r.string()
                    r.skip(16)
                    width = r.float32(); height = r.float32()
                    color = r.color()
                    hull = r.int16()
                    att_data["uvs"] = uvs; att_data["triangles"] = triangles
                    att_data["vertices"] = vertices; att_data["hull"] = hull
                    att_data["edges"] = []; att_data["width"] = width; att_data["height"] = height
                    att_data["path"] = str(mesh_path)
                    if color != "FFFFFFFF": att_data["color"] = color
                    # 特殊跳过逻辑
                    if r.pos + 18 <= len(r.data):
                        hex_check = r.data[r.pos+14:r.pos+18].hex()
                        hex1 = r.data[r.pos:r.pos+2].hex()
                        if hex_check == 'ffffff00': r.skip(2)
                        if hex1 == '0000': r.skip(16)
                elif att_type == "boundingbox":
                    vertices, vertexCount = self.read_vertices()
                    att_data["vertexCount"] = vertexCount
                    att_data["vertices"] = vertices
                    r.skip(8)
                elif att_type == "path":
                    vertices, vertexCount = self.read_vertices()
                    r.skip(8)
                    lengths = [r.float32() for _ in range(r.int16())]
                    closed = r.bool8(); constantSpeed = r.bool8()
                    att_data["closed"] = closed; att_data["constantSpeed"] = constantSpeed
                    att_data["lengths"] = lengths; att_data["vertices"] = vertices
                    att_data["vertexCount"] = vertexCount
                elif att_type == "clipping":
                    vertices, vertexCount = self.read_vertices()
                    r.skip(8)
                    end_slot_id = r.int16()
                    att_data["end"] = self.slots_lookup.get(end_slot_id, "")
                    att_data["vertices"] = vertices; att_data["vertexCount"] = vertexCount
                
                attachments[slot_name][value] = att_data
            skins.append({"name": name, "attachments": attachments})
        self.skins = skins
        return skins
    
    # ─── 动画时间线解析 ──────────────────────────────────
    
    def linetime(self, type_id):
        """解析一条时间线的关键帧数据"""
        r = self.reader
        count = r.int16()
        frames = []
        for_count = 0
        
        while for_count < count:
            e = {}
            time = r.float32()
            e['time'] = time
            
            if type_id in (1, 2, 3):  # translate, scale, shear
                e['x'] = r.float32()
                e['y'] = r.float32()
                for_count += 3
            elif type_id == 0:  # rotate
                e['angle'] = r.float32()
                for_count += 2
            elif type_id in (11, 12):  # path position, spacing
                e['position'] = r.float32()
                for_count += 2
            elif type_id == 14:  # two color
                e['light'] = r.color()
                e['dark'] = r.color(need_alpha=False)
                for_count += 8
            elif type_id == 10:  # transform constraint
                e['rotateMix'] = r.float32()
                e['translateMix'] = r.float32()
                e['scaleMix'] = r.float32()
                e['shearMix'] = r.float32()
                for_count += 5
            elif type_id == 9:  # IK constraint
                e['mix'] = r.float32()
                e['softness'] = r.float32()
                e['bendPositive'] = r.bool_float32()
                e['compress'] = r.bool_float32()
                e['stretch'] = r.bool_float32()
                for_count += 6
            elif type_id == 13:  # path mix
                e['rotateMix'] = r.float32()
                e['translateMix'] = r.float32()
                for_count += 3
            else:
                for_count += 1
            frames.append(e)
        
        # 解析曲线数据
        curve_count = r.int16()
        if curve_count != 0 and type_id != 8:
            curve_idx = 0
            for f in frames:
                if curve_idx >= len(frames) - 1:
                    break
                curve_type = r.data[r.pos:r.pos+4]
                r.skip(4)
                curve_data = r.data[r.pos:r.pos+72]
                curve_p = self.hex_curve(curve_data)
                r.skip(72)
                curve_params = self.calculate_curve_params(curve_p, curve_type)
                if curve_params and curve_params != {"curve": 0, "c2": 0, "c3": 1, "c4": 1}:
                    f.update(curve_params)
                curve_idx += 1
        
        # type_id 6 = deform 特殊处理
        if type_id == 6:
            deform_count = r.int16()
            for i in range(min(deform_count, len(frames))):
                f = frames[i]
                offset = r.int16() * 4
                offset_num = 0
                while r.uint32(-1, True) == 0:
                    r.skip(4)
                    offset_num += 4
                
                vertices = []
                offset_count = 0
                while offset_count < offset:
                    v = r.float32()
                    offset_count += 4
                    vertices.append(v)
                    remaining = offset - offset_count
                    if remaining > 0 and r.data[r.pos:r.pos+remaining] == b'\x00' * remaining:
                        r.skip(remaining)
                        break
                
                re_order = {"time": f.get('time')}
                if vertices:
                    f['vertices'] = vertices
                    re_order['vertices'] = vertices
                    if offset % 4 == 0 and offset_num // 4 != 0:
                        f['offset'] = offset_num // 4
                        re_order['offset'] = offset_num // 4
                if f.get('curve') is not None: re_order['curve'] = f['curve']
                if f.get('c2') is not None: re_order['c2'] = f['c2']
                if f.get('c3') is not None: re_order['c3'] = f['c3']
                if f.get('c4') is not None: re_order['c4'] = f['c4']
                frames[i] = re_order
            
            key_ptr = r.uint32()
            key = r.get_string(key_ptr)
            skin_id = r.int16(peek=True)
            if len(self.skins) < skin_id:
                skin_id = 0
            else:
                r.skip(2)
            return skin_id, {key: frames}
        
        # type_id 8 = drawOrder
        if type_id == 8:
            for f in frames:
                draw_order_count = r.int16()
                offsets_index = []
                for _ in range(draw_order_count):
                    idx = r.int16()
                    r.skip(2)
                    offsets_index.append(idx)
                offsets = []
                for i_slot in range(draw_order_count):
                    index = offsets_index.index(i_slot)
                    if i_slot != index:
                        offsets.append({"slot": self.slots_lookup.get(i_slot, ""), "offset": index - i_slot})
                f["offsets"] = offsets
        
        return frames
    
    # ─── 完整动画解析 ──────────────────────────────────────
    
    def parse_animations(self):
        r = self.reader
        anim_count = r.int16()
        animations = {}
        
        for _ in range(anim_count):
            key = r.string()
            duration = r.float32()
            linetime_num = r.int16()
            
            slots = {}
            bones = {}
            deform = defaultdict(lambda: defaultdict(dict))
            drawOrder = []
            events = []
            paths = {}
            transforms = defaultdict(lambda: defaultdict(dict))
            iks = {}
            
            linetime_count = 0
            while linetime_count < linetime_num:
                type_id = r.int16()
                target_id = r.int16(-1, True)
                name = None
                
                if type_id != 7 and type_id != 8:
                    r.skip(2)
                
                # 根据 type_id 确定目标名称
                if type_id in (4, 5, 14):  # slot attachment/color/twoColor
                    name = self.slots_lookup.get(target_id, "")
                    if name not in slots: slots[name] = {}
                elif type_id == 6:  # deform
                    name = self.slots_lookup.get(target_id, "")
                elif type_id == 9:  # IK
                    name = self.ik_lookup.get(target_id, "")
                elif type_id == 10:  # transform
                    name = self.transform_lookup.get(target_id, "")
                elif type_id in (11, 12, 13):  # path
                    name = self.path_lookup.get(target_id, "")
                    if name not in paths: paths[name] = {}
                elif type_id in (0, 1, 2, 3):  # bone rotate/translate/scale/shear
                    name = self.bones_lookup.get(target_id, "")
                    if name not in bones: bones[name] = {}
                
                # 解析各类型
                if type_id == 0:  # rotate
                    bones[name]["rotate"] = self.linetime(0)
                elif type_id == 1:  # translate
                    bones[name]["translate"] = self.linetime(1)
                elif type_id == 2:  # scale
                    bones[name]["scale"] = self.linetime(2)
                elif type_id == 3:  # shear
                    bones[name]["shear"] = self.linetime(3)
                elif type_id == 4:  # slot attachment
                    frame_count = r.int16()
                    attachment = []
                    for _ in range(frame_count):
                        attachment.append({"time": r.float32()})
                    att_name_count = r.int16()
                    for a in attachment:
                        slot_name = r.string()
                        a["name"] = slot_name if slot_name != '' else None
                    if "attachment" in slots.get(name, {}):
                        slots[name]["attachment"].extend(attachment)
                    else:
                        slots[name]["attachment"] = attachment
                elif type_id == 5:  # slot color
                    frame_count = r.int16()
                    colors = []
                    for _ in range(int(frame_count / 5)):
                        colors.append({"time": r.float32(), "color": r.color()})
                    r.skip(2)
                    for ci, c in enumerate(colors):
                        if ci >= len(colors) - 1:
                            break
                        curve_type = r.data[r.pos:r.pos+4]
                        r.skip(4)
                        curve_data = r.data[r.pos:r.pos+72]
                        curve_p = self.hex_curve(curve_data)
                        r.skip(72)
                        curve_params = self.calculate_curve_params(curve_p, curve_type)
                        if curve_params and curve_params != {"curve": 0, "c2": 0, "c3": 1, "c4": 1}:
                            c.update(curve_params)
                    slots[name]["color"] = colors
                elif type_id == 6:  # deform
                    skin_id, map_data = self.linetime(6)
                    for deform_key in map_data:
                        verts_list = map_data[deform_key]
                        # 差分计算
                        try:
                            skin_name = self.skins_lookup.get(skin_id, "default")
                            if skin_id < len(self.skins):
                                original_verts = self.skins[skin_id]["attachments"].get(name, {}).get(deform_key, {}).get("vertices", [])
                                for e in verts_list:
                                    new_verts = e.get("vertices")
                                    if new_verts is None or len(original_verts) != len(new_verts):
                                        continue
                                    zero_count = 0
                                    for vi in range(len(new_verts)):
                                        val = clean_float(new_verts[vi] - original_verts[vi])
                                        new_verts[vi] = val
                                        if val == 0: zero_count += 1
                                    if zero_count == len(new_verts):
                                        e.pop("vertices", None)
                        except:
                            pass
                        
                        att_name = self.skins_lookup.get(skin_id, "default")
                        if deform_key in deform[att_name].get(name, {}):
                            deform[att_name][name].update(map_data)
                        else:
                            deform[att_name][name] = map_data
                elif type_id == 7:  # events
                    ev_count = r.int16()
                    ev_list = []
                    for _ in range(ev_count):
                        ev_list.append({"time": r.float32()})
                    r.skip(2)
                    for ev in ev_list:
                        ev["name"] = r.string()
                    events = ev_list
                elif type_id == 8:  # drawOrder
                    drawOrder = self.linetime(8)
                elif type_id == 9:  # IK
                    iks[name] = self.linetime(9)
                elif type_id == 10:  # transform
                    transforms[name] = self.linetime(10)
                elif type_id == 11:  # path position
                    paths[name]["position"] = self.linetime(11)
                elif type_id == 12:  # path spacing
                    paths[name]["spacing"] = self.linetime(12)
                elif type_id == 13:  # path mix
                    paths[name]["mix"] = self.linetime(13)
                elif type_id == 14:  # two color
                    slots[name]["twoColor"] = self.linetime(14)
                else:
                    break
                
                linetime_count += 1
            
            anim = {"bones": bones, "slots": slots}
            if iks: anim["ik"] = iks
            if transforms: anim["transform"] = dict(transforms)
            if paths: anim["path"] = paths
            if deform: anim["deform"] = {k: dict(v) for k, v in deform.items()}
            if drawOrder: anim["drawOrder"] = drawOrder
            anim["duration"] = duration
            if events: anim["events"] = events
            
            animations[key] = anim
        
        return animations
    
    # ─── 主解析入口 ──────────────────────────────────────
    
    def parse(self):
        skeleton = self.parse_skeleton()
        print(f"  Skeleton: {skeleton['spine']} ({skeleton['width']}x{skeleton['height']})")
        
        bones = self.parse_bones()
        print(f"  Bones: {len(bones)}")
        
        ik = self.parse_ik()
        print(f"  IK: {len(ik)}")
        
        slots = self.parse_slots()
        print(f"  Slots: {len(slots)}")
        
        transform = self.parse_transform()
        print(f"  Transform: {len(transform)}")
        
        path = self.parse_path()
        print(f"  Path: {len(path)}")
        
        try:
            skins = self.parse_skins()
            print(f"  Skins: {len(skins)}")
        except Exception as e:
            print(f"  Skins: ERROR - {e}")
            skins = []
            self.skins = []
        
        try:
            events = self.parse_events()
            print(f"  Events: {len(events)}")
        except Exception as e:
            print(f"  Events: ERROR - {e}")
            events = {}
        
        try:
            animations = self.parse_animations()
            anim_names = list(animations.keys())
            print(f"  Animations: {len(animations)} {anim_names}")
        except Exception as e:
            import traceback
            print(f"  Animations: ERROR - {e}")
            traceback.print_exc()
            animations = {}
        
        return {
            "skeleton": skeleton,
            "bones": bones,
            "slots": slots,
            "ik": ik,
            "transform": transform,
            "path": path,
            "skins": skins,
            "events": events,
            "animations": animations,
        }


def numpy_to_python(obj):
    """递归转换 numpy 类型为 Python 原生类型"""
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: numpy_to_python(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [numpy_to_python(i) for i in obj]
    else:
        return obj


def decode_scsp(filepath, output=None):
    print(f"\n{'='*60}")
    print(f"Decoding: {os.path.basename(filepath)}")
    raw = decompress_scsp(filepath)
    print(f"  Decompressed: {len(raw)} bytes")
    
    reader = BinaryReader(raw)
    parser = ScspParser(reader)
    result = parser.parse()
    result = numpy_to_python(result)
    
    if output is None:
        output = filepath.replace('.scsp', '.json')
    
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"  Output: {output}")
    return result


if __name__ == '__main__':
    out_dir = r"G:\kaesijiebao\scsp_json"
    os.makedirs(out_dir, exist_ok=True)
    
    test_files = [
        r"G:\keasi\unpacked\model\1001001.scsp",
    ]
    
    for fp in test_files:
        if os.path.exists(fp):
            name = os.path.basename(fp).replace('.scsp', '.json')
            try:
                decode_scsp(fp, os.path.join(out_dir, name))
            except Exception as e:
                import traceback
                print(f"  FAILED: {e}")
                traceback.print_exc()
