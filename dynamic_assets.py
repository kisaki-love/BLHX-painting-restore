"""Export source assets; Unity AnimationClips are preserved as serialized objects."""
import json
from pathlib import Path
import re

import rendering
import cubism_motion

SOURCES = {"spine": "spinepainting", "live2d": "live2d"}


def catalog(root, kind):
    directory = root / SOURCES[kind]
    if not directory.is_dir():
        return ()
    return tuple(sorted(f.name for f in directory.iterdir()
                        if f.is_file() and not f.name.endswith(("_res", ".manifest"))))


def asset_filename(name):
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:110]
    if not clean or Path(clean).is_reserved():
        return "_" + clean
    return clean


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf8")


def spine_asset_scope(primary, loaded):
    """Resolve prefab -> skeleton -> atlas -> materials using CAB and path IDs."""
    objects = {(obj.assets_file.name.casefold(), obj.path_id): obj
               for group in loaded.values() for obj in group.values()}
    def resolve(owner, pointer):
        file_id = pointer["m_FileID"]
        cab = owner.assets_file.name if not file_id else owner.assets_file.externals[file_id-1].path.rsplit("/",1)[-1]
        key = (cab.casefold(), pointer["m_PathID"])
        if key not in objects:
            raise ValueError(f"缺少 Spine 引用：{cab} / {pointer['m_PathID']}")
        return objects[key]

    selected = set()
    def include(obj):
        selected.add((obj.assets_file.name.casefold(), obj.path_id))

    for component in loaded[primary].values():
        if component.type.name != "MonoBehaviour":
            continue
        pointer = component.read_typetree().get("skeletonDataAsset")
        if not pointer or not pointer.get("m_PathID"):
            continue
        skeleton = resolve(component, pointer)
        data = skeleton.read_typetree()
        include(resolve(skeleton, data["skeletonJSON"]))
        for atlas_ref in data["atlasAssets"]:
            atlas = resolve(skeleton, atlas_ref)
            atlas_data = atlas.read_typetree()
            include(resolve(atlas, atlas_data["atlasFile"]))
            for material_ref in atlas_data["materials"]:
                material = resolve(atlas, material_ref)
                slots = material.read_typetree()["m_SavedProperties"]["m_TexEnvs"]
                for slot, value in slots:
                    texture = value["m_Texture"]
                    if texture["m_PathID"] and slot == "_MainTex":
                        include(resolve(material, texture))
    if not selected:
        raise ValueError("共享包中有多个版本，未能确定所选模型的骨骼引用。")
    return selected


def export_assets(job, name, stage, folder):
    kind = job.asset_kind
    directory = job.root / SOURCES[kind]
    bundles = [directory / name]
    for extra in (directory / (name + "_res"), job.root / "live2dmask" / name):
        if extra.is_file() and (extra.parent == directory or kind == "live2d"):
            bundles.append(extra)
    if kind == "spine" and name.casefold().endswith("_hx"):
        shared = directory / (name[:-3] + "_res")
        if shared.is_file() and len(bundles) == 1:
            bundles.append(shared)
    loaded = {}
    filtered = {}
    if kind == "spine":
        for bundle in bundles:
            stage("解析 Spine 引用 · " + bundle.name)
            loaded[bundle] = rendering._load_ab(str(bundle))[1]
        for bundle, group in loaded.items():
            skeletons = [o for o in group.values() if o.type.name == "TextAsset"
                         and (o.peek_name() or "").lower().endswith((".skel", ".skel.bytes"))]
            if len(skeletons) > 1 or (bundle != bundles[0] and name.casefold().endswith("_hx")):
                filtered[bundle] = spine_asset_scope(bundles[0], loaded)
    files, problems = [], []
    textures, mocs, physics, poses, expressions = {}, [], [], [], []
    motions = {}
    has_skeleton = has_atlas = False
    for bundle in bundles:
        stage("读取资源包 · " + bundle.name)
        objects = loaded[bundle] if kind == "spine" else rendering._load_ab(str(bundle))[1]
        mask = bundle.parent.name == "live2dmask"
        bindings = None
        source_bundle = bundle.relative_to(job.root).as_posix()
        for obj in objects.values():
            typ = obj.type.name
            if bundle in filtered and typ in ("TextAsset","Texture2D"):
                if (obj.assets_file.name.casefold(), obj.path_id) not in filtered[bundle]:
                    continue
            if typ not in ("TextAsset", "Texture2D", "AnimationClip", "MonoBehaviour"):
                continue
            if typ == "MonoBehaviour":
                if kind != "live2d" or b"MOC3" not in obj.get_raw_data()[:2048]:
                    continue
            stage("提取 " + typ)
            try:
                label = asset_filename(obj.peek_name() or str(obj.path_id))
                if typ == "AnimationClip":
                    relative = Path("AnimationClip") / (label + ".animclip.bin")
                    payload = obj.get_raw_data()
                elif typ == "MonoBehaviour":
                    payload = bytes(obj.read_typetree()["_bytes"])
                    if not payload.startswith(b"MOC3"):
                        continue
                    relative = Path(label + ".moc3")
                elif typ == "Texture2D":
                    relative = Path(label + ".png")
                    if kind == "live2d":
                        relative = Path("mask" if mask else "textures") / relative
                else:
                    value = obj.read().m_Script
                    payload = value.encode("utf8","surrogateescape") if isinstance(value,str) else bytes(value)
                    if label.lower().endswith((".physics3",".pose3",".exp3",".model3",".motion3")):
                        label += ".json"
                    relative = Path(label)
                    if label.casefold().endswith(".model3.json") or label.casefold()=="manifest.json":
                        relative = Path("source") / relative
                target = folder / relative
                if target.exists():
                    relative = relative.with_name(f"{obj.path_id}_{relative.name}")
                    target = folder / relative
                target.parent.mkdir(parents=True,exist_ok=True)
                if typ == "Texture2D":
                    obj.read().image.save(target,format="PNG")
                else:
                    target.write_bytes(payload)
                ref = relative.as_posix()
                files.append(dict(file=ref, type=typ, path_id=str(obj.path_id),
                                  source_bundle=source_bundle))
                if typ == "AnimationClip" and kind == "live2d" and not mask:
                    try:
                        if bindings is None:
                            bindings = cubism_motion.binding_table(objects)
                        motion, skipped = cubism_motion.convert(obj, bindings)
                        filename = relative.name.removesuffix(".animclip.bin") + ".motion3.json"
                        motion_path = Path("motions") / filename
                        (folder / "motions").mkdir(exist_ok=True)
                        (folder / motion_path).write_text(json.dumps(motion,allow_nan=False,separators=(",",":")),encoding="utf8")
                        motions.setdefault(label,[]).append({"File":motion_path.as_posix()})
                        files.append(dict(file=motion_path.as_posix(),type="CubismMotion",source_clip=ref))
                        if skipped:
                            problems.append(f"{label}：跳过 {skipped} 条非 Cubism 或未匹配绑定")
                    except Exception as exc:
                        problems.append(f"{label}：已保留原始动作，转换失败：{exc}")
                if mask:
                    continue
                if typ == "MonoBehaviour":
                    mocs.append(ref)
                elif typ == "Texture2D":
                    match = re.fullmatch(r"texture[_-]?(\d+)",label,re.I)
                    if match and kind=="live2d":
                        index=int(match[1])
                        if index in textures:
                            problems.append("重复的 Live2D 贴图索引：" + label)
                        textures[index]=ref
                elif typ == "TextAsset":
                    low=label.lower()
                    has_skeleton |= low.endswith((".skel",".skel.bytes",".json")) and kind=="spine"
                    has_atlas |= low.endswith((".atlas",".atlas.txt"))
                    if low.endswith(".physics3.json"): physics.append(ref)
                    if low.endswith(".pose3.json"): poses.append(ref)
                    if low.endswith(".exp3.json"): expressions.append({"Name":label[:-10],"File":ref})
            except Exception as exc:
                problems.append(f"{typ} ({obj.path_id})：{exc}")
    if kind == "live2d":
        stage("生成模型入口")
        if len(mocs)==1 and textures and sorted(textures)==list(range(len(textures))):
            refs={"Moc":mocs[0],"Textures":[textures[i] for i in range(len(textures))]}
            if len(physics)==1: refs["Physics"]=physics[0]
            if len(poses)==1: refs["Pose"]=poses[0]
            if expressions: refs["Expressions"]=expressions
            if motions: refs["Motions"]=motions
            write_json(folder/(name+".model3.json"),{"Version":3,"FileReferences":refs})
        else:
            problems.append("模型或贴图索引不完整，无法生成可靠的 model3.json。")
    elif not (has_skeleton and has_atlas and any(f["type"]=="Texture2D" for f in files)):
        problems.append("缺少骨骼、图集描述或贴图。")
    write_json(folder/"manifest.json",dict(kind=kind, files=files, issues=problems))
    return problems
