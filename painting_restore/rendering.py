# -*- coding: utf-8 -*-
"""
碧蓝航线 静态立绘还原工具  (Azur Lane Painting Restore)
=====================================================

原理
----
碧蓝航线(painting 目录)中, 一张立绘被拆成若干 AssetBundle:

    painting/<立绘名>          -> 配置包: 内含 Prefab(GameObject 树 + RectTransform)
                                  决定各图层的位置/缩放/画布尺寸
    painting/<图层名>_tex      -> 图像包: Texture2D(打散成方块的大图) + Mesh(方块摆放网格)

还原分两步:

1) 图层还原 (Mesh 拼图)
   每个 Texture2D 是被切成 N 个矩形块拼在一张图集里的. Mesh(obj) 里每个四边形
   给出 4 个顶点坐标(v, 目标位置) 与 4 个 UV(vt, 在图集中的位置).
   把每个块按 UV 裁出来贴到对应顶点位置, 即可拼回该图层的完整图像.

2) 图层合成 (Prefab 布局)
   读配置包里 Prefab 的 GameObject/RectTransform 层级, 按 Unity UI 的锚点规则
   算出每个 Image 图层在画布中的矩形(origin, size), 画布尺寸 = 根节点 RectTransform
   的 sizeDelta. 把各图层按其矩形缩放后叠上去即得到完整立绘.

   表情差分: paintingface/<立绘名> 里是若干张 (271x202 之类) 面部贴图,
   贴到 Prefab 中名为 "face" 的节点矩形上, 就得到 <立绘名>_exp_N.png.

   注意: 新立绘的人物层在脸部是**挖空**的, face 节点引用了 paintingface 里的
   0 号表情(默认脸), 必须把它也贴回去, 否则成品脸上是一块纯色方块(透出背景).
   老立绘的脸画在人物层里, face 节点的 Sprite 为空, 这时不补(否则会重影).

依赖: pillow, numpy, UnityPy
用法: 见 启动工具.bat / README.md
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
import time
import traceback
import warnings

from . import __version__ as VERSION

try:
    import numpy as np
    from PIL import Image, ImageFile
    ImageFile.LOAD_TRUNCATED_IMAGES = True
except Exception:  # pragma: no cover
    print("缺少依赖: pillow / numpy。请先运行 启动工具.bat 自动安装。")
    raise

# UnityPy 在包内没写版本号时会刷一堆 UnityVersionFallbackWarning, 屏蔽掉(不影响结果)
warnings.filterwarnings("ignore", message="No valid Unity version found.*")
try:
    import UnityPy
    UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0"
except Exception:  # pragma: no cover
    pass

try:                       # 表情自动对齐用, 没有也能跑(只是不做纠正)
    import cv2
    _HAS_CV2 = True
except Exception:          # pragma: no cover
    _HAS_CV2 = False

# ----------------------------------------------------------------------------
# 常量
# ----------------------------------------------------------------------------
APP_TITLE = "Azur Lane Painting Restore"

Y_BIAS = 1  # 与官方还原结果对齐的 1 像素纵向补偿(实测)

# 排除的图层后缀: 这些是附属包(人物/背景/前景/和谐等), 不是"主立绘配置包"
SUB_SUFFIX = re.compile(r"_(n|rw|front|bj\d*|dec|group|face)$", re.I)

# 纯 UI 子树(按钮/商店标签/信息栏等), 不属于立绘画面
UI_SUBTREE_BAN = {"Touch", "frameContain"}
# 需要跳过的图层名: 商店和谐标签、阴影
LAYER_NAME_BAN = re.compile(r"^(shop_hx|shadow)", re.I)

RE_V = re.compile(r"^v\s(-?[\d.]+)\s(-?[\d.]+)\s(-?[\d.]+)$")
RE_VT = re.compile(r"^vt\s([\d.]+)\s([\d.]+)$")
RE_F = re.compile(r"^f\s(\d+)/\d+/\d+\s(\d+)/\d+/\d+\s(\d+)/\d+/\d+$")


def log(*a):
    print(*a, flush=True)


# ============================================================================
# 一、Mesh 拼图
# ============================================================================
def parse_obj(text: str):
    """解析 obj, 返回 (顶点列表, UV列表, 三角形面列表[3个1based顶点索引])."""
    vs, vts, faces = [], [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("v "):
            m = RE_V.match(line)
            if m:
                vs.append((float(m.group(1)), float(m.group(2))))
        elif line.startswith("vt "):
            m = RE_VT.match(line)
            if m:
                vts.append((float(m.group(1)), float(m.group(2))))
        elif line.startswith("f "):
            m = RE_F.match(line)
            if m:
                faces.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return vs, vts, faces


def restore_layer(tex: Image.Image, obj_text: str, meta: dict | None = None
                  ) -> Image.Image | None:
    """用一个 Texture2D + 一个 Mesh(obj 文本) 拼回该图层的完整图像。

    与 AzurLanePaintingExtract 的算法完全一致(已逐像素比对验证)。

    注意: mesh 顶点坐标是**图层原稿的像素坐标**, 其原点 (0,0) 对应 Unity 里
    该 Image 矩形的左下角。因此拼出的图像还必须让调用方知道 mesh 顶点在
    自身坐标里的位置(meta), 否则像 baifeng_bj 这种「顶点不贴着原点」的图层
    会被贴错位置(y 方向整段偏移)。
    meta 会写入: ymax(顶点最高 y) / ymin / xmax。
    """
    vs, vts, faces = parse_obj(obj_text)
    if not vs or not faces:
        return None

    W, H = tex.size
    # UV -> 图集像素坐标 (Unity 的 v 轴向上, PIL 向下, 需翻转)
    tex_pts = [(round(u * W), round((1.0 - v) * H)) for u, v in vts]

    # obj 顶点 x 为负(Unity 导出习惯), 取绝对值; y 轴翻转到图像坐标
    ymax = max(y for _, y in vs)
    draw_pts = [(abs(x), ymax - y) for x, y in vs]

    cw = int(round(max(p[0] for p in draw_pts)))
    ch = int(round(max(p[1] for p in draw_pts)))
    if meta is not None:
        meta["ymax"] = float(ymax)
        meta["ymin"] = float(min(y for _, y in vs))
        meta["xmax"] = float(cw)
    canvas = Image.new("RGBA", (cw, ch), (255, 255, 255, 0))

    for f in faces:
        dp = [draw_pts[i - 1] for i in f]
        tp = [tex_pts[i - 1] for i in f]
        # 图集裁剪区域
        box = (round(min(p[0] for p in tp)), round(min(p[1] for p in tp)),
               round(max(p[0] for p in tp)), round(max(p[1] for p in tp)))
        # 目标位置
        pos = (round(min(p[0] for p in dp)), round(min(p[1] for p in dp)))
        block = tex.crop(box)
        if block.width <= 0 or block.height <= 0:
            continue
        canvas.paste(block, pos)  # 直接覆盖粘贴, 与参考实现一致(顺序敏感)
    return canvas


# ============================================================================
# 二、Prefab 布局计算 (Unity UI / RectTransform)
# ============================================================================
def _v2(d):
    return (float(d.get("x", 0.0)), float(d.get("y", 0.0)))


def rect_of(r: dict, parent):
    """按 Unity UI 锚点规则计算矩形.

    返回 (origin_x, origin_y, w, h); origin 为矩形左下角(Unity 坐标, y 向上).
    parent 为父节点矩形 (ox, oy, w, h) 或 None(根节点)。
    """
    if "m_SizeDelta" not in r:      # 非 RectTransform, 不参与布局
        return None
    size = _v2(r["m_SizeDelta"])
    amin = _v2(r.get("m_AnchorMin", {}))
    amax = _v2(r.get("m_AnchorMax", {}))
    piv = _v2(r.get("m_Pivot", {}))
    apos = _v2(r.get("m_AnchoredPosition", {}))
    lsc = _v2(r.get("m_LocalScale", {"x": 1.0, "y": 1.0}))

    if parent is None:
        return (0.0, 0.0, size[0], size[1])

    pox, poy, pw, ph = parent
    # 锚点拉伸时尺寸 = 父尺寸*锚点差 + sizeDelta
    w = (amax[0] - amin[0]) * pw + size[0] * lsc[0]
    h = (amax[1] - amin[1]) * ph + size[1] * lsc[1]
    # 轴心点(先算 pivot 位置, 再反推左下角原点是参考实现的写法)
    ppx = (amax[0] - amin[0]) * pw * piv[0] + amin[0] * pw + pox + apos[0]
    ppy = (amax[1] - amin[1]) * ph * piv[1] + amin[1] * ph + poy + apos[1]
    return (ppx - piv[0] * w, ppy - piv[1] * h, w, h)


# ============================================================================
# 三、AssetBundle 读取
# ============================================================================
def _unitypy():
    import UnityPy
    # 少数包没有写入 Unity 版本号, 给个兜底版本
    try:
        UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0"
    except Exception:
        pass
    return UnityPy


class ABNotFound(Exception):
    pass


def _load_ab(path: str):
    UnityPy = _unitypy()
    with warnings.catch_warnings():          # 屏蔽 UnityVersionFallbackWarning 刷屏
        warnings.simplefilter("ignore")
        env = UnityPy.load(path)
    objs = {o.path_id: o for o in env.objects}
    return env, objs


def _tt(o):
    try:
        return o.read_typetree()
    except Exception:
        try:
            return o.read()
        except Exception:
            return None


class PaintingPrefab:
    """一个立绘配置包解析结果: 画布尺寸 + 图层列表 + 表情框。"""

    def __init__(self, name):
        self.name = name
        self.canvas = (0, 0)
        self.layers = []      # [{name, rect, raw, order}]
        self.face_rect = None # (ox, oy, w, h)
        # face 节点引用的 Sprite(默认表情)的 PathID; 0 = 老立绘, 脸直接画在图层里
        self.face_sprite_pid = 0
        # face 节点在层级里的位置(tuple(order)): 用来找它下面那层, 判断脸部是不是洞
        self.face_order = None

    def __repr__(self):
        return f"<PaintingPrefab {self.name} canvas={self.canvas} layers={[l['name'] for l in self.layers]}>"


def read_prefab(ab_path: str, name: str) -> PaintingPrefab:
    """解析 painting/<name> 配置包, 得到画布与各图层矩形。"""
    env, objs = _load_ab(ab_path)

    # 找到根 GameObject: 先同名(忽略大小写), 再退回容器(prefab)指向的对象
    root_pid = None
    fallback = []
    for pid, o in objs.items():
        if o.type.name != "GameObject":
            continue
        d = _tt(o)
        nm = d.get("m_Name") if isinstance(d, dict) else None
        if nm is None:
            continue
        if nm == name:
            root_pid = pid
            break
        if isinstance(nm, str) and nm.lower() == name.lower() and root_pid is None:
            root_pid = pid
        fallback.append((pid, nm))
    if root_pid is None:
        # 容器里登记的 prefab 根节点
        for key, val in getattr(env, "container", {}).items():
            if key.lower().endswith(".prefab"):
                pid = val.path_id if hasattr(val, "path_id") else (
                    val.get("m_PathID") if isinstance(val, dict) else None)
                if pid in objs:
                    root_pid = pid
                    break
    if root_pid is None:
        raise ABNotFound(f"{name}: 未找到同名 GameObject(不是立绘配置包)")

    def rects_of(pid):
        d = _tt(objs[pid]) or {}
        out = []
        for c in d.get("m_Component", []):
            cp = c["component"]["m_PathID"]
            co = objs.get(cp)
            if co is not None and co.type.name in ("RectTransform", "Transform"):
                r = _tt(co)
                if isinstance(r, dict):
                    out.append(r)
        return out

    res = PaintingPrefab(name)
    rootrect = rects_of(root_pid)[0]
    res.canvas = (int(round(rootrect["m_SizeDelta"]["x"])),
                  int(round(rootrect["m_SizeDelta"]["y"])))
    root_rect = (0.0, 0.0,
                 float(rootrect["m_SizeDelta"]["x"]),
                 float(rootrect["m_SizeDelta"]["y"]))

    order = [0]

    def walk(pid, parent_rect, depth):
        d = _tt(objs[pid]) or {}
        nm = d.get("m_Name")
        if nm in UI_SUBTREE_BAN or LAYER_NAME_BAN.match(str(nm or "")):
            return                    # 整个 UI 子树跳过
        rects = rects_of(pid)
        rect = rect_of(rects[0], parent_rect) if rects else None
        eff = rect if rect is not None else parent_rect

        # 取该节点上的 Image 组件(有 m_Sprite 字段且不是 RectTransform)
        for c in d.get("m_Component", []):
            cp = c["component"]["m_PathID"]
            co = objs.get(cp)
            if co is None or co.type.name != "MonoBehaviour":
                continue
            m = _tt(co)
            if not isinstance(m, dict) or "m_Sprite" not in m or "m_AnchoredPosition" in m:
                continue
            sp = m.get("m_Sprite") or {}
            sp_pid = int(sp.get("m_PathID", 0) or 0)
            if nm == "face":
                # 表情框: 绝大多数立绘这里 Sprite 为空(脸已经画在 rw 图层里);
                # 但新立绘会引用 paintingface/<立绘名> 里的默认表情 —— 此时 rw 图层
                # 在脸部是**挖空**的, 必须把这张默认表情贴回去, 否则成品脸上是个
                # 大白框(其实是透出背景层)。见 CHANGELOG v1.1。
                if res.face_rect is None and rect is not None:
                    res.face_rect = rect
                if sp_pid and not res.face_sprite_pid:
                    res.face_sprite_pid = sp_pid
                if res.face_order is None:
                    res.face_order = tuple(order)
                continue
            if LAYER_NAME_BAN.match(str(nm or "")):
                continue
            if sp_pid == 0:                     # 空 Sprite
                continue
            raw = m.get("mRawSpriteSize") or {}
            rec = {
                "name": nm,
                "rect": rect,
                "raw": (float(raw.get("x") or 0), float(raw.get("y") or 0)),
                "order": tuple(order),
                "is_face": (nm == "face"),
                "enabled": int(m.get("m_Enabled", 1)) == 1,
                # 该图层引用的 Sprite 的 PathID: 用于在"图层名对不上包名"时
                # (典型: _hx / _asmr 变体的主图层, 贴图仍复用基础立绘的 _tex 包)
                # 校验候选包里的 Sprite 是否真的是同一个资源。
                "sprite_pid": sp_pid,
            }
            res.layers.append(rec)

        if rects:
            for i, ch in enumerate(rects[0].get("m_Children") or []):
                cpid = ch["m_PathID"]
                cgo = (_tt(objs[cpid]) or {}).get("m_GameObject", {}).get("m_PathID")
                if cgo in objs:
                    order.append(i)
                    walk(cgo, eff, depth + 1)
                    order.pop()

    # 按层级顺序遍历, 保证 z 序正确
    walk(root_pid, None, 0)
    return res


_AB_CACHE = {}

# 图层名对不上包名时要尝试剥掉的"变体后缀"
_VARIANT_SUF = ("_dark_shadow", "_shophx", "_alter", "_asmr", "_hx", "_ex", "_wjz",
                "_doa", "_front", "_bj", "_n", "_h", "_g")


def _cand_bases(layer_name, prefab_name):
    """图层/立绘名对不上 _tex 包名时, 尝试去掉变体后缀后的基础名(按顺序)。"""
    out = []
    for nm in (layer_name, prefab_name):
        if not nm:
            continue
        low = str(nm).lower()
        for suf in _VARIANT_SUF:
            if low.endswith(suf):
                b = str(nm)[: -len(suf)]
                if b and b not in out:
                    out.append(b)
        # 名字中间带变体后缀 (如 aerbien_rw_hx -> aerbien_rw)
        for suf in _VARIANT_SUF:
            if suf in low and not low.endswith(suf):
                idx = low.find(suf)
                b = str(nm)[:idx] + str(nm)[idx + len(suf):]
                if b and b not in out:
                    out.append(b)
    return out


def _tex_paths(painting_dir, base):
    """按 base 找候选 _tex 包(精确优先)。"""
    p = os.path.join(painting_dir, base + "_tex")
    if os.path.isfile(p):
        return [p]
    res = []
    low = base.lower()
    try:
        for f in sorted(os.listdir(painting_dir)):
            fl = f.lower()
            if fl.endswith("_tex") and fl.startswith(low + "_"):
                res.append(os.path.join(painting_dir, f))
    except OSError:
        pass
    return res


def _has_sprite(ab_path, path_id):
    """该 _tex 包里是否含指定 PathID 的 Sprite(用于确认"就是同一个素材")。"""
    try:
        env, objs = _load_ab(ab_path)
    except Exception:
        return False
    for o in objs.values():
        if o.type.name == "Sprite" and o.path_id == path_id:
            return True
    return False


def _typo_tex_path(painting_dir, layer_name, sprite_pid):
    """图层名拼错时的兜底: 找"字母相同、顺序不同"的 <名字>_tex, 再用 Sprite PathID 校验。

    游戏数据里偶有这种拼写错位: 图层名 `leiyibeier_2_rw` 对应的包其实叫
    `leiyierbei_2_rw_tex`。要求字符集合与长度完全一致(等价于"字母顺序打乱"),
    并且包里确实含该图层的 Sprite, 才认 —— 不通过就不猜。
    """
    want = "".join(sorted(layer_name.lower()))
    try:
        for f in sorted(os.listdir(painting_dir)):
            fl = f.lower()
            if not fl.endswith("_tex"):
                continue
            stem = fl[:-4]
            if len(stem) != len(want) or "".join(sorted(stem)) != want:
                continue
            p = os.path.join(painting_dir, f)
            if _has_sprite(p, sprite_pid):
                return p
    except OSError:
        pass
    return None


def load_layer_assets(painting_dir: str, layer_name: str, prefab_name: str,
                      sprite_pid: int | None = None):
    """在 painting 目录里找到某图层的 _tex 包, 返回 (Texture2D->PIL, obj文本, sprite矩形)。

    只认"名字对得上"的包:
      <layer>_tex  ->  目录中以 <layer>_ 开头的 *_tex
    根图层(layer 名 == 立绘名)才允许退回 <立绘名>_tex。
    ★ 关键: 不能给任意图层兜底用背景包, 否则会把背景贴进 UI 节点
      (曾导致 2b_2 的 shop_hx / Touch 标签位置被糊上一块背景图)。

    再补一层: 有些变体的主图层(如 `aerbien_hx` / `bangkeshan_2_hx` / `aimudeng_5_asmr`)
    的贴图并不在 <图层名>_tex 里, 而是**复用基础立绘的包**(配置包里 Sprite 的 PathID
    与 `aerbien_tex` / `bangkeshan_2_tex` 里的完全一致)。这种情况按 sprite_pid 校验后采用,
    校验不通过就不猜(返回 None), 避免贴错图。
    """
    cands = [layer_name]
    if prefab_name and prefab_name.lower() == layer_name.lower():
        cands.append(prefab_name)
    ab = None
    for c in cands:
        p = os.path.join(painting_dir, c + "_tex")
        if os.path.isfile(p):
            ab = p
            break
    if ab is None:
        # 模糊匹配: 目录里以 <layer>_ 开头或以 <layer>_tex 结尾的
        low = layer_name.lower()
        try:
            for f in sorted(os.listdir(painting_dir)):
                fl = f.lower()
                if fl == low + "_tex" or (fl.endswith("_tex") and fl.startswith(low + "_")):
                    ab = os.path.join(painting_dir, f)
                    break
        except OSError:
            pass
    if ab is None and sprite_pid:
        # 变体复用基础立绘的贴图包 -> 必须用 Sprite PathID 确认是同一个资源
        for base in _cand_bases(layer_name, prefab_name):
            for p in _tex_paths(painting_dir, base):
                if _has_sprite(p, int(sprite_pid)):
                    ab = p
                    break
            if ab:
                break
    if ab is None and sprite_pid:
        # 图层名拼写错位(leiyibeier_2_rw ↔ leiyierbei_2_rw_tex)
        ab = _typo_tex_path(painting_dir, layer_name, int(sprite_pid))
    if ab is None:
        return None, None, None

    if ab in _AB_CACHE:
        return _AB_CACHE[ab]

    tex_img, obj_text, srect = None, None, None
    try:
        env, objs = _load_ab(ab)
        for o in objs.values():
            if o.type.name == "Texture2D":
                d = o.read()
                if tex_img is None or getattr(d, "m_Name", None) == layer_name:
                    try:
                        tex_img = d.image
                    except Exception:
                        pass
            elif o.type.name == "Mesh" and obj_text is None:
                try:
                    obj_text = o.read().export()
                except Exception:
                    obj_text = None
            elif o.type.name == "Sprite" and srect is None:
                try:
                    d = o.read()
                    r = d.m_Rect
                    if getattr(d, "m_Name", None) == layer_name or True:
                        srect = (float(r.x), float(r.y), float(r.width), float(r.height))
                except Exception:
                    srect = None
    except Exception as e:
        log(f"    [警告] 读取 {os.path.basename(ab)} 失败: {e}")
    _AB_CACHE[ab] = (tex_img, obj_text, srect)
    return tex_img, obj_text, srect


def sprite_crop(tex: Image.Image, srect):
    """按 Sprite 的 m_Rect(Unity 左下原点) 裁剪整图。"""
    if not srect:
        return tex
    x, y, w, h = srect
    W, H = tex.size
    if w <= 0 or h <= 0 or (w == W and h == H):
        return tex
    box = (int(round(x)), int(round(H - y - h)), int(round(x + w)), int(round(H - y)))
    box = (max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3]))
    if box[2] <= box[0] or box[3] <= box[1]:
        return tex
    return tex.crop(box)


# ============================================================================
# 四、合成
# ============================================================================
def layer_bitmap(pf: PaintingPrefab, painting_dir: str, L: dict, export_layer=None):
    """还原单个图层, 返回 (已缩放的图层图, 画布左上角 x, y); 取不到素材返回 None。"""
    if L["rect"] is None:
        return None
    ox, oy, w, h = L["rect"]
    if w <= 0 or h <= 0:
        return None
    tex, obj, srect = load_layer_assets(painting_dir, L["name"], pf.name,
                                        L.get("sprite_pid"))
    if tex is None:
        return None

    # 1) 有 Mesh -> 拼图; 无 Mesh -> 贴图本身就是完整图(可能需按 Sprite 裁剪)
    meta = {}
    lay = restore_layer(tex, obj, meta) if obj else None
    rw, rh = L["raw"]
    if rw > 0 and rh > 0:
        sw, sh = w / rw, h / rh
    else:
        sw, sh = 1.0, 1.0

    if lay is None:                       # 无 Mesh: 贴图/裁剪结果已是「画布单位」的原稿
        lay = sprite_crop(tex, srect)
        ytop = float(h)                   # 直接按矩形高度贴(不要再乘 sh, 否则会被推飞出画布)
    else:
        # mesh 顶点是「图层原稿」的像素坐标, 原点(0,0) 就是该 Image 矩形的左下角,
        # 原点之上 ymax 处是图像顶边。所以必须用 ymax 定位, 而不是矩形高度:
        # 多数图层 ymax≈mRawSpriteSize.y, 与旧公式只差 1~4 像素(看不出来);
        # 少数图层(baifeng_rw 差 447px, baifeng_bj 差 684px)会整段错位。
        ytop = float(meta.get("ymax", h)) * sh

    if export_layer is not None:
        export_layer(L["name"], lay)

    # 2) 缩放到 RectTransform 给出的矩形尺寸
    dw, dh = int(round(lay.width * sw)), int(round(lay.height * sh))
    if (dw, dh) != lay.size and dw > 0 and dh > 0:
        lay = lay.resize((dw, dh), Image.BILINEAR)

    # 3) 画布左上角坐标 (Unity y 向上 -> PIL y 向下)
    #    ytop = mesh 原点(即矩形左下角)到图像顶边的距离, 它可能大于 rect 高度
    #    (图层原稿比矩形大, 例如 baifeng_rw / baifeng_bj), 必须按它定位。
    x = int(round(ox))
    y = int(round(pf.canvas[1] - oy - ytop)) + Y_BIAS
    return lay, x, y


def _probe_hole(pf: PaintingPrefab, probe: dict, L: dict, lay, x, y):
    """合成过程中顺手判断"这个图层在 face 矩形处是不是挖了洞"(不额外拼图)。

    判据: 该层在 face 矩形内几乎全透明, 而紧贴矩形外 8px 的一圈明显有内容。
    """
    if probe.get("hole") or pf.face_rect is None or pf.face_order is None:
        return
    if L["order"] >= pf.face_order:        # 只看 face 节点下面的图层
        return
    fx, fy, fw, fh = pf.face_rect
    fw, fh = int(round(fw)), int(round(fh))
    if fw < 8 or fh < 8:
        return
    pad = 8
    a = np.asarray(lay.getchannel("A"), dtype=np.float32)
    if float(a.mean()) < 40:               # 该层本身几乎没内容, 判不了
        return
    u0, v0 = int(round(fx)) - x, int(round(pf.canvas[1] - fy - fh)) - y
    inner = a[max(0, v0):max(0, v0 + fh), max(0, u0):max(0, u0 + fw)]
    outer = a[max(0, v0 - pad):max(0, v0 + fh + pad),
              max(0, u0 - pad):max(0, u0 + fw + pad)]
    if inner.size < 100 or outer.size <= inner.size:
        return
    in_m = float(inner.mean())
    ring = (float(outer.sum()) - float(inner.sum())) / (outer.size - inner.size)
    if in_m < 40.0 and ring > max(30.0, 3 * in_m):
        probe["hole"] = True
        probe["hole_layer"] = L["name"]


def composite(pf: PaintingPrefab, painting_dir: str, face_img: Image.Image | None = None,
              face_offset=(0, 0), probe: dict | None = None, stage=None, export_layer=None):
    """把某立绘的所有图层合成为一张完整立绘。

    face_offset: 表情贴图相对 face 节点的额外偏移(由 align_face 得出)
    probe      : 传入 dict 则顺手检测"脸部是不是挖空的", 结果写入 probe["hole"]
                 (见 face_is_hole; 复用这一步就不用为了判定再合成一遍)
    """
    C = pf.canvas
    canvas = Image.new("RGBA", C, (255, 255, 255, 0))

    missing = []
    for L in pf.layers:
        if stage is not None:
            stage("合成图层 · " + L["name"])
        if L["name"] == "face":
            continue                     # 表情单独处理
        r = layer_bitmap(pf, painting_dir, L, export_layer)
        if r is None:
            if L["rect"] is not None and L["rect"][2] > 0 and L["rect"][3] > 0:
                missing.append(L["name"])
            continue
        lay, x, y = r
        if probe is not None:
            _probe_hole(pf, probe, L, lay, x, y)
        canvas.alpha_composite(lay, (x, y))

    if face_img is not None and pf.face_rect is not None:
        paste_face(canvas, pf.face_rect, face_img, face_offset)
    return canvas, missing


def paste_face(canvas, rect, face, offset):
    ox, oy, w, h = rect
    size = (int(round(w)), int(round(h)))
    if size != face.size and size[0] > 0:
        face = face.resize(size, Image.BILINEAR)
    x = int(round(ox)) + int(offset[0])
    y = int(round(canvas.height - oy - h)) + Y_BIAS + int(offset[1])
    canvas.alpha_composite(face, (x, y))


def face_is_hole(pf: PaintingPrefab, painting_dir: str) -> bool:
    """脸部那块是不是挖空的洞(需要补默认表情)。

    新立绘把脸拆成独立 Sprite, 人物层在脸部**挖了个矩形洞** —— 不补就会露出背景层,
    看起来像脸上盖了个大白框(其实透出来的是背景)。老立绘脸直接画在人物层里
    (prefab 的 face 节点引用的 Sprite 另有用处), 这时不能补, 否则会重影。

    这里为了独立判定会完整合成一遍; 主流程请用 composite(..., probe={}) 顺带判定。
    """
    probe = {}
    composite(pf, painting_dir, None, probe=probe)
    return bool(probe.get("hole"))


# ============================================================================
# 五、表情差分
# ============================================================================
# 表情自动对齐参数
FA_X = 220          # 横向搜索半径
FA_Y = 340          # 纵向搜索半径
FA_GAIN = 0.15      # 最佳位置 NCC 至少要比原位置高这么多才采用(避免误判)
FA_MIN_NCC = 0.60   # 最佳位置 NCC 下限


def _masked_ncc(img, tpl, mask, x, y):
    """带掩膜的零均值 NCC(单点)。"""
    h, w = tpl.shape[:2]
    if x < 0 or y < 0 or x + w > img.shape[1] or y + h > img.shape[0]:
        return -9.0
    m = mask.astype(bool)
    if m.sum() < 300:
        return -9.0
    sub = img[y:y + h, x:x + w]
    a = sub[m].astype(np.float32)
    b = tpl[m].astype(np.float32)
    a -= a.mean(); b -= b.mean()
    d = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / d) if d > 0 else -9.0


def align_face(patch: Image.Image, base: Image.Image, face_rect):
    """自动把表情贴图对齐到立绘里那张脸的位置。

    绝大多数立绘直接按 Prefab 的 face 节点矩形贴就是准的(此时返回 (0,0));
    但有少数立绘(prefab 里那个 face 矩形和实际头部对不上, 例如 yichui_7)
    会整体偏移。这里拿贴图里"去掉五官后的头发/角"当模板,
    在基础立绘上做匹配, 只在证据很强时才纠正, 避免误判。

    返回 (dx, dy), 即相对 face 矩形的偏移。
    """
    if not _HAS_CV2 or face_rect is None:
        return (0, 0)
    C = base.size
    PA = np.array(patch.convert("RGBA"))
    H, W = PA.shape[:2]
    mask = (PA[..., 3] > 200).astype(np.uint8)
    # 抹掉五官(眼/口)区域, 只留头发、角等各表情一致的部分
    mask[int(H * 0.55):int(H * 0.98), int(W * 0.30):int(W * 0.75)] = 0
    if int(mask.sum()) < 3000:
        return (0, 0)

    fx, fy, fw, fh = face_rect
    hx, hy = int(round(fx)), int(round(C[1] - fy - fh))
    B = np.array(Image.alpha_composite(
        Image.new("RGBA", C, (255, 255, 255, 255)), base).convert("RGB"))
    tpl = PA[..., :3][..., ::-1].copy()          # PIL RGB -> cv2 BGR

    x0, y0 = max(0, hx - FA_X), max(0, hy - FA_Y)
    x1, y1 = min(C[0], hx + FA_X + W), min(C[1], hy + FA_Y + H)
    if x1 - x0 < W or y1 - y0 < H:
        return (0, 0)
    try:
        res = np.nan_to_num(cv2.matchTemplate(B[y0:y1, x0:x1], tpl,
                                              cv2.TM_CCORR_NORMED, mask=mask))
        dil = cv2.dilate(res, np.ones((15, 15), np.uint8))
        pk = np.where((res == dil) & (res > 0.4))
        vals = res[pk]
        order = np.argsort(vals)[::-1][:12]
        cands = [(int(pk[1][i]), int(pk[0][i])) for i in order]
    except Exception:
        return (0, 0)

    n_hint = _masked_ncc(B, tpl, mask, hx, hy)
    best = (n_hint, hx, hy)
    for cx, cy in cands:
        s = _masked_ncc(B, tpl, mask, x0 + cx, y0 + cy)
        if s > best[0]:
            best = (s, x0 + cx, y0 + cy)
    if best[0] - n_hint > FA_GAIN and best[0] >= FA_MIN_NCC:
        return (best[1] - hx, best[2] - hy)
    return (0, 0)


def load_face_pack(face_dir: str, name: str):
    """读取 paintingface/<name>, 返回 (贴图表, {Sprite PathID: PIL.Image})。

    贴图表: {贴图名: Image}, 按贴图名排序(0,1,2...)。
    Sprite 表: 用于按 prefab 里 face 节点引用的 Sprite 找回"默认表情"。
    """
    ab = os.path.join(face_dir, name)
    out, pid2img = {}, {}
    if not os.path.isfile(ab):
        return out, pid2img
    try:
        env, objs = _load_ab(ab)
        texs = {}
        for o in objs.values():
            if o.type.name != "Texture2D":
                continue
            try:
                d = o.read()
                img = d.image
            except Exception:
                continue
            nm = str(getattr(d, "m_Name", "") or "").strip()
            if img is not None and nm:
                out[nm] = img
                texs[o.path_id] = img
        # Sprite -> 它引用的贴图
        for o in objs.values():
            if o.type.name != "Sprite":
                continue
            try:
                d = o.read()
                t = getattr(getattr(d, "m_RD", None), "texture", None)
                tp = getattr(t, "path_id", None)
                if tp is None and t is not None:          # 兼容旧版 UnityPy 的写法
                    tp = t.get("m_PathID") if isinstance(t, dict) else getattr(t, "m_PathID", None)
                if tp is not None and tp in texs:
                    pid2img[o.path_id] = texs[tp]
            except Exception:
                continue
    except Exception as e:
        log(f"    [警告] 读取表情包失败: {e}")
        return {}, {}

    def key(k):
        return (0, int(k), "") if k.isdigit() else (1, 0, k)

    return {k: out[k] for k in sorted(out, key=key)}, pid2img


def load_faces(face_dir: str, name: str):
    """读取 paintingface/<name> 里的表情贴图, 返回 {编号: PIL.Image} (按贴图名排序)。"""
    return load_face_pack(face_dir, name)[0]


def pick_default_face(pf: "PaintingPrefab", faces: dict, pid2img: dict,
                      face_dir: str | None = None, name: str | None = None):
    """挑出"基础立绘"该补的默认表情(只在确认脸部是洞之后调用)。

    优先用 prefab 里 face 节点引用的那张 Sprite(按 PathID 精确匹配);
    少数配置包没写这个引用(PathID=0)但图层照样挖了洞(如 haerxibaoweier_3),
    这时按命名约定取 0 号贴图 —— 和表情差分第 1 张是同一张。

    变体自己没有 paintingface 包时(如 jian_3_hx / zhaohe_4_hx), 按 PathID
    向基础立绘的表情包借用。
    """
    pid = int(getattr(pf, "face_sprite_pid", 0) or 0)
    if pid:
        img = pid2img.get(pid)
        if img is not None:
            return img
        if face_dir and name:
            for base in _cand_bases(name, name):
                p = os.path.join(face_dir, base)
                if os.path.isfile(p):
                    _, m2 = load_face_pack(face_dir, base)
                    if pid in m2:
                        log(f"    默认表情借用 {base} 的表情包")
                        return m2[pid]
    if "0" in faces:
        return faces["0"]             # 兜底: 命名约定 0 号就是默认表情
    if faces:
        # 少数老资源的表情从 1 开始编号(没有 0 号), 用第一张充当默认表情
        return next(iter(faces.values()))
    return None


# ============================================================================
# 六、解包文件模式(只有 Texture2D + Mesh, 无 AB)
# ============================================================================
def restore_unpacked(tex_dir: str, mesh_dir: str, out_dir: str, only=None):
    """只按 Texture2D + Mesh 还原每个图层(等价于 AzurLanePaintingExtract 的基本功能)。"""
    os.makedirs(out_dir, exist_ok=True)
    if not os.path.isdir(mesh_dir) or not os.path.isdir(tex_dir):
        log("  [错误] 目录不存在")
        return []
    meshes = [f for f in os.listdir(mesh_dir) if f.lower().endswith(".obj")]
    done = []
    for i, mf in enumerate(sorted(meshes), 1):
        stem = os.path.splitext(mf)[0]
        if stem.endswith("-mesh"):
            stem = stem[:-5]
        if only and stem != only:
            continue
        texp = None
        for cand in (stem + ".png", stem + ".tga"):
            p = os.path.join(tex_dir, cand)
            if os.path.isfile(p):
                texp = p
                break
        if texp is None:
            continue
        try:
            tex = Image.open(texp).convert("RGBA")
            obj = open(os.path.join(mesh_dir, mf), "r", encoding="utf-8", errors="ignore").read()
            img = restore_layer(tex, obj)
            if img is None:
                continue
            img.save(os.path.join(out_dir, stem + ".png"))
            done.append(stem)
            log(f"  [{i}/{len(meshes)}] {stem}  -> {img.size}")
        except Exception as e:
            log(f"  [{i}/{len(meshes)}] {stem} 失败: {e}")
    return done


# ============================================================================
# 七、主流程
# ============================================================================
def find_paintings(painting_dir: str):
    """列出所有"主立绘"配置包名。

    规则: 若 X 去掉一个结构后缀(_n/_rw/_front/_bjx/_dec/_group) 后得到的名字也是
    一个配置包, 说明 X 只是某立绘的一层/变体, 跳过。
    _hx(国服和谐) 本身是独立立绘, 保留; 但 X_n_hx 这种"和谐版的 n 变体"跳过。
    """
    try:
        entries = os.listdir(painting_dir)
    except OSError:
        return []
    files = set(entries)
    names = []
    for f in entries:
        if f.endswith("_tex") or not os.path.isfile(os.path.join(painting_dir, f)):
            continue
        # 结构性变体?
        skip = False
        if SUB_SUFFIX.search(f):
            if SUB_SUFFIX.sub("", f) in files:
                skip = True
        if not skip:
            for hx in ("_shophx", "_hx"):
                if f.endswith(hx):
                    base = f[: -len(hx)]
                    if SUB_SUFFIX.search(base) and SUB_SUFFIX.sub("", base) in files:
                        skip = True
                        break
        if not skip:
            names.append(f)
    return sorted(names)


def is_prefab(ab_path: str) -> bool:
    try:
        env, objs = _load_ab(ab_path)
    except Exception:
        return False
    n_img = 0
    for o in objs.values():
        if o.type.name != "GameObject":
            continue
        d = _tt(o) or {}
        for c in d.get("m_Component", []):
            cp = c["component"]["m_PathID"]
            co = objs.get(cp)
            if co is not None and co.type.name == "Mesh":
                n_img += 1
    return True


# ---------------------------------------------------------------------------
# 素材完整性检查
# ---------------------------------------------------------------------------
UI_LIKE = re.compile(r"^(shop_hx|shadow)", re.I)
NUM_LIKE = re.compile(r"^\d+$")


def prefab_layers(ab_path):
    """读配置包, 返回 {图层名: sprite PathID}(不含 face / UI 子树 / 空 Sprite)。"""
    out = {}
    env, objs = _load_ab(ab_path)
    for o in objs.values():
        if o.type.name != "GameObject":
            continue
        d = _tt(o)
        if not isinstance(d, dict):
            continue
        nm = d.get("m_Name")
        for c in d.get("m_Component", []):
            cp = c["component"]["m_PathID"]
            co = objs.get(cp)
            if co is None or co.type.name != "MonoBehaviour":
                continue
            m = _tt(co)
            if not isinstance(m, dict) or "m_Sprite" not in m or "m_AnchoredPosition" in m:
                continue
            sp = m.get("m_Sprite") or {}
            if nm == "face" or sp.get("m_PathID", 0) == 0:
                continue
            out[nm] = int(sp.get("m_PathID", 0) or 0)
    return out


def expression_faces(faces, default):
    """先确定需要绘制的源表情；默认脸已用于主图，重复贴图只取首次出现。"""
    def source_key(face):
        rgba = face.convert("RGBA")
        return rgba.size, rgba.tobytes()

    used = {source_key(default)} if default is not None else set()
    for face in faces:
        key = source_key(face)
        if key not in used:
            used.add(key)
            yield face


