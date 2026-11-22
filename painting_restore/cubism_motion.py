"""Convert supported Unity Cubism scalar bindings to motion3 curves."""
import math
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

ATTRS = {
    ("CubismParameter", "Value"): "Parameter",
    ("CubismPart", "Opacity"): "PartOpacity",
    ("CubismRenderController", "Opacity"): "Model",
}


def crc(text):
    return zlib.crc32(text.encode("utf8"))


@dataclass
class Bindings:
    direct: dict
    scripts: dict
    controllers: dict
    groups: dict
    missing: dict
    complete_targets: dict
    path_hashes: set


def model_ids(objects):
    """Read the fixed-width parameter/part ID tables of little-endian Cubism 3–5 MOCs."""
    for obj in objects.values():
        if obj.type.name != "MonoBehaviour" or b"MOC3" not in obj.get_raw_data()[:2048]:
            continue
        row = obj.read_typetree()
        data = bytes(row.get("_bytes", []))
        if len(data) < 704 or data[:4] != b"MOC3" or data[4] not in range(1, 6) or data[5]:
            continue
        offsets = struct.unpack_from("<160I", data, 64)
        if not 704 <= offsets[0] <= len(data)-24:
            continue
        result = {}
        for target, count_index, id_section in (("Parameter", 5, 50), ("PartOpacity", 0, 3)):
            count = struct.unpack_from("<I", data, offsets[0]+count_index*4)[0]
            start = offsets[id_section]
            if start < 704 or start + count*64 > len(data):
                return {}
            records = [data[start+i*64:start+(i+1)*64].partition(b"\0")[0] for i in range(count)]
            try:
                result[target] = [record.decode("utf8") for record in records]
            except UnicodeDecodeError:
                return {}
            if len(set(result[target])) != count or any(not name for name in result[target]):
                return {}
        return result
    return {}


@lru_cache(maxsize=1)
def numbered_paths():
    return {target: {crc(f"{folder}/{prefix}{n}"): f"{prefix}{n}" for n in range(4096)}
            for target, folder, prefix in (("Parameter", "Parameters", "Param"),
                                           ("PartOpacity", "Parts", "Part"))}


def binding_table(objects):
    """Identify each binding by prefab path, component script and serialized property."""
    game_names = {}
    hierarchy = {}
    scripts = {}
    for pid, obj in objects.items():
        kind = obj.type.name
        if kind == "GameObject":
            game_names[pid] = obj.peek_name()
        elif kind in ("Transform", "RectTransform"):
            row = obj.read_typetree()
            hierarchy[pid] = (row["m_GameObject"]["m_PathID"], row["m_Father"]["m_PathID"])
        elif kind == "MonoScript":
            scripts[pid] = obj.read_typetree()["m_ClassName"]
    paths = {}
    for tid, (owner, _) in hierarchy.items():
        parts = []
        current = tid
        while current in hierarchy:
            node, current = hierarchy[current]
            parts.append(game_names[node])
        paths[owner] = "/".join(reversed(parts[:-1]))
    native = model_ids(objects)
    table = {}
    marker_owners = defaultdict(set)
    parameter_names = {}
    controller_rows = []
    missing = {}
    path_hashes = {crc(path) for path in paths.values()}
    component_ids = defaultdict(set)
    complete_targets = {}
    complete_tree = (len(paths) == len(game_names) and
                     sum(parent == 0 for _, parent in hierarchy.values()) == 1 and
                     all(not parent or parent in hierarchy for _, parent in hierarchy.values()))
    targets = {"CubismParameter": "Parameter", "CubismPart": "PartOpacity"}
    for obj in objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        row = obj.read_typetree()
        script = row["m_Script"]["m_PathID"]
        owner = row["m_GameObject"]["m_PathID"]
        cls_name = scripts.get(script)
        if cls_name in ("CubismMouthParameter", "CubismEyeBlinkParameter") and row.get("m_Enabled", 1):
            marker_owners[cls_name].add(owner)
        if cls_name in ("CubismMouthController", "CubismEyeBlinkController") and paths.get(owner) == "" and row.get("m_Enabled", 1):
            controller_rows.append((cls_name, script, row))
        for (cls, prop), target in ATTRS.items():
            if scripts.get(script) == cls and prop in row and owner in paths:
                key = crc(paths[owner]), crc(prop), script
                if target == "Model" and paths[owner]:
                    continue
                name = game_names[owner]
                index = row.get("_unmanagedIndex", -1)
                if 0 <= index < len(native.get(target, [])):
                    name = native[target][index]
                component_ids[script].add(name)
                value = target, ("Opacity" if target == "Model" else name)
                if target == "Parameter":
                    parameter_names[owner] = name
                if key in table and table[key] != value:
                    raise ValueError("绑定路径哈希存在歧义")
                table[key] = value
    for script, cls in scripts.items():
        target = targets.get(cls)
        if target not in native:
            continue
        folder, prop = ("Parts", "Opacity") if target == "PartOpacity" else ("Parameters", "Value")
        for name in native[target]:
            table.setdefault((crc(f"{folder}/{name}"), crc(prop), script), (target, name))
        present = set(native[target])
        if complete_tree and component_ids[script] == present:
            complete_targets[script] = crc(prop)
        missing[script] = {h: name for h, name in numbered_paths()[target].items()
                           if h not in path_hashes and name not in present}

    controllers, groups = {}, {}
    for cls, script, row in controller_rows:
        blink = cls == "CubismEyeBlinkController"
        marker = "CubismEyeBlinkParameter" if blink else "CubismMouthParameter"
        effect, prop = ("EyeBlink", "EyeOpening") if blink else ("LipSync", "MouthOpening")
        if prop not in row:
            continue
        owners = marker_owners[marker]
        if not owners.issubset(parameter_names):
            continue
        ids = sorted({parameter_names[owner] for owner in owners})
        key = (0, crc(prop), script)
        if not ids or row.get("BlendMode") == 0:
            controllers[key] = [("Parameter", name) for name in ids]
        elif row.get("BlendMode") == (2 if blink else 1):
            controllers[key] = [("Model", effect)]
            groups[effect] = ids
    return Bindings(table, scripts, controllers, groups, missing, complete_targets, path_hashes)


def stream_tracks(block):
    count = block["curveCount"]
    tracks = [[] for _ in range(count)]
    words = block["data"]
    data = struct.pack(f"<{len(words)}I", *words)
    pos = 0
    while pos < len(data):
        time, size = struct.unpack_from("<fi", data, pos)
        pos += 8
        if size < 0 or pos + size*20 > len(data):
            raise ValueError("StreamedClip 帧长度异常")
        for _ in range(size):
            idx, a,b,c,v = struct.unpack_from("<i4f", data, pos)
            pos += 20
            if not 0 <= idx < count:
                raise ValueError("StreamedClip 曲线索引异常")
            if time < -1e30:
                # Unity's initial-value sentinel, superseded by a key at zero.
                tracks[idx].append((0.,0.,0.,0.,v))
            elif math.isfinite(time):
                key = (time,a,b,c,v)
                if tracks[idx] and tracks[idx][-1][0] == time:
                    tracks[idx][-1] = key
                else:
                    tracks[idx].append(key)
    return tracks


def segments(keys, start, stop):
    if not keys:
        raise ValueError("曲线没有关键帧")
    if keys[0][0] > start:
        keys = [(start,0,0,0,keys[0][-1])] + keys
    if keys[-1][0] < stop:
        keys = keys + [(stop,0,0,0,keys[-1][-1])]
    result = []
    nseg = npoint = 0
    for (t,a,b,c,v), (end,_,_,_,next_v) in zip(keys,keys[1:]):
        left, right = max(start,t), min(stop,end)
        if right <= left:
            continue
        stepped = (a==b==c==0) or not all(map(math.isfinite,(a,b,c)))
        def sample(x):
            return v if stepped else ((a*x+b)*x+c)*x+v
        u,w = left-t,right-t
        v0 = sample(u)
        v1 = next_v if right==end else sample(w)
        if not result:
            result.extend([left-start,v0])
            npoint = 1
        span = right-left
        if stepped or (a==b==0):
            result.extend([2 if stepped and v0!=v1 else 0,right-start,v1])
            npoint += 1
        else:
            slope0 = (3*a*u+2*b)*u+c
            slope1 = (3*a*w+2*b)*w+c
            result.extend([1,left-start+span/3,v0+slope0*span/3,
                           right-start-span/3,v1-slope1*span/3,right-start,v1])
            npoint += 3
        nseg += 1
    if not result or not all(map(math.isfinite,result)):
        raise ValueError("曲线时间范围或数值异常")
    return result,nseg,npoint


def convert(obj, bindings):
    table, scripts = bindings.direct, bindings.scripts
    clip = obj.read_typetree()
    m = clip["m_MuscleClip"]
    start,stop = m["m_StartTime"],m["m_StopTime"]
    fps = clip["m_SampleRate"]
    raw_bindings = clip["m_ClipBindingConstant"]["genericBindings"]
    if not raw_bindings:
        return None, {"empty": 1}
    if not (math.isfinite(stop-start) and stop>start and fps>0):
        raise ValueError("动作时长或帧率无效")
    data = m["m_Clip"].get("data",m["m_Clip"])
    stream = data["m_StreamedClip"]
    if stream.get("discreteCurveCount",0):
        raise ValueError("不支持离散绑定的布局")
    streamed = stream_tracks(stream)
    dense = data["m_DenseClip"]
    nd = dense["m_CurveCount"]
    values = dense["m_SampleArray"]
    frames = dense["m_FrameCount"]
    constants = data.get("m_ConstantClip",{}).get("data",[])
    if nd and (len(values)!=nd*frames or dense["m_SampleRate"]<=0):
        raise ValueError("DenseClip 采样表无效")
    direct, overrides = {}, {}
    nseg = npoint = idx = 0
    diagnostics = {}
    for binding in raw_bindings:
        key = binding["path"], binding["attribute"], binding["script"]["m_PathID"]
        mapping = table.get(key) if binding["typeID"]==114 else None
        controlled = bindings.controllers.get(key) if binding["typeID"] == 114 else None
        if mapping or controlled:
            if idx < len(streamed):
                keys = streamed[idx]
            elif idx < len(streamed)+nd:
                col = idx-len(streamed)
                rate = dense["m_SampleRate"]
                samples = values[col::nd]
                keys = [(dense["m_BeginTime"]+i/rate,0,0,(samples[i+1]-v)*rate if i+1<frames else 0,v)
                        for i,v in enumerate(samples)]
            else:
                keys = [(start,0,0,0,constants[idx-len(streamed)-nd])]
            seq,ns,np = segments(keys,start,stop)
            destination = direct if mapping else overrides
            for target in ([mapping] if mapping else controlled):
                if target in destination:
                    raise ValueError("同一参数有重复轨道")
                destination[target] = seq, ns, np
        else:
            cls = scripts.get(key[2], f"Unity 类型 {binding['typeID']}")
            absent = bindings.missing.get(key[2], {}).get(key[0])
            label = f"源模型无目标 {absent}" if absent else cls
            if (not absent and binding["typeID"] == 114 and bindings.complete_targets.get(key[2]) == key[1]
                    and key[0] not in bindings.path_hashes):
                label = f"预制体路径不存在 {cls}"
            if controlled == []:
                label = f"控制器无目标 {cls}"
            diagnostics.setdefault(label, []).append({"path_crc":key[0], "property_crc":key[1],
                                                       "script_path_id":str(key[2])})
        idx += ({1:3,2:4,3:3,4:3}.get(binding["attribute"],1)
                if binding["typeID"]==4 else 1)
    if idx!=len(streamed)+nd+len(constants):
        raise ValueError("轨道数量不一致或无可识别 Cubism 参数")
    direct.update(overrides)
    curves = []
    for target in ("Model", "Parameter", "PartOpacity"):
        for (kind, name), (seq, ns, np) in direct.items():
            if kind == target:
                curves.append({"Target":kind,"Id":name,"Segments":seq})
                nseg += ns
                npoint += np
    if not curves:
        return None, diagnostics
    meta = dict(Duration=stop-start,Fps=fps,Loop=m.get("m_LoopTime",False),
                AreBeziersRestricted=True,CurveCount=len(curves),TotalSegmentCount=nseg,
                TotalPointCount=npoint,UserDataCount=0,TotalUserDataSize=0)
    return {"Version":3,"Meta":meta,"Curves":curves},diagnostics
