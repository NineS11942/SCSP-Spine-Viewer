import struct
import lz4.block
import os

class BoneData:
    def __init__(self, idx, name, parent_idx, length, x, y, rot, sx, sy, shx, shy, transform_mode, skin_required):
        self.idx = idx
        self.name = name
        self.parent_idx = parent_idx
        self.length = length
        self.x = x
        self.y = y
        self.rotation = rot
        self.scaleX = sx
        self.scaleY = sy
        self.shearX = shx
        self.shearY = shy
        self.transformMode = transform_mode
        self.skinRequired = bool(skin_required)
        
    def __repr__(self):
        return f"<Bone(idx={self.idx}, name='{self.name}', parent={self.parent_idx})>"

class IkConstraintData:
    def __init__(self, name, order, skin_req, bend_dir, compress, mix, softness, stretch, uniform, target_idx, bones):
        self.name = name
        self.order = order
        self.skinRequired = bool(skin_req)
        self.bendDirection = bend_dir
        self.compress = bool(compress)
        self.mix = mix
        self.softness = softness
        self.stretch = bool(stretch)
        self.uniform = bool(uniform)
        self.target_idx = target_idx
        self.bones = bones
        
    def __repr__(self):
        return f"<IkConstraint(name='{self.name}', target={self.target_idx}, bones={self.bones})>"

class SlotData:
    def __init__(self, idx, name, bone_idx, color, dark_color, has_dark_color, attachment_name, blend_mode):
        self.idx = idx
        self.name = name
        self.bone_idx = bone_idx
        self.color = color
        self.dark_color = dark_color
        self.has_dark_color = bool(has_dark_color)
        self.attachment_name = attachment_name
        self.blend_mode = blend_mode
        
    def __repr__(self):
        return f"<Slot(idx={self.idx}, name='{self.name}', bone={self.bone_idx}, attach='{self.attachment_name}')>"

class TransformConstraintData:
    def __init__(self, name, order, skin_req, floats, local, relative, target_idx, bones):
        self.name = name
        self.order = order
        self.skinRequired = bool(skin_req)
        self.rotation, self.x, self.y, self.scaleX, self.scaleY, self.shearY, \
        self.rotateMix, self.translateMix, self.scaleMix, self.shearMix = floats
        self.local = bool(local)
        self.relative = bool(relative)
        self.target_idx = target_idx
        self.bones = bones
        
    def __repr__(self):
        return f"<TransformConstraint(name='{self.name}', target={self.target_idx}, bones={self.bones})>"

class PathConstraintData:
    def __init__(self, name, order, skin_req, pos_mode, spac_mode, rot_mode, floats, target_idx, bones):
        self.name = name
        self.order = order
        self.skinRequired = bool(skin_req)
        self.positionMode = pos_mode
        self.spacingMode = spac_mode
        self.rotateMode = rot_mode
        self.offsetRotation, self.position, self.spacing, self.rotateMix, self.translateMix = floats
        self.target_idx = target_idx
        self.bones = bones
        
    def __repr__(self):
        return f"<PathConstraint(name='{self.name}', target={self.target_idx}, bones={self.bones})>"

# ─── Attachment types for Skins (段6) ───

class RegionAttachment:
    """Case 0: 248B object. 13 dwords + 2 Vectors(4B) + path StringRef + 4 color floats."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'region'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        self.dwords = []       # 13 raw dword values (+0x48~+0x78)
        self.region_uvs = []   # Vector at +0x80, 4B elements
        self.triangles = []    # Vector at +0xA0, 4B elements
        self.path = None       # StringRef at +0xC0
        self.color = (1.0, 1.0, 1.0, 1.0)  # 4 floats at +0xE8~+0xF4
    def __repr__(self):
        return f"<Region(name='{self.name}', path='{self.path}')>"

class BoundingBoxAttachment:
    """Case 1: 136B = VertexAttachment. readVertexAttachment inline, path consumed but discarded."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'boundingbox'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        self.bones = []       # uint16 array (vertexCount)
        self.vertices = []    # dword array (via readVertices)
        self.deform_length = 0
    def __repr__(self):
        return f"<BoundingBox(name='{self.name}', verts={len(self.vertices)})>"

class MeshAttachment:
    """Case 2: 416B. readVertexAttachment + 6 dwords + uvs + triangles + edges×2 + string + 10 dwords + flag + dword."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'mesh'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        # VertexAttachment fields
        self.va_bones = []         # uint16 array
        self.va_vertices = []      # dword array (via readVertices)
        self.deform_length = 0
        self.path = None           # StringRef — stored for Mesh (unlike BBox)
        # Mesh-specific
        self.dwords_6 = []         # 6 dwords at +0xA0~+0xB4
        self.uvs = []              # Vector4B at +0xC0 (4B elements)
        self.triangles = []        # Vector4B at +0xE0 (4B elements)
        self.edges = []            # VectorTypeB at +0x100 (2B elements, int16)
        self.edges2 = []           # VectorTypeB at +0x120 (2B elements, int16)
        self.mesh_string = None    # StringRef at +0x140
        self.dwords_10 = []        # 10 dwords at +0x160~+0x18C
        self.dword_190 = 0
        self.flag_194 = False      # uint8→bool
        self.dword_198 = 0
        self.sequence_string = None  # StringRef (version ≥ 30001)
        self.skin_index = -1       # int16
        self.inherit_deform = False # uint8→bool
    def __repr__(self):
        return f"<Mesh(name='{self.name}', path='{self.path}', tris={len(self.triangles)})>"

class LinkedMeshAttachment:
    """Case 3: Same as MeshAttachment but with deferred parent resolution."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'linkedmesh'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        # Shared MeshAttachment fields up to sequence
        self.va_bones = []
        self.va_vertices = []
        self.deform_length = 0
        self.path = None
        self.dwords_6 = []
        self.uvs = []
        self.triangles = []
        self.edges = []
        self.edges2 = []
        self.mesh_string = None
        self.dwords_10 = []
        self.dword_190 = 0
        self.flag_194 = False
        self.dword_198 = 0
        self.sequence_string = None
        self.skin_index = -1
        self.inherit_deform = False
        # Deferred fields
        self.parent_mesh_name = None  # attachmentName used to look up parent in Phase 3
    def __repr__(self):
        return f"<LinkedMesh(name='{self.name}', parent='{self.parent_mesh_name}', skin_idx={self.skin_index})>"

class PathAttachment:
    """Case 4: 176B = VertexAttachment + lengths Vector + closed + constantSpeed."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'path'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        self.va_bones = []
        self.va_vertices = []
        self.deform_length = 0
        self.lengths = []      # Vector4B (4B elements)
        self.closed = False
        self.constant_speed = False
    def __repr__(self):
        return f"<PathAttach(name='{self.name}', closed={self.closed})>"

class PointAttachment:
    """Case 5: 64B = Attachment(48B) + 3 dwords. dynamic_cast to VA fails → 0 byte VA consumption."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'point'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        self.field_0x30 = 0   # dword (x / rotation?)
        self.field_0x34 = 0   # dword
        self.field_0x38 = 0   # dword
    def __repr__(self):
        return f"<Point(name='{self.name}')>"

class ClippingAttachment:
    """Case 6: 144B = VertexAttachment + endSlotIndex (int16). dynamic_cast succeeds."""
    def __init__(self, name, skin_name, slot_index):
        self.type = 'clipping'
        self.name = name
        self.skin_name = skin_name
        self.slot_index = slot_index
        self.va_bones = []
        self.va_vertices = []
        self.deform_length = 0
        self.end_slot_index = -1
    def __repr__(self):
        return f"<Clipping(name='{self.name}', endSlot={self.end_slot_index})>"

class SkinData:
    """Skin object (144B): name + bones + constraints + attachments."""
    def __init__(self, name, is_default=False):
        self.name = name
        self.is_default = is_default
        self.bone_indices = []
        self.constraint_names = []
        self.attachments = []  # list of attachment objects
    def __repr__(self):
        return f"<Skin(name='{self.name}', default={self.is_default}, attachments={len(self.attachments)})>"

# ─── EventData (段7) ───

class EventData:
    """EventData (120B object = 0x78). Fixed 28-byte binary record per event.
    ASM: spMalloc(0x78) @ 0x146D4, EventData_ctor @ 0x146EB.
    Fields: name(StringRef) + intValue(int32) + floatValue(float32) +
            stringValue(StringRef) + audioPath(StringRef) + volume(float32) + balance(float32).
    """
    def __init__(self, name, int_value, float_value, string_value, audio_path, volume, balance):
        self.name = name
        self.int_value = int_value
        self.float_value = float_value
        self.string_value = string_value
        self.audio_path = audio_path
        self.volume = volume
        self.balance = balance
    def __repr__(self):
        return f"<Event(name='{self.name}', int={self.int_value}, float={self.float_value:.2f})>"

# ─── Animation / Timeline types (段8) ───

class TimelineData:
    """Base for all timeline types parsed from the stream.
    Each subclass records its case number and the raw vectors read from the binary.
    """
    def __init__(self, timeline_type_name, case_id):
        self.timeline_type = timeline_type_name
        self.case_id = case_id
    def __repr__(self):
        return f"<Timeline({self.timeline_type}, case={self.case_id})>"

class CurveTimelineData(TimelineData):
    """Cases 0,1,2,3,5,6,9,10,11,12,13,14 — inherit CurveTimeline (80B or 120B).
    Common: curveData vector + frames/values vector + an index field.
    ASM: CurveTimeline base sub_7FF734F82B00, curveData.size = 19*fc-19.
    """
    def __init__(self, timeline_type_name, case_id, index, frames_or_values, curve_data):
        super().__init__(timeline_type_name, case_id)
        self.index = index  # boneIndex / slotIndex / constraintIndex (int16 from stream)
        self.frames_or_values = frames_or_values  # readVertices result (raw dwords)
        self.curve_data = curve_data  # readVertices result (raw dwords)

class DeformTimelineData(TimelineData):
    """Case 6 — DeformTimeline (120B = 0x78). CurveTimeline + dual Vector.
    ASM: spMalloc(0x78), ctor sub_7FF734F545D0.
    Stream: int16(slotIndex) + readVertices(frames) + readVertices(curveData)
          + uint16(deformFrameCount) + deformFrameCount×readVertices(perFrame)
          + uint32(attachmentNameRef) + int16(skinIndex) [version≥0x7531, always true]
    """
    def __init__(self, slot_index, frames, curve_data, per_frame_vertices,
                 attachment_name, skin_index):
        super().__init__('DeformTimeline', 6)
        self.slot_index = slot_index
        self.frames = frames
        self.curve_data = curve_data
        self.per_frame_vertices = per_frame_vertices  # list of readVertices results
        self.attachment_name = attachment_name
        self.skin_index = skin_index

class AttachmentTimelineData(TimelineData):
    """Case 4 — AttachmentTimeline (80B). Does NOT inherit CurveTimeline.
    ASM: ctor sub_7FF734F533D0 → Timeline base directly.
    Stream: int16(slotIndex) + readVertices(frames)
          + uint16(nameCount) + nameCount×uint32(StringRef)
    """
    def __init__(self, slot_index, frames, attachment_names):
        super().__init__('AttachmentTimeline', 4)
        self.slot_index = slot_index
        self.frames = frames  # readVertices result (raw dwords)
        self.attachment_names = attachment_names  # list of resolved strings

class EventTimelineData(TimelineData):
    """Case 7 — EventTimeline (72B = 0x48). Does NOT inherit CurveTimeline.
    ASM: ctor sub_7FF734F57690 → Timeline base directly.
    Stream: readVertices(frames) + uint16(eventCount) + eventCount×uint32(eventNameRef)
    """
    def __init__(self, frames, event_name_refs):
        super().__init__('EventTimeline', 7)
        self.frames = frames
        self.event_name_refs = event_name_refs  # list of resolved event name strings

class DrawOrderTimelineData(TimelineData):
    """Case 8 — DrawOrderTimeline (72B = 0x48). Does NOT inherit CurveTimeline.
    ASM: ctor sub_7FF734F56770 → Timeline base directly.
    Stream: readVertices(frames) + uint16(drawOrderCount)
          + drawOrderCount×[uint16(slotCount) + slotCount×4B(int32 indices via memcpy)]
    """
    def __init__(self, frames, draw_orders):
        super().__init__('DrawOrderTimeline', 8)
        self.frames = frames
        self.draw_orders = draw_orders  # list of lists of int32 indices

class AnimationData:
    """Animation object (80B = 0x50 in engine).
    ASM: Animation ctor sub_7FF734F52C50.
    Stream per animation: uint32(nameRef 4B) + uint32(duration raw float 4B) + readTimelines(dynamic).
    """
    def __init__(self, name, duration, timelines):
        self.name = name
        self.duration = duration
        self.timelines = timelines
    def __repr__(self):
        return f"<Animation(name='{self.name}', duration={self.duration:.3f}, timelines={len(self.timelines)})>"

class SCSPV3Parser:
    def __init__(self, filepath):
        self.filepath = filepath
        self.spine_data = b""
        self.string_pool = b""
        self.pos = 0
        
        # Parsed data
        self.hash = ""
        self.version = ""
        self.x = 0.0
        self.y = 0.0
        self.width = 0.0
        self.height = 0.0
        self.fps = 0.0
        self.images_path = ""
        self.audio_path = ""
        
        self.bones = []
        self.ik_constraints = []
        self.slots = []
        self.transform_constraints = []
        self.path_constraints = []
        self.skins = []
        self.events = []
        self.animations = []
        
    def _read_string(self, ref):
        if ref == 0xFFFFFFFF or ref >= len(self.string_pool):
            return None
        end_idx = self.string_pool.find(b'\0', ref)
        if end_idx != -1:
            return self.string_pool[ref:end_idx].decode('utf-8', errors='ignore')
        return self.string_pool[ref:].decode('utf-8', errors='ignore')

    def parse(self):
        with open(self.filepath, 'rb') as f:
            raw_data = f.read()
            
        # 1. LZ4 Decompression
        uncompressed_size = struct.unpack_from('<I', raw_data, 0)[0]
        compressed_size = struct.unpack_from('<I', raw_data, 4)[0]
        compressed_data = raw_data[8:8+compressed_size]
        
        decompressed = lz4.block.decompress(compressed_data, uncompressed_size=uncompressed_size)
        
        # 2. Extract Data Sections
        data_size = struct.unpack_from('<I', decompressed, 0)[0]
        string_pool_size = struct.unpack_from('<I', decompressed, 4)[0]
        magic = decompressed[8:12]
        
        if magic != b"scsp":
            raise ValueError(f"Invalid magic: {magic}")
            
        # spine_data contains the 8-byte magic/version, just like Yuna engine's rawData pointer!
        self.spine_data = decompressed[8:8+data_size]
        self.string_pool = decompressed[8+data_size:8+data_size+string_pool_size]
        
        # 3. Parse Header (Fixed 74 bytes + strings)
        self.width = struct.unpack_from('<f', self.spine_data, 0x20)[0]
        self.height = struct.unpack_from('<f', self.spine_data, 0x24)[0]
        self.fps = struct.unpack_from('<f', self.spine_data, 0x28)[0]
        
        hash_ref = struct.unpack_from('<I', self.spine_data, 74)[0]
        self.hash = self._read_string(hash_ref)
        
        version_ref = struct.unpack_from('<I', self.spine_data, 78)[0]
        self.version = self._read_string(version_ref)
        
        self.x = struct.unpack_from('<f', self.spine_data, 82)[0]
        self.y = struct.unpack_from('<f', self.spine_data, 86)[0]
        
        images_ref = struct.unpack_from('<I', self.spine_data, 90)[0]
        self.images_path = self._read_string(images_ref)
        
        audio_ref = struct.unpack_from('<I', self.spine_data, 94)[0]
        self.audio_path = self._read_string(audio_ref)
        
        print(f"[Header] Hash: {self.hash}, Version: {self.version}")
        print(f"[Header] Size: {self.width}x{self.height}, Pos: {self.x},{self.y}, FPS: {self.fps}")
        print(f"[Header] Images: {self.images_path}, Audio: {self.audio_path}")
        
        # 4. Parse Bones (Starts at offset 98)
        self.pos = 98
        self._parse_bones()
        
        # 5. Parse IK Constraints
        self._parse_ik_constraints()
        
        # 6. Parse Slots
        self._parse_slots()
        
        # 7. Parse Transform Constraints
        self._parse_transform_constraints()
        
        # 8. Parse Path Constraints
        self._parse_path_constraints()
        
        # 9. Parse Skins (段6 — most complex segment)
        self._parse_skins()
        
        # 10. Parse Events (段7 — fixed 28B per event)
        self._parse_events()
        
        # 11. Parse Animations (段8 — 15 timeline types, most complex segment)
        self._parse_animations()
        
    def _parse_bones(self):
        bone_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Bones] Count: {bone_count}")
        
        for i in range(bone_count):
            bone_raw = struct.unpack_from('<h I h 8f h b', self.spine_data, self.pos)
            self.pos += 43
            
            name = self._read_string(bone_raw[1])
            bone = BoneData(
                idx=bone_raw[0],
                name=name,
                parent_idx=bone_raw[2],
                length=bone_raw[3],
                x=bone_raw[4],
                y=bone_raw[5],
                rot=bone_raw[6],
                sx=bone_raw[7],
                sy=bone_raw[8],
                shx=bone_raw[9],
                shy=bone_raw[10],
                transform_mode=bone_raw[11],
                skin_required=bone_raw[12]
            )
            self.bones.append(bone)
            
        print(f"[Bones] Successfully parsed {len(self.bones)} bones.")
        print(f"        First bone: {self.bones[0]}")
        print(f"        Last bone:  {self.bones[-1]}")
        print(f"        Final Pos:  0x{self.pos:X}")

    def _parse_ik_constraints(self):
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[IK Constraints] Count: {count}")
        
        for i in range(count):
            ik_data = struct.unpack_from('< I I B i B f f B B H H', self.spine_data, self.pos)
            self.pos += 28
            
            name = self._read_string(ik_data[0])
            order = ik_data[1]
            skin_req = ik_data[2]
            bend_dir = ik_data[3]
            compress = ik_data[4]
            mix = ik_data[5]
            softness = ik_data[6]
            stretch = ik_data[7]
            uniform = ik_data[8]
            target_idx = ik_data[9]
            bone_count = ik_data[10]
            
            bones = list(struct.unpack_from(f'<{bone_count}H', self.spine_data, self.pos))
            self.pos += bone_count * 2
            
            ik = IkConstraintData(name, order, skin_req, bend_dir, compress, mix, softness, stretch, uniform, target_idx, bones)
            self.ik_constraints.append(ik)
            
        print(f"[IK Constraints] Successfully parsed {len(self.ik_constraints)} IK constraints.")
        if self.ik_constraints:
            print(f"                 First IK: {self.ik_constraints[0]}")
        print(f"                 Final Pos: 0x{self.pos:X}")

    def _parse_slots(self):
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Slots] Count: {count}")
        
        for i in range(count):
            slot_data = struct.unpack_from('< h I h 8f B I h', self.spine_data, self.pos)
            self.pos += 47
            
            name = self._read_string(slot_data[1])
            attachment_name = self._read_string(slot_data[12])
            
            slot = SlotData(
                idx=slot_data[0],
                name=name,
                bone_idx=slot_data[2],
                color=slot_data[3:7],
                dark_color=slot_data[7:11],
                has_dark_color=slot_data[11],
                attachment_name=attachment_name,
                blend_mode=slot_data[13]
            )
            self.slots.append(slot)
            
        print(f"[Slots] Successfully parsed {len(self.slots)} slots.")
        if self.slots:
            print(f"        First slot: {self.slots[0]}")
            print(f"        Last slot:  {self.slots[-1]}")
        print(f"        Final Pos:  0x{self.pos:X}")

    def _parse_transform_constraints(self):
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Transform Constraints] Count: {count}")
        
        for i in range(count):
            # 55 bytes fixed part
            tc_data = struct.unpack_from('< I I B 10f B B H H', self.spine_data, self.pos)
            self.pos += 55
            
            name = self._read_string(tc_data[0])
            order = tc_data[1]
            skin_req = tc_data[2]
            floats = tc_data[3:13]
            local = tc_data[13]
            relative = tc_data[14]
            target_idx = tc_data[15]
            bone_count = tc_data[16]
            
            # Dynamic bone indices
            bones = list(struct.unpack_from(f'<{bone_count}H', self.spine_data, self.pos))
            self.pos += bone_count * 2
            
            tc = TransformConstraintData(name, order, skin_req, floats, local, relative, target_idx, bones)
            self.transform_constraints.append(tc)
            
        print(f"[Transform Constraints] Successfully parsed {len(self.transform_constraints)} constraints.")
        if self.transform_constraints:
            print(f"                        First TC: {self.transform_constraints[0]}")
        print(f"                        Final Pos: 0x{self.pos:X}")

    def _parse_path_constraints(self):
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Path Constraints] Count: {count}")
        
        for i in range(count):
            # 39 bytes fixed part
            pc_data = struct.unpack_from('< I I B 3h 5f H H', self.spine_data, self.pos)
            self.pos += 39
            
            name = self._read_string(pc_data[0])
            order = pc_data[1]
            skin_req = pc_data[2]
            pos_mode, spac_mode, rot_mode = pc_data[3:6]
            floats = pc_data[6:11]
            target_idx = pc_data[11]
            bone_count = pc_data[12]
            
            # Dynamic bone indices
            bones = list(struct.unpack_from(f'<{bone_count}H', self.spine_data, self.pos))
            self.pos += bone_count * 2
            
            pc = PathConstraintData(name, order, skin_req, pos_mode, spac_mode, rot_mode, floats, target_idx, bones)
            self.path_constraints.append(pc)
            
        print(f"[Path Constraints] Successfully parsed {len(self.path_constraints)} constraints.")
        if self.path_constraints:
            print(f"                   First PC: {self.path_constraints[0]}")
        print(f"                   Final Pos: 0x{self.pos:X}")

    # ─── Skins helpers (段6) ───

    def _read_vertices(self):
        """readVertices (sub_7FF734A12660): uint16 count + count×4 bytes memcpy.
        Returns list of raw dword values (4B each)."""
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        values = list(struct.unpack_from(f'<{count}I', self.spine_data, self.pos))
        self.pos += count * 4
        return values

    def _read_vertex_attachment(self, has_vertex_base=True):
        """readVertexAttachment (sub_7FF734A13250):
        If has_vertex_base is True (BBox/Mesh/Path/Clipping inherit VertexAttachment):
          uint16 vertexCount → vertexCount×uint16 bones → readVertices → uint32 deformLength → uint32 path(StringRef, consumed only)
        If False (Point — dynamic_cast fails): consumes 0 bytes.
        Returns (bones, vertices, deformLength, path_str_or_None)
        """
        if not has_vertex_base:
            return [], [], 0, None

        # Step 1: uint16 vertexCount
        vertex_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2

        # Step 2: bones array (vertexCount × uint16)
        bones = list(struct.unpack_from(f'<{vertex_count}H', self.spine_data, self.pos))
        self.pos += vertex_count * 2

        # Step 3: readVertices → vertices Vector (uint16 count + count×4 bytes)
        vertices = self._read_vertices()

        # Step 4: uint32 deformLength
        deform_length = struct.unpack_from('<I', self.spine_data, self.pos)[0]
        self.pos += 4

        # Step 5: path StringRef (consumed, but NOT stored for BBox/Path/Clipping — caller decides)
        path_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
        self.pos += 4
        path_str = self._read_string(path_ref)

        return bones, vertices, deform_length, path_str

    def _read_vector_4b(self):
        """Read a Vector with 4-byte elements: uint16 count + count×4 bytes memcpy."""
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        values = list(struct.unpack_from(f'<{count}I', self.spine_data, self.pos))
        self.pos += count * 4
        return values

    def _read_vector_2b(self):
        """Read a Vector with 2-byte elements: uint16 count + count×2 bytes memcpy."""
        count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        values = list(struct.unpack_from(f'<{count}h', self.spine_data, self.pos))
        self.pos += count * 2
        return values

    def _parse_skins(self):
        """段6: readSkins — 9140B function, 7 attachment types.
        Three-phase structure:
          Phase 1: Read skin count, init skins Vector
          Phase 2: For each skin → bones, constraints, attachments (switch on 7 types)
          Phase 3: Resolve LinkedMesh deferred entries
        """
        skin_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Skins] Count: {skin_count}")

        linked_meshes = []  # Phase 3 deferred list

        for skin_idx in range(skin_count):
            # ── Skin header ──
            # 1. skinName (uint32 StringRef)
            skin_name_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
            self.pos += 4
            skin_name = self._read_string(skin_name_ref)

            is_default = (skin_name == "default")
            skin = SkinData(skin_name, is_default)

            # 2. boneCount (uint16) + boneIndices (int16 each)
            bone_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
            self.pos += 2
            for _ in range(bone_count):
                bidx = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                skin.bone_indices.append(bidx)

            # 3. constraintCount (uint16) + constraintNames (uint32 StringRef each)
            constraint_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
            self.pos += 2
            for _ in range(constraint_count):
                cref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                self.pos += 4
                skin.constraint_names.append(self._read_string(cref))

            # 4. attachmentCount (uint16)
            attachment_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
            self.pos += 2

            # ── Attachment loop ──
            for _ in range(attachment_count):
                # Common header: 12 bytes
                slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                attach_name_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                self.pos += 4
                attach_name = self._read_string(attach_name_ref)
                attach_type = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                attach_skin_name_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                self.pos += 4
                attach_skin_name = self._read_string(attach_skin_name_ref)

                attachment = None
                # attach_name (step 3) = display/atlas name (constructor name in engine)
                # attach_skin_name (step 5) = skin placeholder key (used in Skin::addAttachment)
                # JSON key = attach_skin_name (or attach_name if skin_name is None)
                # JSON "path" = attach_name (only if different from key)
                json_key_name = attach_skin_name or attach_name
                atlas_display_name = attach_name  # always the atlas region lookup name
                
                # DEBUG: log cases where names differ
                if attach_name != attach_skin_name and attach_skin_name:
                    print(f"  [DEBUG] slot={slot_index} type={attach_type} attach_name='{attach_name}' attach_skin_name='{attach_skin_name}' key='{json_key_name}'")

                if attach_type == 0:
                    # ── Case 0: RegionAttachment ──
                    attachment = RegionAttachment(json_key_name, attach_skin_name, slot_index)
                    attachment.atlas_name = atlas_display_name
                    # 13 dwords (4B each) at +0x48~+0x78
                    attachment.dwords = list(struct.unpack_from('<13I', self.spine_data, self.pos))
                    self.pos += 13 * 4
                    # Vector_0x80: uint16 count + count×4B (regionUVs)
                    attachment.region_uvs = self._read_vector_4b()
                    # Vector_0xA0: uint16 count + count×4B (triangles, each 4B!)
                    attachment.triangles = self._read_vector_4b()
                    # path StringRef at +0xC0
                    path_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4
                    attachment.path = self._read_string(path_ref)
                    # Color: 4 × dword (float, default 1.0f) at +0xE8~+0xF4
                    color_raw = struct.unpack_from('<4f', self.spine_data, self.pos)
                    self.pos += 16
                    attachment.color = color_raw

                elif attach_type == 1:
                    # ── Case 1: BoundingBoxAttachment ──
                    # Inline readVertexAttachment (dynamic_cast succeeds)
                    attachment = BoundingBoxAttachment(json_key_name, attach_skin_name, slot_index)
                    attachment.atlas_name = atlas_display_name
                    bones, vertices, deform_length, _path = self._read_vertex_attachment(has_vertex_base=True)
                    attachment.bones = bones
                    attachment.vertices = vertices
                    attachment.deform_length = deform_length
                    # path consumed but discarded (not stored)

                elif attach_type in (2, 3):
                    # ── Case 2/3: MeshAttachment / LinkedMesh ──
                    if attach_type == 3:
                        attachment = LinkedMeshAttachment(json_key_name, attach_skin_name, slot_index)
                        attachment.atlas_name = atlas_display_name
                        attachment.parent_mesh_name = attach_name
                    else:
                        attachment = MeshAttachment(json_key_name, attach_skin_name, slot_index)
                        attachment.atlas_name = atlas_display_name

                    # Inline readVertexAttachment (path IS stored for Mesh)
                    bones, vertices, deform_length, path_str = self._read_vertex_attachment(has_vertex_base=True)
                    attachment.va_bones = bones
                    attachment.va_vertices = vertices
                    attachment.deform_length = deform_length
                    attachment.path = path_str

                    # 6 × dword at +0xA0~+0xB4
                    attachment.dwords_6 = list(struct.unpack_from('<6I', self.spine_data, self.pos))
                    self.pos += 6 * 4

                    # uvs Vector (4B elements)
                    attachment.uvs = self._read_vector_4b()
                    # triangles Vector (4B elements)
                    attachment.triangles = self._read_vector_4b()
                    # edges Vector (2B elements, int16)
                    attachment.edges = self._read_vector_2b()
                    # edges2 Vector (2B elements, int16)
                    attachment.edges2 = self._read_vector_2b()

                    # StringRef at +0x140
                    str_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4
                    attachment.mesh_string = self._read_string(str_ref)

                    # 10 × dword at +0x160~+0x18C
                    attachment.dwords_10 = list(struct.unpack_from('<10I', self.spine_data, self.pos))
                    self.pos += 10 * 4

                    # dword_190
                    attachment.dword_190 = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4
                    # flag_194 (uint8 → bool)
                    attachment.flag_194 = bool(struct.unpack_from('<B', self.spine_data, self.pos)[0])
                    self.pos += 1
                    # dword_198
                    attachment.dword_198 = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4

                    # Version ≥ 30001 path (ASM @ 0x169E9~0x16DF4):
                    # StringRef (4B) — sequence string
                    seq_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4
                    attachment.sequence_string = self._read_string(seq_ref)

                    # int16 #1 (2B) — step 26 @ 0x16A3C: movsx eax, word ptr [rdi+rdx]
                    field_26 = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                    self.pos += 2

                    # int16 #2 (2B) — @ 0x16DF4: movsx r12d, word ptr [r8+rdx] → skinIndex
                    si = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                    self.pos += 2
                    attachment.skin_index = si

                    # uint8 → bool inheritDeform (@ 0x16E0F)
                    attachment.inherit_deform = bool(struct.unpack_from('<B', self.spine_data, self.pos)[0])
                    self.pos += 1

                    # Case 3 deferred: add to linked_meshes for Phase 3
                    if attach_type == 3:
                        attachment.parent_mesh_name = attachment.sequence_string
                        linked_meshes.append({
                            'mesh': attachment,
                            'slot_index': slot_index,
                            'skin_index': si,
                            'parent_name': attach_name,
                            'inherit_deform': attachment.inherit_deform,
                        })

                elif attach_type == 4:
                    # ── Case 4: PathAttachment ──
                    attachment = PathAttachment(json_key_name, attach_skin_name, slot_index)
                    attachment.atlas_name = atlas_display_name
                    # Inline readVertexAttachment (path consumed & discarded)
                    bones, vertices, deform_length, _path = self._read_vertex_attachment(has_vertex_base=True)
                    attachment.va_bones = bones
                    attachment.va_vertices = vertices
                    attachment.deform_length = deform_length
                    # lengths Vector (4B elements)
                    attachment.lengths = self._read_vector_4b()
                    # uint8 closed → bool
                    attachment.closed = bool(struct.unpack_from('<B', self.spine_data, self.pos)[0])
                    self.pos += 1
                    # uint8 constantSpeed → bool
                    attachment.constant_speed = bool(struct.unpack_from('<B', self.spine_data, self.pos)[0])
                    self.pos += 1

                elif attach_type == 5:
                    # ── Case 5: PointAttachment ──
                    # readVertexAttachment called but dynamic_cast FAILS (no VertexAttachment in RTTI)
                    # → 0 bytes consumed by readVertexAttachment
                    attachment = PointAttachment(json_key_name, attach_skin_name, slot_index)
                    attachment.atlas_name = atlas_display_name
                    # 3 × dword at +0x30/+0x34/+0x38
                    d1, d2, d3 = struct.unpack_from('<3I', self.spine_data, self.pos)
                    self.pos += 12
                    attachment.field_0x30 = d1
                    attachment.field_0x34 = d2
                    attachment.field_0x38 = d3

                elif attach_type == 6:
                    # ── Case 6: ClippingAttachment ──
                    # readVertexAttachment called, dynamic_cast succeeds (inherits VA), a3=NULL (path discarded)
                    attachment = ClippingAttachment(json_key_name, attach_skin_name, slot_index)
                    attachment.atlas_name = atlas_display_name
                    bones, vertices, deform_length, _path = self._read_vertex_attachment(has_vertex_base=True)
                    attachment.va_bones = bones
                    attachment.va_vertices = vertices
                    attachment.deform_length = deform_length
                    # int16 endSlotIndex
                    attachment.end_slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                    self.pos += 2

                else:
                    # Unknown attachment type — can't parse further
                    print(f"  [!] Unknown attachment type {attach_type} at pos 0x{self.pos:X}, skipping")
                    continue

                if attachment is not None:
                    skin.attachments.append(attachment)

            self.skins.append(skin)

        # ── Phase 3: LinkedMesh deferred resolution ──
        # In the engine this resolves parent mesh pointers.
        # In our parser we just record the linkage info for JSON export.
        for entry in linked_meshes:
            entry['mesh']._resolved = True  # mark as processed

        # ── Summary ──
        total_attachments = sum(len(s.attachments) for s in self.skins)
        type_counts = {}
        for s in self.skins:
            for a in s.attachments:
                type_counts[a.type] = type_counts.get(a.type, 0) + 1

        print(f"[Skins] Successfully parsed {len(self.skins)} skins, {total_attachments} total attachments.")
        if self.skins:
            print(f"        Default skin: {next((s.name for s in self.skins if s.is_default), 'N/A')}")
            print(f"        Type breakdown: {type_counts}")
        print(f"        LinkedMesh deferred entries: {len(linked_meshes)}")
        print(f"        Final Pos: 0x{self.pos:X}")

    def _parse_events(self):
        """段7: readEvents (sub_7FF734A14640)
        Each EventData is a fixed 28-byte record:
          uint32 nameRef + int32 intValue + float32 floatValue +
          uint32 stringRef + uint32 audioRef + float32 volume + float32 balance
        ASM: spMalloc(0x78=120) @ 0x146D4, fields at +0x28, +0x2C, +0x38, +0x58, +0x70, +0x74
        """
        event_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]
        self.pos += 2
        print(f"[Events] Count: {event_count}")

        for i in range(event_count):
            # Fixed 28-byte record: '<I i f I I f f'
            ev = struct.unpack_from('<I i f I I f f', self.spine_data, self.pos)
            self.pos += 28

            name_ref    = ev[0]   # uint32 StringRef → event name
            int_value   = ev[1]   # int32  → eventData+0x28
            float_value = ev[2]   # float  → eventData+0x2C (raw IEEE-754)
            string_ref  = ev[3]   # uint32 StringRef → eventData+0x38 SpineString
            audio_ref   = ev[4]   # uint32 StringRef → eventData+0x58 SpineString
            volume      = ev[5]   # float  → eventData+0x70 (raw IEEE-754)
            balance     = ev[6]   # float  → eventData+0x74 (raw IEEE-754)

            name = self._read_string(name_ref)
            string_value = self._read_string(string_ref) if string_ref != 0xFFFFFFFF else ""
            audio_path = self._read_string(audio_ref) if audio_ref != 0xFFFFFFFF else ""

            event = EventData(name, int_value, float_value, string_value, audio_path, volume, balance)
            self.events.append(event)

        print(f"[Events] Successfully parsed {len(self.events)} events.")
        if self.events:
            print(f"         First event: {self.events[0]}")
            print(f"         Last event:  {self.events[-1]}")
        print(f"         Final Pos: 0x{self.pos:X}")

    def _parse_animations(self):
        """段8: readAnimations @ 0x7FF734A13A50 + readTimelines @ sub_7FF734A17AA0.
        Outer: uint16(animationCount) + per-animation: uint32(nameRef) + uint32(duration float) + readTimelines.
        Inner (readTimelines): uint16(timelineCount) + per-timeline: int16(type) + switch(15 cases).
        All byte consumption sequences are ASM-verified — see 段8_Animations解析器_分析任务.md.
        """
        anim_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] A1: movzx edx, word ptr
        self.pos += 2
        print(f"[Animations] Count: {anim_count}")

        for anim_idx in range(anim_count):
            # ── Per-animation header (A3) ──
            # 1. name = uint32 StringRef (4B) [ASM✓] mov ecx,[rsi+rax] @ 0x13C26
            name_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
            self.pos += 4
            anim_name = self._read_string(name_ref)

            # 2. duration = uint32 raw IEEE-754 float (4B) [ASM✓] mov esi,[rbx+rsi] @ 0x13C72
            duration_raw = struct.unpack_from('<f', self.spine_data, self.pos)[0]
            self.pos += 4

            # 3. readTimelines (sub_7FF734A17AA0)
            timelines = self._parse_timelines()

            anim = AnimationData(anim_name, duration_raw, timelines)
            self.animations.append(anim)

        # ── Summary ──
        total_timelines = sum(len(a.timelines) for a in self.animations)
        type_counts = {}
        for a in self.animations:
            for t in a.timelines:
                type_counts[t.timeline_type] = type_counts.get(t.timeline_type, 0) + 1
        print(f"[Animations] Successfully parsed {len(self.animations)} animations, {total_timelines} total timelines.")
        if self.animations:
            print(f"             First: {self.animations[0]}")
            print(f"             Last:  {self.animations[-1]}")
            print(f"             Type breakdown: {type_counts}")
        print(f"             Final Pos: 0x{self.pos:X}")
        print(f"             Data length: 0x{len(self.spine_data):X}")

    # Timeline type name mapping [RTTI✓ all 15 verified]
    _TIMELINE_NAMES = {
        0: 'RotateTimeline',              # frames=2*fc
        1: 'TranslateTimeline',            # frames=3*fc
        2: 'ScaleTimeline',                # frames=3*fc
        3: 'ShearTimeline',                # frames=3*fc
        4: 'AttachmentTimeline',           # no CurveTimeline
        5: 'ColorTimeline',                # frames=5*fc
        6: 'DeformTimeline',               # 120B, dual vector
        7: 'EventTimeline',                # no CurveTimeline
        8: 'DrawOrderTimeline',            # no CurveTimeline
        9: 'IkConstraintTimeline',         # frames=6*fc
        10: 'TransformConstraintTimeline',  # frames=5*fc
        11: 'PathConstraintPositionTL',     # frames=2*fc
        12: 'PathConstraintSpacingTL',      # frames=2*fc
        13: 'PathConstraintMixTimeline',    # frames=3*fc
        14: 'TwoColorTimeline',            # frames=8*fc
    }

    def _parse_timelines(self):
        """readTimelines (sub_7FF734A17AA0): uint16(timelineCount) + switch on 15 cases.
        Returns list of TimelineData objects.
        """
        timeline_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx edx, word ptr @ 0x17AFB
        self.pos += 2
        timelines = []

        for _ in range(timeline_count):
            tl_type = struct.unpack_from('<h', self.spine_data, self.pos)[0]  # [ASM✓] movsx rdx, word ptr @ 0x17C17
            self.pos += 2

            if tl_type == 0:
                # ── Case 0: RotateTimeline [ASM✓ B2] ──
                # int16(boneIndex 2B) + readVertices(frames@+0x30) + readVertices(curveData@+0x08)
                bone_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()    # readVertices → +0x30
                curve_data = self._read_vertices() # readVertices → +0x08
                tl = CurveTimelineData('RotateTimeline', 0, bone_index, frames, curve_data)

            elif tl_type in (1, 2, 3):
                # ── Case 1/2/3: Translate/Scale/ShearTimeline [ASM✓ C2/D2] ──
                # All three share identical byte consumption pattern:
                # int16(boneIndex 2B) + readVertices(values@+0x28) + readVertices(curveData@+0x08)
                bone_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                values = self._read_vertices()
                curve_data = self._read_vertices()
                tl = CurveTimelineData(self._TIMELINE_NAMES[tl_type], tl_type,
                                       bone_index, values, curve_data)

            elif tl_type == 4:
                # ── Case 4: AttachmentTimeline [ASM✓ E2] ──
                # int16(slotIndex 2B, movsx→QWORD@+0x08) + readVertices(frames@+0x10)
                # + uint16(nameCount 2B) + nameCount×uint32(StringRef 4B each)
                slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()  # readVertices → +0x10
                # sub_7FF734A13590 = Vector::clear [ASM✓ N3] — does NOT consume stream data
                name_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx @ 0x17E93
                self.pos += 2
                attach_names = []
                for _ in range(name_count):
                    ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]  # [ASM✓] mov r9d @ 0x17EE4
                    self.pos += 4
                    attach_names.append(self._read_string(ref))
                tl = AttachmentTimelineData(slot_index, frames, attach_names)

            elif tl_type == 5:
                # ── Case 5: ColorTimeline [ASM✓ F2] ──
                # Same pattern as Case 0: int16(slotIndex) + readVertices(frames) + readVertices(curveData)
                slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()
                curve_data = self._read_vertices()
                tl = CurveTimelineData('ColorTimeline', 5, slot_index, frames, curve_data)

            elif tl_type == 6:
                # ── Case 6: DeformTimeline [ASM✓ G2] ──
                # int16(slotIndex 2B) + readVertices(frames@+0x30) + readVertices(curveData@+0x08)
                # + uint16(deformFrameCount 2B)
                # + deformFrameCount × readVertices(perFrameVertices)
                # + uint32(attachmentNameRef 4B)
                # + int16(skinIndex 2B) [version≥0x7531 — always true for SCSP format]
                slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()
                curve_data = self._read_vertices()
                # Vector::clear calls [N3] — no stream consumption
                deform_fc = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx @ 0x18370
                self.pos += 2
                # Second Vector::clear [N3] — no stream consumption
                per_frame_verts = []
                for _ in range(deform_fc):
                    pf = self._read_vertices()  # readVertices per deform frame
                    per_frame_verts.append(pf)
                # attachmentName StringRef (4B) [ASM✓] mov ecx @ 0x186DC
                attach_ref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                self.pos += 4
                attach_name = self._read_string(attach_ref)
                # skinIndex int16 (2B) — version ≥ 0x7531 path [ASM✓] movsx @ 0x18773
                # Format version is always ≥ 30001 (0x7531), so this is always read.
                skin_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                tl = DeformTimelineData(slot_index, frames, curve_data,
                                        per_frame_verts, attach_name, skin_index)

            elif tl_type == 7:
                # ── Case 7: EventTimeline [ASM✓ H2] ──
                # readVertices(frames@+0x08) + uint16(eventCount 2B)
                # + eventCount × readEventRef(4B each = uint32 StringRef) [N2]
                frames = self._read_vertices()
                event_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx @ 0x1884C
                self.pos += 2
                event_names = []
                for _ in range(event_count):
                    # readEventRef (sub_7FF734A196B0) [ASM✓ N2]: consumes 4B uint32 StringRef
                    eref = struct.unpack_from('<I', self.spine_data, self.pos)[0]
                    self.pos += 4
                    event_names.append(self._read_string(eref))
                tl = EventTimelineData(frames, event_names)

            elif tl_type == 8:
                # ── Case 8: DrawOrderTimeline [ASM✓ I2] ──
                # readVertices(frames@+0x08) + uint16(drawOrderCount 2B)
                # + drawOrderCount × [uint16(slotCount 2B) + memcpy(slotCount×4B int32)]
                frames = self._read_vertices()
                do_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx @ 0x18AEE
                self.pos += 2
                # Vector::clear [N3] — no stream consumption
                draw_orders = []
                for _ in range(do_count):
                    slot_count = struct.unpack_from('<H', self.spine_data, self.pos)[0]  # [ASM✓] movzx @ 0x18B2A
                    self.pos += 2
                    # memcpy(data, buf+pos, slotCount*4) [ASM✓] @ 0x18BD5
                    indices = list(struct.unpack_from(f'<{slot_count}i', self.spine_data, self.pos))
                    self.pos += slot_count * 4
                    draw_orders.append(indices)
                tl = DrawOrderTimelineData(frames, draw_orders)

            elif tl_type in (9, 10, 11, 12, 13):
                # ── Cases 9-13: Constraint Timelines [ASM✓ J2/K2/L2] ──
                # All share identical "CurveTimeline + index" pattern:
                # int16(constraintIndex 2B) + readVertices(frames@+0x28) + readVertices(curveData@+0x08)
                constraint_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()
                curve_data = self._read_vertices()
                tl = CurveTimelineData(self._TIMELINE_NAMES[tl_type], tl_type,
                                       constraint_index, frames, curve_data)

            elif tl_type == 14:
                # ── Case 14: TwoColorTimeline [ASM✓ M2] ──
                # Same pattern as Cases 9-13 but +0x48 = slotIndex (not constraintIndex)
                # int16(slotIndex 2B) + readVertices(frames@+0x28) + readVertices(curveData@+0x08)
                slot_index = struct.unpack_from('<h', self.spine_data, self.pos)[0]
                self.pos += 2
                frames = self._read_vertices()
                curve_data = self._read_vertices()
                tl = CurveTimelineData('TwoColorTimeline', 14, slot_index, frames, curve_data)

            else:
                raise ValueError(f"Unknown timeline type {tl_type} at pos 0x{self.pos:X}")

            timelines.append(tl)

        return timelines

    def export_json(self):
        """Export parsed data to Spine 3.8 JSON format (Segment 0)."""
        import json
        import math
        
        skeleton = {}
        if self.hash:
            skeleton["hash"] = self.hash
        if self.version:
            skeleton["spine"] = self.version
            
        def is_valid(v):
            return not math.isnan(v) and abs(v) > 1e-10

        if is_valid(self.x):
            skeleton["x"] = self.x
        if is_valid(self.y):
            skeleton["y"] = self.y
        if is_valid(self.width):
            skeleton["width"] = self.width
        if is_valid(self.height):
            skeleton["height"] = self.height
        if is_valid(self.fps):
            skeleton["fps"] = self.fps
            
        if self.images_path:
            skeleton["images"] = self.images_path
        if self.audio_path:
            skeleton["audio"] = self.audio_path

        # --- Stage 1: Bones ---
        transform_mode_map = {
            0: "normal",
            1: "onlyTranslation",
            2: "noRotationOrReflection",
            3: "noScale",
            4: "noScaleOrReflection"
        }
        
        def clean_float(val):
            r = round(val, 4)
            # Prevent -0.0
            if r == 0:
                return 0
            return int(r) if r.is_integer() else r

        bones_json = []
        for bone in self.bones:
            b_dict = {"name": bone.name}
            
            if bone.parent_idx != -1 and bone.parent_idx < len(self.bones):
                b_dict["parent"] = self.bones[bone.parent_idx].name
                
            if clean_float(bone.length) != 0:
                b_dict["length"] = clean_float(bone.length)
            if clean_float(bone.x) != 0:
                b_dict["x"] = clean_float(bone.x)
            if clean_float(bone.y) != 0:
                b_dict["y"] = clean_float(bone.y)
            if clean_float(bone.rotation) != 0:
                b_dict["rotation"] = clean_float(bone.rotation)
            if clean_float(bone.scaleX) != 1:
                b_dict["scaleX"] = clean_float(bone.scaleX)
            if clean_float(bone.scaleY) != 1:
                b_dict["scaleY"] = clean_float(bone.scaleY)
            if clean_float(bone.shearX) != 0:
                b_dict["shearX"] = clean_float(bone.shearX)
            if clean_float(bone.shearY) != 0:
                b_dict["shearY"] = clean_float(bone.shearY)
                
            t_mode = transform_mode_map.get(bone.transformMode, "normal")
            if t_mode != "normal":
                b_dict["transform"] = t_mode
                
            if bone.skinRequired:
                b_dict["skin"] = True
                
            bones_json.append(b_dict)

        # --- Stage 2: Slots ---
        blend_mode_map = {
            0: "normal",
            1: "additive",
            2: "multiply",
            3: "screen"
        }
        
        def to_hex_color(color_tuple):
            r, g, b, a = color_tuple
            return f"{int(round(r*255)):02X}{int(round(g*255)):02X}{int(round(b*255)):02X}{int(round(a*255)):02X}"
            
        def to_hex_dark(color_tuple):
            r, g, b, _ = color_tuple
            return f"{int(round(r*255)):02X}{int(round(g*255)):02X}{int(round(b*255)):02X}"

        slots_json = []
        for slot in self.slots:
            s_dict = {"name": slot.name}
            if 0 <= slot.bone_idx < len(self.bones):
                s_dict["bone"] = self.bones[slot.bone_idx].name
                
            color_hex = to_hex_color(slot.color)
            if color_hex != "FFFFFFFF":
                s_dict["color"] = color_hex
                
            if slot.has_dark_color:
                s_dict["dark"] = to_hex_dark(slot.dark_color)
                
            if slot.attachment_name and slot.attachment_name != "None":
                s_dict["attachment"] = slot.attachment_name
                
            b_mode = blend_mode_map.get(slot.blend_mode, "normal")
            if b_mode != "normal":
                s_dict["blend"] = b_mode
                
            slots_json.append(s_dict)

        # --- Stage 3: IK Constraints ---
        ik_json = []
        for ik in self.ik_constraints:
            ik_dict = {"name": ik.name}
            if getattr(ik, 'order', 0) != 0:
                ik_dict["order"] = ik.order
            if getattr(ik, 'skinRequired', False):
                ik_dict["skin"] = True
                
            ik_bones = []
            for b_idx in ik.bones:
                if 0 <= b_idx < len(self.bones):
                    ik_bones.append(self.bones[b_idx].name)
            if ik_bones:
                ik_dict["bones"] = ik_bones
                
            if 0 <= ik.target_idx < len(self.bones):
                ik_dict["target"] = self.bones[ik.target_idx].name
                
            if clean_float(getattr(ik, 'mix', 1.0)) != 1.0:
                ik_dict["mix"] = clean_float(ik.mix)
            if clean_float(getattr(ik, 'softness', 0.0)) != 0.0:
                ik_dict["softness"] = clean_float(ik.softness)
                
            if getattr(ik, 'bendDirection', 1) == -1:
                ik_dict["bendPositive"] = False
                
            if getattr(ik, 'compress', False):
                ik_dict["compress"] = True
            if getattr(ik, 'stretch', False):
                ik_dict["stretch"] = True
            if getattr(ik, 'uniform', False):
                ik_dict["uniform"] = True
                
            ik_json.append(ik_dict)

        # --- Stage 4: Transform Constraints ---
        tc_json = []
        for tc in self.transform_constraints:
            tc_dict = {"name": tc.name}
            if getattr(tc, 'order', 0) != 0:
                tc_dict["order"] = tc.order
            if getattr(tc, 'skinRequired', False):
                tc_dict["skin"] = True
                
            tc_bones = []
            for b_idx in tc.bones:
                if 0 <= b_idx < len(self.bones):
                    tc_bones.append(self.bones[b_idx].name)
            if tc_bones:
                tc_dict["bones"] = tc_bones
                
            if 0 <= tc.target_idx < len(self.bones):
                tc_dict["target"] = self.bones[tc.target_idx].name
                
            if clean_float(getattr(tc, 'rotation', 0.0)) != 0: tc_dict["rotation"] = clean_float(tc.rotation)
            if clean_float(getattr(tc, 'x', 0.0)) != 0: tc_dict["x"] = clean_float(tc.x)
            if clean_float(getattr(tc, 'y', 0.0)) != 0: tc_dict["y"] = clean_float(tc.y)
            if clean_float(getattr(tc, 'scaleX', 0.0)) != 0: tc_dict["scaleX"] = clean_float(tc.scaleX)
            if clean_float(getattr(tc, 'scaleY', 0.0)) != 0: tc_dict["scaleY"] = clean_float(tc.scaleY)
            if clean_float(getattr(tc, 'shearY', 0.0)) != 0: tc_dict["shearY"] = clean_float(tc.shearY)
            
            if clean_float(getattr(tc, 'rotateMix', 1.0)) != 1: tc_dict["rotateMix"] = clean_float(tc.rotateMix)
            if clean_float(getattr(tc, 'translateMix', 1.0)) != 1: tc_dict["translateMix"] = clean_float(tc.translateMix)
            if clean_float(getattr(tc, 'scaleMix', 1.0)) != 1: tc_dict["scaleMix"] = clean_float(tc.scaleMix)
            if clean_float(getattr(tc, 'shearMix', 1.0)) != 1: tc_dict["shearMix"] = clean_float(tc.shearMix)
            
            if getattr(tc, 'local', False): tc_dict["local"] = True
            if getattr(tc, 'relative', False): tc_dict["relative"] = True
            
            tc_json.append(tc_dict)

        # --- Stage 5: Path Constraints ---
        pos_mode_map = {0: "fixed", 1: "percent"}
        spac_mode_map = {0: "length", 1: "fixed", 2: "percent"}
        rot_mode_map = {0: "tangent", 1: "chain", 2: "chainScale"}
        
        pc_json = []
        for pc in self.path_constraints:
            pc_dict = {"name": pc.name}
            if getattr(pc, 'order', 0) != 0:
                pc_dict["order"] = pc.order
            if getattr(pc, 'skinRequired', False):
                pc_dict["skin"] = True
                
            pc_bones = []
            for b_idx in pc.bones:
                if 0 <= b_idx < len(self.bones):
                    pc_bones.append(self.bones[b_idx].name)
            if pc_bones:
                pc_dict["bones"] = pc_bones
                
            if 0 <= pc.target_idx < len(self.slots):
                pc_dict["target"] = self.slots[pc.target_idx].name
                
            p_mode = pos_mode_map.get(getattr(pc, 'positionMode', 1), "percent")
            if p_mode != "percent":
                pc_dict["positionMode"] = p_mode
                
            s_mode = spac_mode_map.get(getattr(pc, 'spacingMode', 0), "length")
            if s_mode != "length":
                pc_dict["spacingMode"] = s_mode
                
            r_mode = rot_mode_map.get(getattr(pc, 'rotateMode', 0), "tangent")
            if r_mode != "tangent":
                pc_dict["rotateMode"] = r_mode
                
            if clean_float(getattr(pc, 'offsetRotation', 0.0)) != 0: pc_dict["rotation"] = clean_float(pc.offsetRotation)
            if clean_float(getattr(pc, 'position', 0.0)) != 0: pc_dict["position"] = clean_float(pc.position)
            if clean_float(getattr(pc, 'spacing', 0.0)) != 0: pc_dict["spacing"] = clean_float(pc.spacing)
            if clean_float(getattr(pc, 'rotateMix', 1.0)) != 1: pc_dict["rotateMix"] = clean_float(pc.rotateMix)
            if clean_float(getattr(pc, 'translateMix', 1.0)) != 1: pc_dict["translateMix"] = clean_float(pc.translateMix)
            
            pc_json.append(pc_dict)

        # --- Stage 7: Events ---
        events_json = {}
        for ev in self.events:
            ev_dict = {}
            if ev.int_value != 0:
                ev_dict["int"] = ev.int_value
            if clean_float(ev.float_value) != 0:
                ev_dict["float"] = clean_float(ev.float_value)
            if ev.string_value:
                ev_dict["string"] = ev.string_value
            if ev.audio_path:
                ev_dict["audio"] = ev.audio_path
            if clean_float(ev.volume) != 1.0:
                ev_dict["volume"] = clean_float(ev.volume)
            if clean_float(ev.balance) != 0:
                ev_dict["balance"] = clean_float(ev.balance)
                
            events_json[ev.name] = ev_dict

        # --- Stage 6: Skins ---
        def _float_to_color(r, g, b, a):
            return f"{int(r*255):02x}{int(g*255):02x}{int(b*255):02x}{int(a*255):02x}"

        def _build_vertices_array(bones, vertices_raw):
            floats = [struct.unpack('<f', struct.pack('<I', d))[0] for d in vertices_raw]
            if not bones:
                return [clean_float(f) for f in floats]
            out = []
            bone_idx = 0
            float_idx = 0
            while bone_idx < len(bones):
                bone_count = bones[bone_idx]
                out.append(bone_count)
                bone_idx += 1
                for _ in range(bone_count):
                    out.append(bones[bone_idx])
                    bone_idx += 1
                    out.append(clean_float(floats[float_idx]))
                    out.append(clean_float(floats[float_idx+1]))
                    out.append(clean_float(floats[float_idx+2]))
                    float_idx += 3
            return out
            
        def _count_vertices(bones, vertices_raw):
            if not bones:
                return len(vertices_raw) // 2
            count = 0
            idx = 0
            while idx < len(bones):
                bone_count = bones[idx]
                idx += 1 + bone_count
                count += 1
            return count

        skins_json = []
        for skin in self.skins:
            skin_dict = {"name": skin.name, "attachments": {}}
            for att in skin.attachments:
                slot_name = self.slots[att.slot_index].name
                if slot_name not in skin_dict["attachments"]:
                    skin_dict["attachments"][slot_name] = {}
                
                att_dict = {}
                if att.type != 'region':
                    att_dict["type"] = att.type
                
                # atlas_name = the atlas region display name (binary step 3)
                # att.name = the JSON key (binary step 5 / skin placeholder)
                # Output "path" when atlas_name differs from JSON key
                atlas_name = getattr(att, 'atlas_name', None)
                if atlas_name and atlas_name != att.name:
                    if att.type == 'linkedmesh':
                        pass  # linkedmesh path handled separately below
                    else:
                        att_dict["path"] = atlas_name
                    
                if att.type == 'region':
                    floats = [struct.unpack('<f', struct.pack('<I', d))[0] for d in att.dwords[:7]]
                    if clean_float(floats[0]) != 0: att_dict["x"] = clean_float(floats[0])
                    if clean_float(floats[1]) != 0: att_dict["y"] = clean_float(floats[1])
                    if clean_float(floats[2]) != 0: att_dict["rotation"] = clean_float(floats[2])
                    if clean_float(floats[3]) != 1: att_dict["scaleX"] = clean_float(floats[3])
                    if clean_float(floats[4]) != 1: att_dict["scaleY"] = clean_float(floats[4])
                    if clean_float(floats[5]) != 0: att_dict["width"] = clean_float(floats[5])
                    if clean_float(floats[6]) != 0: att_dict["height"] = clean_float(floats[6])
                    
                    color = _float_to_color(*att.color)
                    if color != "ffffffff": att_dict["color"] = color

                elif att.type in ('mesh', 'linkedmesh'):
                    d6_floats = [struct.unpack('<f', struct.pack('<I', d))[0] for d in att.dwords_6]
                    if clean_float(d6_floats[2]) != 0: att_dict["width"] = clean_float(d6_floats[2])
                    if clean_float(d6_floats[3]) != 0: att_dict["height"] = clean_float(d6_floats[3])
                    
                    d10_floats = [struct.unpack('<f', struct.pack('<I', d))[0] for d in att.dwords_10]
                    color = _float_to_color(*d10_floats[6:10])
                    if color != "ffffffff": att_dict["color"] = color
                    
                    if att.type == 'linkedmesh':
                        # parent = the key of the parent mesh attachment in the skin
                        if att.parent_mesh_name and att.parent_mesh_name != att.name:
                            att_dict["parent"] = att.parent_mesh_name
                        # Only output path if atlas_name is different and it's NOT a linkedmesh (linkedmesh paths are inherited)
                        # Wait, linked mesh doesn't have its own path, it uses the parent's path.
                        # So no path here.
                            
                        if att.skin_index != -1 and att.skin_index < len(self.skins):
                            if self.skins[att.skin_index].name != 'default':
                                att_dict["skin"] = self.skins[att.skin_index].name
                        if not att.inherit_deform:
                            att_dict["deform"] = False
                    else:
                        # att.triangles is actually regionUVs (Vector_B in the SCSP binary)
                        # Spine JSON expects regionUVs (0.0-1.0), NOT the mapped atlas UVs.
                        uvs = [clean_float(struct.unpack('<f', struct.pack('<I', d))[0]) for d in att.triangles]
                        if uvs: att_dict["uvs"] = uvs
                        
                        # Vector_B (att.triangles) is regionUVs, which is not exported in JSON.
                        # Vector_C (att.edges) is triangles (int16 array).
                        # Vector_D (att.edges2) is edges (int16 array).
                        if att.edges: att_dict["triangles"] = att.edges
                        if att.edges2: att_dict["edges"] = att.edges2
                        
                        verts = _build_vertices_array(att.va_bones, att.va_vertices)
                        if verts: att_dict["vertices"] = verts
                        if att.dword_190 > 0: att_dict["hull"] = att.dword_190

                elif att.type == 'boundingbox':
                    att_dict["vertexCount"] = _count_vertices(att.bones, att.vertices)
                    verts = _build_vertices_array(att.bones, att.vertices)
                    if verts: att_dict["vertices"] = verts
                    
                elif att.type == 'path':
                    att_dict["vertexCount"] = _count_vertices(att.va_bones, att.va_vertices)
                    if att.closed: att_dict["closed"] = True
                    if not att.constant_speed: att_dict["constantSpeed"] = False
                    lengths = [clean_float(struct.unpack('<f', struct.pack('<I', d))[0]) for d in att.lengths]
                    if lengths: att_dict["lengths"] = lengths
                    verts = _build_vertices_array(att.va_bones, att.va_vertices)
                    if verts: att_dict["vertices"] = verts

                elif att.type == 'clipping':
                    if 0 <= att.end_slot_index < len(self.slots):
                        att_dict["end"] = self.slots[att.end_slot_index].name
                    att_dict["vertexCount"] = _count_vertices(att.va_bones, att.va_vertices)
                    verts = _build_vertices_array(att.va_bones, att.va_vertices)
                    if verts: att_dict["vertices"] = verts

                elif att.type == 'point':
                    x = clean_float(struct.unpack('<f', struct.pack('<I', att.field_0x30))[0])
                    y = clean_float(struct.unpack('<f', struct.pack('<I', att.field_0x34))[0])
                    r = clean_float(struct.unpack('<f', struct.pack('<I', att.field_0x38))[0])
                    if x != 0: att_dict["x"] = x
                    if y != 0: att_dict["y"] = y
                    if r != 0: att_dict["rotation"] = r

                skin_dict["attachments"][slot_name][att.name] = att_dict

            if skin_dict["attachments"]:
                skins_json.append(skin_dict)

        # --- Stage 8: Animations ---
        # All frame layouts verified via IDA apply() decompilation.
        # See: 段8_Timeline帧布局_IDA验証.md

        import numpy as np

        def _dword_to_float(d):
            return struct.unpack('<f', struct.pack('<I', d))[0]

        def _recover_bezier(curve_data_dwords, frame_idx):
            """Recover original bezier control points [cx1,cy1,cx2,cy2] from
            pre-sampled curveData. IDA-verified: 9 uniform (x,y) samples at t=0.1..0.9.
            getCurvePercent @ 0x7FF734F82D30: curveData[19*f+0]=type, [19*f+1..18]=samples.
            P0=(0,0), P3=(1,1), B(t)=3(1-t)²t·C1 + 3(1-t)t²·C2 + t³.
            Least-squares solve for cx1,cx2 and cy1,cy2 independently."""
            base = 19 * frame_idx
            if base + 18 >= len(curve_data_dwords):
                return None
            # Build coefficient matrix for t=0.1,0.2,...,0.9
            A = []
            bx, by = [], []
            for i in range(9):
                t = (i + 1) / 10.0
                a1 = 3 * (1 - t) ** 2 * t
                a2 = 3 * (1 - t) * t ** 2
                A.append([a1, a2])
                sx = _dword_to_float(curve_data_dwords[base + 1 + i * 2])
                sy = _dword_to_float(curve_data_dwords[base + 2 + i * 2])
                bx.append(sx - t ** 3)
                by.append(sy - t ** 3)
            A = np.array(A)
            bx_arr = np.array(bx)
            by_arr = np.array(by)
            cx1, cx2 = np.linalg.lstsq(A, bx_arr, rcond=None)[0]
            cy1, cy2 = np.linalg.lstsq(A, by_arr, rcond=None)[0]
            return [clean_float(cx1), clean_float(cy1), clean_float(cx2), clean_float(cy2)]

        def _get_curve(curve_data_dwords, frame_idx):
            """Return curve value for JSON export.
            IDA: curveData[19*f]=0.0→LINEAR, =1.0→STEPPED, =2.0→BEZIER."""
            base = 19 * frame_idx
            if base >= len(curve_data_dwords):
                return None  # LINEAR default
            type_f = _dword_to_float(curve_data_dwords[base])
            if type_f == 0.0:
                return None  # LINEAR — omit from JSON
            if type_f == 1.0:
                return "stepped"
            # BEZIER
            pts = _recover_bezier(curve_data_dwords, frame_idx)
            return pts  # [cx1, cy1, cx2, cy2] or None

        def _apply_curve_to_frame(frame, cv):
            """Apply curve data to a frame dict in Spine 3.8 JSON format.
            Spine 3.8 spec (SkeletonJson.cpp L704-714): individual keys
            {"curve": cx1, "c2": cy1, "c3": cx2, "c4": cy2}
            NOT an array. 'stepped' is a string value for the 'curve' key."""
            if cv is None:
                return
            if isinstance(cv, str):  # "stepped"
                frame["curve"] = cv
            elif isinstance(cv, list) and len(cv) == 4:
                frame["curve"] = cv[0]  # cx1
                frame["c2"] = cv[1]     # cy1
                frame["c3"] = cv[2]     # cx2
                frame["c4"] = cv[3]     # cy2
            else:
                frame["curve"] = cv  # fallback

        def _export_curve_frames(frames_dwords, curve_data_dwords, stride, field_names):
            """Generic exporter for CurveTimeline-based frame arrays.
            stride: dwords per frame. field_names: list of JSON key names for non-time elements.
            IDA-verified: frames[i*stride+0]=time, frames[i*stride+1..]=values."""
            fc = len(frames_dwords) // stride if stride > 0 else 0
            result = []
            for i in range(fc):
                base = i * stride
                frame = {"time": clean_float(_dword_to_float(frames_dwords[base]))}
                for j, key in enumerate(field_names):
                    val = _dword_to_float(frames_dwords[base + 1 + j])
                    if key.startswith('_int_'):
                        # IDA: (int)*(float*) cast — e.g. bendDirection
                        val = int(val)
                        key = key[5:]
                    elif key.startswith('_bool_'):
                        val = val != 0.0
                        key = key[6:]
                    if key == 'angle':
                        if clean_float(val) != 0:
                            frame[key] = clean_float(val)
                    else:
                        frame[key] = clean_float(val) if isinstance(val, float) else val
                # Curve for frames 0..fc-2 (last frame has no curve)
                if i < fc - 1:
                    cv = _get_curve(curve_data_dwords, i)
                    _apply_curve_to_frame(frame, cv)
                result.append(frame)
            return result

        # Frame stride and field mappings — all IDA apply() verified
        _BONE_TL_MAP = {
            0: (2, ['angle']),        # RotateTimeline [IDA✓ 0x7FF734F5B2D0]
            1: (3, ['x', 'y']),       # TranslateTimeline [IDA✓ 0x7FF734F5B850]
            2: (3, ['x', 'y']),       # ScaleTimeline [ctor same as Translate]
            3: (3, ['x', 'y']),       # ShearTimeline [ctor same as Translate]
        }
        _BONE_TL_JSON_KEY = {0: 'rotate', 1: 'translate', 2: 'scale', 3: 'shear'}

        animations_json = {}
        for anim in self.animations:
            anim_dict = {}

            # Collect timelines by category
            bone_tls = {}      # bone_name -> {rotate:[], translate:[], ...}
            slot_tls = {}      # slot_name -> {attachment:[], color:[], twoColor:[]}
            deform_tls = {}    # skin_name -> slot_name -> attach_name -> frames
            ik_tls = {}        # constraint_name -> frames
            transform_tls = {} # constraint_name -> frames
            path_tls = {}      # constraint_name -> {position/spacing/mix: frames}
            draworder_frames = None
            event_frames = None

            for tl in anim.timelines:
                # ── Bone timelines (Cases 0-3) ──
                if tl.case_id in _BONE_TL_MAP:
                    stride, fields = _BONE_TL_MAP[tl.case_id]
                    json_key = _BONE_TL_JSON_KEY[tl.case_id]
                    bone_name = self.bones[tl.index].name if 0 <= tl.index < len(self.bones) else f"bone_{tl.index}"
                    frames_list = _export_curve_frames(tl.frames_or_values, tl.curve_data, stride, fields)
                    if bone_name not in bone_tls:
                        bone_tls[bone_name] = {}
                    bone_tls[bone_name][json_key] = frames_list

                # ── Case 4: AttachmentTimeline ──
                elif tl.case_id == 4:
                    slot_name = self.slots[tl.slot_index].name if 0 <= tl.slot_index < len(self.slots) else f"slot_{tl.slot_index}"
                    fc = len(tl.frames)
                    frames_list = []
                    for i in range(fc):
                        frame = {"time": clean_float(_dword_to_float(tl.frames[i]))}
                        if i < len(tl.attachment_names) and tl.attachment_names[i]:
                            frame["name"] = tl.attachment_names[i]
                        else:
                            frame["name"] = None
                        frames_list.append(frame)
                    if slot_name not in slot_tls:
                        slot_tls[slot_name] = {}
                    slot_tls[slot_name]["attachment"] = frames_list

                # ── Case 5: ColorTimeline [IDA✓ 0x7FF734F541A0] ──
                elif tl.case_id == 5:
                    slot_name = self.slots[tl.index].name if 0 <= tl.index < len(self.slots) else f"slot_{tl.index}"
                    # stride=5: time, r, g, b, a (all float 0.0~1.0)
                    fc = len(tl.frames_or_values) // 5
                    frames_list = []
                    for i in range(fc):
                        base = i * 5
                        t = clean_float(_dword_to_float(tl.frames_or_values[base]))
                        r = _dword_to_float(tl.frames_or_values[base + 1])
                        g = _dword_to_float(tl.frames_or_values[base + 2])
                        b = _dword_to_float(tl.frames_or_values[base + 3])
                        a = _dword_to_float(tl.frames_or_values[base + 4])
                        color_hex = f"{int(round(r*255)):02x}{int(round(g*255)):02x}{int(round(b*255)):02x}{int(round(a*255)):02x}"
                        frame = {"time": t, "color": color_hex}
                        if i < fc - 1:
                            cv = _get_curve(tl.curve_data, i)
                            _apply_curve_to_frame(frame, cv)
                        frames_list.append(frame)
                    if slot_name not in slot_tls:
                        slot_tls[slot_name] = {}
                    slot_tls[slot_name]["color"] = frames_list

                # ── Case 6: DeformTimeline ──
                elif tl.case_id == 6:
                    skin_name = "default"
                    if 0 <= tl.skin_index < len(self.skins):
                        skin_name = self.skins[tl.skin_index].name
                    slot_name = self.slots[tl.slot_index].name if 0 <= tl.slot_index < len(self.slots) else f"slot_{tl.slot_index}"
                    attach_name = tl.attachment_name or "unknown"
                    fc = len(tl.frames)
                    frames_list = []
                    for i in range(fc):
                        frame = {"time": clean_float(_dword_to_float(tl.frames[i]))}
                        if i < len(tl.per_frame_vertices):
                            verts = [clean_float(_dword_to_float(d)) for d in tl.per_frame_vertices[i]]
                            # Trim trailing zeros
                            while verts and verts[-1] == 0:
                                verts.pop()
                            if verts:
                                # Find offset (first non-zero index)
                                offset = 0
                                while offset < len(verts) and verts[offset] == 0:
                                    offset += 1
                                if offset > 0:
                                    verts = verts[offset:]
                                    frame["offset"] = offset
                                frame["vertices"] = verts
                        if i < fc - 1:
                            cv = _get_curve(tl.curve_data, i)
                            _apply_curve_to_frame(frame, cv)
                        frames_list.append(frame)
                    if skin_name not in deform_tls:
                        deform_tls[skin_name] = {}
                    if slot_name not in deform_tls[skin_name]:
                        deform_tls[skin_name][slot_name] = {}
                    deform_tls[skin_name][slot_name][attach_name] = frames_list

                # ── Case 7: EventTimeline ──
                elif tl.case_id == 7:
                    fc = len(tl.frames)
                    frames_list = []
                    for i in range(fc):
                        frame = {"time": clean_float(_dword_to_float(tl.frames[i]))}
                        if i < len(tl.event_name_refs) and tl.event_name_refs[i]:
                            frame["name"] = tl.event_name_refs[i]
                        frames_list.append(frame)
                    event_frames = frames_list

                # ── Case 8: DrawOrderTimeline [IDA✓ 0x7FF734F56E40] ──
                # Binary stores FULL slot permutation (slot_count ints), NOT (slotIdx,offset) pairs.
                # Must convert to Spine JSON offset format via inverse algorithm.
                # Verified against Spine 3.8 SkeletonJson.cpp L1064-1103 forward algorithm.
                elif tl.case_id == 8:
                    fc = len(tl.frames)
                    slot_count = len(self.slots)
                    frames_list = []
                    for i in range(fc):
                        frame = {"time": clean_float(_dword_to_float(tl.frames[i]))}
                        if i < len(tl.draw_orders):
                            new_order = tl.draw_orders[i]
                            if new_order and len(new_order) == slot_count:
                                # Inverse of Spine's forward algorithm:
                                #   Forward (SkeletonJson.cpp):
                                #     for each {slot_name, offset} sorted by originalIndex:
                                #       drawOrder2[originalIndex + offset] = originalIndex
                                #   Inverse:
                                #     offset = target_position - originalIndex
                                # new_order[draw_pos] = original_slot_index
                                default_order = list(range(slot_count))
                                if list(new_order) != default_order:
                                    # Build: where does each original slot end up?
                                    new_pos_of = [0] * slot_count
                                    for pos, orig in enumerate(new_order):
                                        new_pos_of[orig] = pos
                                    # Emit offset entries for moved slots, sorted by orig index
                                    offsets_list = []
                                    for orig in range(slot_count):
                                        target = new_pos_of[orig]
                                        if target != orig:
                                            slot_name = self.slots[orig].name if 0 <= orig < slot_count else f"slot_{orig}"
                                            offsets_list.append({"slot": slot_name, "offset": target - orig})
                                    if offsets_list:
                                        frame["offsets"] = offsets_list
                        frames_list.append(frame)
                    draworder_frames = frames_list

                # ── Case 9: IkConstraintTimeline [IDA✓ 0x7FF734F580A0] ──
                elif tl.case_id == 9:
                    # stride=6: time, mix, softness, bendDirection(int), compress(bool), stretch(bool)
                    c_name = f"ik_{tl.index}"
                    if 0 <= tl.index < len(self.ik_constraints):
                        c_name = self.ik_constraints[tl.index].name
                    frames_list = _export_curve_frames(
                        tl.frames_or_values, tl.curve_data, 6,
                        ['mix', 'softness', '_int_bendDirection', '_bool_compress', '_bool_stretch']
                    )
                    ik_tls[c_name] = frames_list

                # ── Case 10: TransformConstraintTimeline [IDA✓ 0x7FF734F5EC60] ──
                elif tl.case_id == 10:
                    # stride=5: time, rotateMix, translateMix, scaleMix, shearMix
                    c_name = f"tc_{tl.index}"
                    if 0 <= tl.index < len(self.transform_constraints):
                        c_name = self.transform_constraints[tl.index].name
                    frames_list = _export_curve_frames(
                        tl.frames_or_values, tl.curve_data, 5,
                        ['rotateMix', 'translateMix', 'scaleMix', 'shearMix']
                    )
                    transform_tls[c_name] = frames_list

                # ── Cases 11-13: PathConstraint Timelines [IDA✓] ──
                elif tl.case_id in (11, 12, 13):
                    c_name = f"pc_{tl.index}"
                    if 0 <= tl.index < len(self.path_constraints):
                        c_name = self.path_constraints[tl.index].name
                    if c_name not in path_tls:
                        path_tls[c_name] = {}
                    if tl.case_id == 11:
                        # stride=2: time, position [IDA✓ 0x7FF734F5A390]
                        frames_list = _export_curve_frames(tl.frames_or_values, tl.curve_data, 2, ['position'])
                        path_tls[c_name]["position"] = frames_list
                    elif tl.case_id == 12:
                        # stride=2: time, spacing
                        frames_list = _export_curve_frames(tl.frames_or_values, tl.curve_data, 2, ['spacing'])
                        path_tls[c_name]["spacing"] = frames_list
                    else:  # 13
                        # stride=3: time, rotateMix, translateMix [IDA✓ 0x7FF734F59EE0]
                        frames_list = _export_curve_frames(tl.frames_or_values, tl.curve_data, 3, ['rotateMix', 'translateMix'])
                        path_tls[c_name]["mix"] = frames_list

                # ── Case 14: TwoColorTimeline [IDA✓ 0x7FF734F5F0F0] ──
                elif tl.case_id == 14:
                    slot_name = self.slots[tl.index].name if 0 <= tl.index < len(self.slots) else f"slot_{tl.index}"
                    # stride=8: time, lightR, lightG, lightB, lightA, darkR, darkG, darkB
                    fc = len(tl.frames_or_values) // 8
                    frames_list = []
                    for i in range(fc):
                        base = i * 8
                        t = clean_float(_dword_to_float(tl.frames_or_values[base]))
                        lr = _dword_to_float(tl.frames_or_values[base + 1])
                        lg = _dword_to_float(tl.frames_or_values[base + 2])
                        lb = _dword_to_float(tl.frames_or_values[base + 3])
                        la = _dword_to_float(tl.frames_or_values[base + 4])
                        dr = _dword_to_float(tl.frames_or_values[base + 5])
                        dg = _dword_to_float(tl.frames_or_values[base + 6])
                        db = _dword_to_float(tl.frames_or_values[base + 7])
                        light = f"{int(round(lr*255)):02x}{int(round(lg*255)):02x}{int(round(lb*255)):02x}{int(round(la*255)):02x}"
                        dark = f"{int(round(dr*255)):02x}{int(round(dg*255)):02x}{int(round(db*255)):02x}"
                        frame = {"time": t, "light": light, "dark": dark}
                        if i < fc - 1:
                            cv = _get_curve(tl.curve_data, i)
                            _apply_curve_to_frame(frame, cv)
                        frames_list.append(frame)
                    if slot_name not in slot_tls:
                        slot_tls[slot_name] = {}
                    slot_tls[slot_name]["twoColor"] = frames_list

            # Assemble animation dict
            if bone_tls:
                anim_dict["bones"] = bone_tls
            if slot_tls:
                anim_dict["slots"] = slot_tls
            if ik_tls:
                anim_dict["ik"] = ik_tls
            if transform_tls:
                anim_dict["transform"] = transform_tls
            if path_tls:
                anim_dict["path"] = path_tls
            if deform_tls:
                anim_dict["deform"] = deform_tls
            if draworder_frames:
                anim_dict["drawOrder"] = draworder_frames
            if event_frames:
                anim_dict["events"] = event_frames

            animations_json[anim.name] = anim_dict

        # ── Final assembly ──
        out_dict = {
            "skeleton": skeleton,
            "bones": bones_json
        }
        if slots_json:
            out_dict["slots"] = slots_json
        if ik_json:
            out_dict["ik"] = ik_json
        if tc_json:
            out_dict["transform"] = tc_json
        if pc_json:
            out_dict["path"] = pc_json
        if skins_json:
            out_dict["skins"] = skins_json
        if events_json:
            out_dict["events"] = events_json
        if animations_json:
            out_dict["animations"] = animations_json
            
        return json.dumps(out_dict, indent=4, ensure_ascii=False)

if __name__ == '__main__':
    parser = SCSPV3Parser(r'G:\keasi\unpacked\model\1005.scsp')
    parser.parse()
    
    json_data = parser.export_json()
    out_path = r'G:\keasi\unpacked\model\1005.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(json_data)
    print(f"\nJSON successfully exported to {out_path}")
