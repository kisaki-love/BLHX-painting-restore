"""Immutable jobs and per-painting transactions shared by the CLI and GUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, gettempdir
from contextlib import contextmanager
import gc
import hashlib
import shutil
from uuid import uuid4
import os
import re

import rendering as engine
import dynamic_assets


IMPORT_EXAMPLE = "目录示例：com.bilibili.azurlane / files / AssetBundles"


def locate(folder):
    if not str(folder).strip():
        raise ValueError("请选择游戏包根目录、files 或 AssetBundles。")
    directory = Path(folder).expanduser().resolve()
    for root in (directory, directory / "AssetBundles", directory / "files" / "AssetBundles"):
        if any((root / d).is_dir() for d in ("painting", "live2d", "spinepainting", *dynamic_assets.IMAGE_DIRS["background"], "updatebg")):
            return root
    raise ValueError(f"请选择包含立绘资源的 AssetBundles 根目录。{IMPORT_EXAMPLE}")


def choose(root, only=None, limit=None, asset_kind="painting"):
    names = engine.find_paintings(str(root / "painting"))
    if asset_kind != "painting":
        names = dynamic_assets.catalog(root, asset_kind)
    if only is not None:
        wanted = {name.strip().casefold() for name in only if name.strip()}
        names = [name for name in names if name.casefold() in wanted]
        missing = wanted.difference(n.casefold() for n in names)
        if missing:
            raise ValueError("找不到指定立绘：" + "、".join(sorted(missing)))
    if not names or (limit is not None and limit < 1):
        raise ValueError("没有可处理的立绘。")
    return tuple(names[:limit])


@dataclass(frozen=True)
class Job:
    root: Path
    output: Path
    names: tuple[str, ...]
    check: bool = False
    expressions: bool = True
    layers: bool = False
    align: bool = True
    report_name: str = "素材检查报告.txt"
    asset_kind: str = "painting"
    hide_hx: bool = False


@dataclass(frozen=True)
class Result:
    name: str
    status: str
    stage: str
    detail: str = ""

    def __str__(self):
        return " · ".join(filter(None, (self.name, self.status, self.stage, self.detail)))


@dataclass(frozen=True)
class Event:
    kind: str
    done: int
    name: str
    stage: str
    result: Result | None = None


class Cancelled(Exception):
    pass


def safe_name(name):
    if not name or name in {".", ".."} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or Path(name).is_reserved():
        raise ValueError(f"资源名不适合用作文件名：{name}")
    return name


@contextmanager
def staging(output):
    # Normal directory permissions are inherited by the final PNGs on Windows.
    tmp = output / (".blhx-" + uuid4().hex)
    tmp.mkdir()
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp)


@contextmanager
def output_lock(target):
    key = hashlib.sha256(os.path.normcase(str(target)).encode()).hexdigest()
    with open(Path(gettempdir()) / ("blhx-" + key + ".lock"), "a+b") as lock:
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def publish(stage, target, name, *, flat_images=False):
    """Replace only files owned by this exporter; undo replacements on write errors."""
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
        raise ValueError("输出子目录是链接，请选择普通文件夹。")
    if not target.exists():
        stage.rename(target)
        return
    if flat_images:
        managed = re.compile(re.escape(name.replace("~", "~~")) + r"(?:~(?:[2-9]|[1-9][0-9]+))?\.png")
    else:
        managed = re.compile(re.escape(name) + r"(?:_exp(?:_[1-9][0-9]*)?|__layer__.+)?\.png")
    old = [p for p in target.iterdir() if managed.fullmatch(p.name)]
    backup = stage.parent / "previous"
    backup.mkdir()
    moved, new = [], []
    try:
        for p in old:
            if not p.is_file():
                raise ValueError(f"同名输出不是文件：{p}")
            p.rename(backup / p.name)
            moved.append(p)
        for p in stage.iterdir():
            dest = target / p.name
            p.rename(dest)
            new.append(dest)
    except Exception:
        for dest in reversed(new):
            dest.rename(stage / dest.name)
        for dest in reversed(moved):
            (backup / dest.name).rename(dest)
        raise


def process(job, name, stage, folder):
    painting = str(job.root / "painting")
    faces_dir = str(job.root / "paintingface")
    stage("读取配置")
    pf = engine.read_prefab(str(Path(painting) / name), name)
    if not pf.layers:
        raise ValueError("配置中没有可还原的图层")
    def layer(label, image):
        image.save(folder / f"{name}__layer__{safe_name(label)}.png")

    probe = {}
    base, missing = engine.composite(pf, painting, probe=probe, stage=stage,
                                     export_layer=layer if job.layers and folder else None)
    issues = ["缺少图层：" + n for n in missing if not engine.UI_LIKE.match(n) and not engine.NUM_LIKE.match(n)]
    stage("读取表情")
    faces, sprites = engine.load_face_pack(faces_dir, name) if pf.face_rect else ({}, {})
    if pf.face_rect and not faces and (Path(faces_dir) / name).is_file():
        issues.append("表情包存在，但未能读取可用贴图")
    default = None
    if probe.get("hole"):
        default = engine.pick_default_face(pf, faces, sprites, faces_dir, name)
        if default is None:
            issues.append("缺少补全主图所需的默认脸")
    if folder is not None:
        offset = (0, 0)
        if job.align and faces:
            stage("对齐表情")
            offset = engine.align_face(next(iter(faces.values())), base, pf.face_rect)
        stage("保存主图")
        path = folder / f"{name}.png"
        if default is None:
            base.save(path)
        else:
            with base.copy() as main:
                engine.paste_face(main, pf.face_rect, default, offset)
                main.save(path)
        if job.expressions:
            for i, face in enumerate(engine.expression_faces(faces.values(), default), 1):
                stage(f"保存表情 {i}")
                with base.copy() as image:
                    engine.paste_face(image, pf.face_rect, face, offset)
                    image.save(folder / f"{name}_exp_{i}.png")
    return issues


def execute(job, notify=lambda event: None, stopped=lambda: False):
    if not job.names:
        raise ValueError("未选择立绘。")
    root = job.root.resolve()
    output = job.output.resolve()
    if job.asset_kind in ("spine", "live2d"):
        output = (output / dynamic_assets.SOURCES[job.asset_kind]).resolve()
    if output == root or root in output.parents:
        raise ValueError("请选择游戏资源目录之外的输出目录。")
    results = []
    for index, name in enumerate(job.names):
        engine._AB_CACHE.clear()
        phase = "准备"
        notes = []
        def stage(label):
            nonlocal phase
            if stopped():
                raise Cancelled
            phase = label
            notify(Event("stage", index, name, label))
        try:
            stage("准备")
            safe_name(name)
            output.mkdir(parents=True, exist_ok=True)
            if job.check:
                issues = process(job, name, stage, None)
                stage("检查完毕")
            else:
                with staging(output) as temp:
                    folder = Path(temp) / "new"
                    folder.mkdir()
                    if job.asset_kind == "painting":
                        issues = process(job, name, stage, folder)
                    else:
                        issues = dynamic_assets.export_assets(job, name, stage, folder, notes)
                    stage("提交文件")
                    with output_lock(output / name):
                        if job.asset_kind == "painting":
                            publish(folder, output / name, name)
                        elif job.asset_kind in dynamic_assets.IMAGE_DIRS:
                            publish(folder, output, name, flat_images=True)
                        else:
                            target = output / name
                            suffix = 1
                            while target.exists():
                                suffix += 1
                                target = output / f"{name} ({suffix})"
                            folder.rename(target)
            status = "部分完成" if issues else ("已导出（有说明）" if notes else "成功")
            result = Result(name, status, phase, "\n".join(issues + notes))
        except Cancelled:
            for pending in job.names[index:]:
                result = Result(pending, "取消", phase if pending == name else "尚未开始")
                results.append(result)
                notify(Event("result", index, pending, result.stage, result))
            break
        except Exception as exc:
            result = Result(name, "失败", phase, str(exc) or type(exc).__name__)
        finally:
            engine._AB_CACHE.clear()
            # UnityPy object readers form cycles holding open Windows file handles.
            gc.collect()
        results.append(result)
        notify(Event("result", index + 1, name, result.stage, result))
    if job.check and not stopped():
        text = "\n".join(map(str, results)) + "\n"
        with NamedTemporaryFile(dir=output, mode="w", encoding="utf-8-sig", delete=False) as tmp:
            path = Path(tmp.name)
        try:
            path.write_text(text, encoding="utf-8-sig")
            os.replace(path, output / safe_name(job.report_name))
        finally:
            path.unlink(missing_ok=True)
    return results
