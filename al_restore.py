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

依赖: pillow, numpy, UnityPy
用法: 见 启动工具.bat / README.md
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import time
import traceback
import warnings

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
VERSION = "1.0.0"
APP_TITLE = "碧蓝航线 · 静态立绘还原工具"

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
            if nm == "face":
                # 表情框: 基础包里 Sprite 通常为空, 但矩形有效, 需要记录
                if res.face_rect is None and rect is not None:
                    res.face_rect = rect
                continue
            if LAYER_NAME_BAN.match(str(nm or "")):
                continue
            if sp.get("m_PathID", 0) == 0:      # 空 Sprite
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
                "sprite_pid": int(sp.get("m_PathID", 0) or 0),
            }
            if nm == "face":
                res.face_rect = rect
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
def composite(pf: PaintingPrefab, painting_dir: str, face_img: Image.Image | None = None,
              face_offset=(0, 0)):
    """把某立绘的所有图层合成为一张完整立绘。

    face_offset: 表情贴图相对 face 节点的额外偏移(由 align_face 得出)
    """
    C = pf.canvas
    canvas = Image.new("RGBA", C, (255, 255, 255, 0))

    missing = []
    for L in pf.layers:
        if L["name"] == "face":
            continue                     # 表情单独处理
        if L["rect"] is None:
            continue
        ox, oy, w, h = L["rect"]
        if w <= 0 or h <= 0:
            continue
        tex, obj, srect = load_layer_assets(painting_dir, L["name"], pf.name,
                                            L.get("sprite_pid"))
        if tex is None:
            missing.append(L["name"])
            continue

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

        # 2) 缩放到 RectTransform 给出的矩形尺寸
        dw, dh = int(round(lay.width * sw)), int(round(lay.height * sh))
        if (dw, dh) != lay.size and dw > 0 and dh > 0:
            lay = lay.resize((dw, dh), Image.BILINEAR)

        # 3) 贴到画布 (Unity y 向上 -> PIL y 向下)
        #    ytop = mesh 原点(即矩形左下角)到图像顶边的距离, 它可能大于 rect 高度
        #    (图层原稿比矩形大, 例如 baifeng_rw / baifeng_bj), 必须按它定位。
        x = int(round(ox))
        y = int(round(C[1] - oy - ytop)) + Y_BIAS
        canvas.alpha_composite(lay, (x, y))

    # 表情
    if face_img is not None and pf.face_rect is not None:
        ox, oy, w, h = pf.face_rect
        f = face_img
        if (int(round(w)), int(round(h))) != f.size and int(round(w)) > 0:
            f = f.resize((int(round(w)), int(round(h))), Image.BILINEAR)
        x = int(round(ox)) + int(face_offset[0])
        y = int(round(C[1] - oy - h)) + Y_BIAS + int(face_offset[1])
        canvas.alpha_composite(f, (x, y))

    return canvas, missing


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


def load_faces(face_dir: str, name: str):
    """读取 paintingface/<name> 里的表情贴图, 返回 {编号: PIL.Image} (按贴图名排序)。"""
    ab = os.path.join(face_dir, name)
    if not os.path.isfile(ab):
        return {}
    out = {}
    try:
        env, objs = _load_ab(ab)
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
    except Exception as e:
        log(f"    [警告] 读取表情包失败: {e}")
        return {}

    def key(k):
        return (0, int(k), "") if k.isdigit() else (1, 0, k)

    return {k: out[k] for k in sorted(out, key=key)}


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


def check_materials_report(bundles_dir, out_txt=None, only=None, jobs=None,
                           progress=None, should_stop=None):
    """检查素材完整性: 每个立绘的各图层贴图包是否都在。

    返回 dict(real=[(立绘, [缺的层...])], uionly=[...], noface=[...], fail=[...],
              total=总数, checked=已检查数)
    """
    P = os.path.join(bundles_dir, "painting")
    F = os.path.join(bundles_dir, "paintingface")
    names = find_paintings(P) if os.path.isdir(P) else []
    if only:
        want = {str(s).strip().lower() for s in only}
        names = [n for n in names if n.lower() in want]
    if jobs:
        names = names[: jobs]

    real, uionly, noface, fail = [], [], [], []
    for i, name in enumerate(names, 1):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(names), name)
        try:
            layers = prefab_layers(os.path.join(P, name))
            miss = [L for L in sorted(layers)
                    if load_layer_assets(P, L, name, layers[L])[0] is None]
            if miss:
                core = [L for L in miss if not UI_LIKE.match(L) and not NUM_LIKE.match(L)]
                (real if core else uionly).append((name, core or miss))
            if not os.path.isfile(os.path.join(F, name)):
                noface.append(name)
        except Exception as e:
            fail.append((name, str(e)))

    log("=" * 62)
    log(f"素材完整性检查: 共 {len(names)} 个立绘")
    log(f"  ① 只缺 UI 节点(不影响成品): {len(uionly)} 个")
    log(f"  ② 真缺主图层素材(成品会缺这一层): {len(real)} 个")
    for n, ls in real:
        log(f"        {n:<26s} 缺: {', '.join(ls)}")
    log(f"  ③ 没有表情包(正常, 只是没表情差分): {len(noface)} 个")
    log(f"  ④ 配置包读取失败: {len(fail)} 个")
    for n, e in fail[:10]:
        log(f"        {n:<26s} {e}")

    if out_txt:
        try:
            d = os.path.dirname(os.path.abspath(out_txt))
            if d:
                os.makedirs(d, exist_ok=True)
            with open(out_txt, "w", encoding="utf-8") as f:
                f.write("# 真缺主图层素材的立绘(%d 个)\n" % len(real))
                for n, ls in real:
                    f.write("%s\t%s\n" % (n, ", ".join(ls)))
                f.write("\n# 没有表情包的立绘(%d 个)\n" % len(noface))
                f.write(", ".join(noface) + "\n")
            log(f"  清单已写入: {out_txt}")
        except OSError as e:
            log(f"  [警告] 写清单失败: {e}")

    return dict(real=real, uionly=uionly, noface=noface, fail=fail,
                total=len(names), checked=min(len(names), i if names else 0))


def run(bundles_dir, out_dir, only=None, want_layer=False, want_face=True,
        jobs=None, progress=None, should_stop=None, face_align=True):
    """bundle 模式主流程。返回成功处理的立绘名列表。

    only       : 只处理这些立绘名(不区分大小写); None = 全部
    progress   : 回调 (已完成序号, 总数, 当前立绘名)
    should_stop: 回调, 返回 True 则中止
    face_align : 表情贴图自动对齐(纠正少数立绘表情框偏移的问题)
    """
    painting_dir = os.path.join(bundles_dir, "painting")
    face_dir = os.path.join(bundles_dir, "paintingface")
    if not os.path.isdir(painting_dir):
        log(f"[错误] 找不到 painting 目录: {painting_dir}")
        return []

    names = find_paintings(painting_dir)
    if only:
        want = {s.strip().lower() for s in only if str(s).strip()}
        names = [n for n in names if n.lower() in want]
        if not names:
            log("[提示] 指定的立绘一个都没匹配到, 请检查名字是否写对")
    log(f"待处理立绘: {len(names)} 个")

    ok = []
    t0 = time.time()
    for idx, name in enumerate(names, 1):
        if jobs and idx > jobs:
            break
        if should_stop and should_stop():
            log(">>> 已手动停止")
            break
        if progress:
            progress(idx, len(names), name)
        ab = os.path.join(painting_dir, name)
        try:
            pf = read_prefab(ab, name)
        except Exception as e:
            continue
        if not pf.layers:
            continue
        log(f"[{idx}/{len(names)}] {name}  画布={pf.canvas}  图层={len(pf.layers)}")

        ship_dir = os.path.join(out_dir, name)
        os.makedirs(ship_dir, exist_ok=True)

        # 分层输出
        if want_layer:
            for L in pf.layers:
                if L["name"] == "face" or L["rect"] is None:
                    continue
                tex, obj, srect = load_layer_assets(painting_dir, L["name"], name,
                                                    L.get("sprite_pid"))
                if tex is None:
                    continue
                try:
                    img = restore_layer(tex, obj) if obj else None
                    if img is None:
                        img = sprite_crop(tex, srect)
                except Exception:
                    img = None
                if img:
                    img.save(os.path.join(ship_dir, f"{name}__layer__{L['name']}.png"))

        # 基础立绘(无表情)
        base, missing = composite(pf, painting_dir, None)
        if missing:
            log(f"    缺少图层资源: {', '.join(missing)}")
        if pf.face_rect is None:
            base.save(os.path.join(ship_dir, f"{name}.png"))
            log(f"    -> {name}.png {base.size} (无表情框)")
            ok.append(name)
            continue

        base.save(os.path.join(ship_dir, f"{name}.png"))

        # 表情差分
        faces = load_faces(face_dir, name) if want_face else {}
        if not faces:
            base.save(os.path.join(ship_dir, f"{name}_exp.png"))
            log(f"    -> {name}.png / {name}_exp.png {base.size} (无表情资源)")
            ok.append(name)
            continue

        # 表情自动对齐: 每个立绘只算一次, 所有表情共用
        foff = (0, 0)
        if face_align:
            try:
                foff = align_face(next(iter(faces.values())), base, pf.face_rect)
            except Exception:
                foff = (0, 0)
            if foff != (0, 0):
                log(f"    表情自动对齐: 在原位置基础上偏移 {foff}")

        for i, (fname, fimg) in enumerate(faces.items(), 1):
            img, _ = composite(pf, painting_dir, fimg, foff)
            img.save(os.path.join(ship_dir, f"{name}_exp_{i}.png"))
        # 兼容 ALPA 的导入命名
        img0, _ = composite(pf, painting_dir, next(iter(faces.values())), foff)
        img0.save(os.path.join(ship_dir, f"{name}_exp.png"))
        log(f"    -> {name}.png + {len(faces)} 张表情 {base.size}")
        ok.append(name)

    log(f"完成, 耗时 {time.time()-t0:.1f}s, 成功 {len(ok)} 个")
    return ok


# ============================================================================
# 八、GUI
# ============================================================================
def gui():
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title(f"{APP_TITLE}  v{VERSION}")
    root.geometry("960x790")
    root.minsize(880, 700)
    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=3)      # 立绘列表区(可拉伸, 优先吃掉多余空间)
    root.rowconfigure(5, weight=2)      # 日志区(可拉伸)
    # 注意: 第 4 行是「开始还原」那排按钮, 不能给权重, 否则窗口一矮就被压没了

    v_bundles = tk.StringVar()
    v_out = tk.StringVar()
    v_search = tk.StringVar()
    v_scope = tk.StringVar(value="all")       # all | selected
    v_layer = tk.BooleanVar(value=False)
    v_face = tk.BooleanVar(value=True)
    v_align = tk.BooleanVar(value=True)
    v_count = tk.StringVar(value="尚未扫描")
    v_prog = tk.StringVar(value="就绪")

    state = {"all": [], "shown": [], "sel": set(), "stop": False}

    # ---------------------------------------------------------------- 1. 目录
    f1 = ttk.LabelFrame(root, text=" 1. 目录 ", padding=10)
    f1.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
    f1.columnconfigure(1, weight=1)

    ttk.Label(f1, text="AssetBundles 根目录:").grid(row=0, column=0, sticky="w")
    ttk.Entry(f1, textvariable=v_bundles).grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Button(f1, text="浏览…", command=lambda: _pick_bundles()).grid(row=0, column=2)
    ttk.Label(f1, text="输出目录:").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(f1, textvariable=v_out).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))
    ttk.Button(f1, text="浏览…", command=lambda: _pick_out()).grid(row=1, column=2, pady=(6, 0))
    ttk.Label(f1, text="(根目录为游戏 AssetBundles，需含 painting 子目录)", foreground="#888").grid(
        row=2, column=1, sticky="w", padx=6, pady=(4, 0))

    # ------------------------------------------------------------ 2. 选立绘
    f2 = ttk.LabelFrame(root, text=" 2. 选择要还原的立绘 ", padding=10)
    f2.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
    f2.columnconfigure(0, weight=1)
    f2.rowconfigure(2, weight=1)

    bar = ttk.Frame(f2)
    bar.grid(row=0, column=0, sticky="ew")
    bar.columnconfigure(1, weight=1)
    ttk.Label(bar, text="搜索:").grid(row=0, column=0, sticky="w")
    se = ttk.Entry(bar, textvariable=v_search)
    se.grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Button(bar, text="扫描 / 刷新", command=lambda: _scan()).grid(row=0, column=2)
    ttk.Label(bar, textvariable=v_count, foreground="#0a6").grid(row=0, column=3, padx=(10, 0))

    btns = ttk.Frame(f2)
    btns.grid(row=1, column=0, sticky="ew", pady=(6, 4))
    ttk.Button(btns, text="全选(当前列表)", command=lambda: _sel_all()).pack(side="left")
    ttk.Button(btns, text="反选", command=lambda: _sel_invert()).pack(side="left", padx=6)
    ttk.Button(btns, text="清空选择", command=lambda: _sel_clear()).pack(side="left")
    ttk.Label(btns, text="  ← 支持 Ctrl / Shift 多选，双击=只选它",
              foreground="#888").pack(side="left", padx=8)

    lbwrap = ttk.Frame(f2)
    lbwrap.grid(row=2, column=0, sticky="nsew")
    lbwrap.columnconfigure(0, weight=1)
    lbwrap.rowconfigure(0, weight=1)
    lb = tk.Listbox(lbwrap, selectmode="extended", height=8, activestyle="none",
                    exportselection=False)
    sb = ttk.Scrollbar(lbwrap, orient="vertical", command=lb.yview)
    lb.configure(yscrollcommand=sb.set)
    lb.grid(row=0, column=0, sticky="nsew")
    sb.grid(row=0, column=1, sticky="ns")

    # ------------------------------------------------------------- 3. 选项
    f3 = ttk.LabelFrame(root, text=" 3. 选项 ", padding=10)
    f3.grid(row=2, column=0, sticky="ew", padx=10, pady=4)
    ttk.Radiobutton(f3, text="还原全部立绘(约 3000 个, 40+ 分钟)", variable=v_scope,
                    value="all").grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(f3, text="只还原上面选中的立绘  ★", variable=v_scope,
                    value="selected").grid(row=0, column=1, sticky="w", padx=(14, 0))
    ttk.Checkbutton(f3, text="同时导出各图层 PNG", variable=v_layer).grid(
        row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Checkbutton(f3, text="导出表情差分", variable=v_face).grid(
        row=1, column=1, sticky="w", padx=(14, 0), pady=(6, 0))
    ttk.Checkbutton(f3, text="表情贴图自动对齐（推荐）", variable=v_align).grid(
        row=1, column=2, sticky="w", padx=(14, 0), pady=(6, 0))
    ttk.Label(f3, text="小技巧: 先在上面的搜索框输入关键词(如 aerbien) → 点「全选(当前列表)」→ 就只还原这一批",
              foreground="#888").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ------------------------------------------------------------- 4. 运行
    f4 = ttk.Frame(root, padding=(10, 0))
    f4.grid(row=4, column=0, sticky="ew")
    f4.columnconfigure(3, weight=1)
    btn_run = ttk.Button(f4, text="开始还原", command=lambda: _start())
    btn_run.grid(row=0, column=0)
    btn_stop = ttk.Button(f4, text="停止", state="disabled", command=lambda: state.__setitem__("stop", True))
    btn_stop.grid(row=0, column=1, padx=6)
    ttk.Button(f4, text="打开输出目录", command=lambda: _open_out()).grid(row=0, column=2)
    btn_chk = ttk.Button(f4, text="检查素材完整性", command=lambda: _check_materials())
    btn_chk.grid(row=0, column=3, padx=(6, 0))
    ttk.Progressbar(f4, mode="determinate", maximum=100).grid(row=0, column=4, sticky="ew", padx=10)
    ttk.Label(f4, textvariable=v_prog).grid(row=0, column=5, sticky="e")
    f4.columnconfigure(4, weight=1)

    txt = tk.Text(root, height=8, font=("Consolas", 9))
    txt.grid(row=5, column=0, sticky="nsew", padx=10, pady=(8, 10))

    # ================================================================ 逻辑
    def _set_count():
        n_all, n_sel = len(state["all"]), len(state["sel"])
        extra = f"（列表显示 {len(state['shown'])}）" if len(state["shown"]) != n_all else ""
        v_count.set(f"共 {n_all} 个立绘{extra} · 已选 {n_sel} 个")

    def _sync_from_lb():
        """把列表框中"当前可见项"的选中状态并入 state['sel']（隐藏项保留）。"""
        shown = set(state["shown"])
        vis = {state["shown"][i] for i in lb.curselection() if i < len(state["shown"])}
        state["sel"] = (state["sel"] - shown) | vis

    def _refresh():
        q = v_search.get().strip().lower()
        state["shown"] = [n for n in state["all"] if q in n.lower()] if q else list(state["all"])
        lb.delete(0, "end")
        for n in state["shown"]:
            lb.insert("end", n)
        for i, n in enumerate(state["shown"]):
            if n in state["sel"]:
                lb.selection_set(i)
        _set_count()

    def _on_select(_=None):
        _sync_from_lb()
        _set_count()

    def _on_search(*_):
        _sync_from_lb()
        _refresh()

    def _sel_all():
        _sync_from_lb()
        state["sel"] |= set(state["shown"])
        _refresh()

    def _sel_invert():
        _sync_from_lb()
        for n in state["shown"]:
            state["sel"].symmetric_difference_update({n})
        _refresh()

    def _sel_clear():
        _sync_from_lb()
        state["sel"] = set()
        _refresh()

    def _pick_only(event):
        i = lb.nearest(event.y)
        if 0 <= i < len(state["shown"]):
            _sync_from_lb()
            name = state["shown"][i]
            if not (event.state & 0x0004):        # 非 Ctrl
                state["sel"] = {name}
            else:
                state["sel"] ^= {name}
            _refresh()
            v_scope.set("selected")

    def _pick_bundles():
        p = filedialog.askdirectory(title="选择 AssetBundles 根目录")
        if p:
            v_bundles.set(p)
            if not v_out.get():
                v_out.set(os.path.join(os.path.dirname(p), "还原输出"))
            _scan()

    def _pick_out():
        p = filedialog.askdirectory(title="选择输出目录")
        if p:
            v_out.set(p)

    def _scan():
        b = v_bundles.get().strip()
        pd_ = os.path.join(b, "painting")
        if not os.path.isdir(pd_):
            v_count.set("未找到 painting 目录")
            log(f"[错误] 不是有效的 AssetBundles 根目录: {b}")
            return
        state["all"] = find_paintings(pd_)
        state["sel"] &= set(state["all"])
        log(f"扫描完成: 共 {len(state['all'])} 个立绘配置包")
        _refresh()

    def _open_out():
        o = v_out.get().strip()
        if o and os.path.isdir(o):
            os.startfile(o)
        else:
            messagebox.showinfo("提示", "输出目录还不存在")

    # ---- 日志用队列，避免跨线程操作控件 ----
    q = queue.Queue()

    class _W(io.TextIOBase):
        def write(self, s):
            q.put(s)
            return len(s)

    def _pump():
        try:
            while True:
                txt.insert("end", q.get_nowait())
        except queue.Empty:
            pass
        txt.see("end")
        root.after(100, _pump)

    pb = None
    for w in f4.winfo_children():
        if isinstance(w, ttk.Progressbar):
            pb = w

    def _progress(i, n, name):
        pb.configure(maximum=n, value=i)
        v_prog.set(f"{i}/{n}  {name}")

    def _start():
        b = v_bundles.get().strip()
        if not os.path.isdir(os.path.join(b, "painting")):
            messagebox.showwarning("提示", "请先选择正确的 AssetBundles 根目录（需含 painting 子目录）")
            return
        _sync_from_lb()
        only = None
        if v_scope.get() == "selected":
            if not state["sel"]:
                messagebox.showwarning("提示", "还没有选择立绘。\n在列表里 Ctrl/Shift 多选，或者直接双击某个立绘。")
                return
            only = sorted(state["sel"])
        elif not state["all"]:
            _scan()
            if not state["all"]:
                return
        out = v_out.get().strip() or os.path.join(os.path.dirname(b), "还原输出")
        v_out.set(out)
        state["stop"] = False
        btn_run.config(state="disabled")
        btn_stop.config(state="normal")
        pb.configure(value=0, maximum=len(only) if only else 1)

        def _work():
            old = sys.stdout
            sys.stdout = _W()
            t0 = time.time()
            try:
                log("=" * 60)
                log(f"输出目录: {out}")
                if only:
                    log(f"只还原 {len(only)} 个: {', '.join(only)}")
                else:
                    log("还原全部立绘")
                run(b, out, only=only, want_layer=v_layer.get(),
                    want_face=v_face.get(), progress=_progress,
                    should_stop=lambda: state["stop"], face_align=v_align.get())
                log(f">>> 完成, 用时 {time.time()-t0:.1f} 秒")
                v_prog.set("完成")
            except Exception:
                log(traceback.format_exc())
                v_prog.set("出错")
            finally:
                sys.stdout = old
                btn_run.config(state="normal")
                btn_stop.config(state="disabled")

        threading.Thread(target=_work, daemon=True).start()

    def _check_materials():
        b = v_bundles.get().strip()
        if not os.path.isdir(os.path.join(b, "painting")):
            messagebox.showwarning("提示", "请先选择正确的 AssetBundles 根目录（需含 painting 子目录）")
            return
        _sync_from_lb()
        scope = sorted(state["sel"]) if (v_scope.get() == "selected" and state["sel"]) else None
        out_txt = os.path.join(v_out.get().strip() or os.path.dirname(b) or b, "素材缺失清单.txt")
        state["stop"] = False
        btn_run.config(state="disabled")
        btn_chk.config(state="disabled")
        btn_stop.config(state="normal")
        pb.configure(value=0, maximum=1)

        def _work():
            old = sys.stdout
            sys.stdout = _W()
            t0 = time.time()
            try:
                log("=" * 60)
                if scope:
                    log(f"检查素材完整性（仅已选的 {len(scope)} 个立绘）")
                else:
                    log("检查素材完整性（全部立绘）")
                check_materials_report(b, out_txt=out_txt, only=scope,
                                       progress=_progress,
                                       should_stop=lambda: state["stop"])
                log(f">>> 检查完成, 用时 {time.time()-t0:.1f} 秒")
                v_prog.set("完成")
            except Exception:
                log(traceback.format_exc())
                v_prog.set("出错")
            finally:
                sys.stdout = old
                btn_run.config(state="normal")
                btn_chk.config(state="normal")
                btn_stop.config(state="disabled")

        threading.Thread(target=_work, daemon=True).start()

    lb.bind("<<ListboxSelect>>", _on_select)
    lb.bind("<Double-Button-1>", _pick_only)
    v_search.trace_add("write", _on_search)
    txt.configure(state="normal")

    _pump()
    log("使用步骤: 选 AssetBundles 根目录 → 在列表里选立绘 → 点「开始还原」")
    log("提示: 不选任何立绘、并保持「还原全部立绘」时, 会还原全部（约 3000 个, 约 45 分钟）")
    root.mainloop()


# ============================================================================
# 入口
# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="碧蓝航线 静态立绘还原工具")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {VERSION}")
    ap.add_argument("--bundles", help="AssetBundles 根目录(含 painting/)")
    ap.add_argument("--out", help="输出目录")
    ap.add_argument("--only", help="只处理这些立绘名, 逗号分隔")
    ap.add_argument("--layer", action="store_true", help="同时导出各图层")
    ap.add_argument("--no-face", action="store_true", help="不导出表情差分")
    ap.add_argument("--no-face-align", action="store_true",
                    help="关闭表情贴图自动对齐(默认开启)")
    ap.add_argument("--jobs", type=int, help="最多处理 N 个(调试用)")
    ap.add_argument("--unpacked", nargs=3, metavar=("TEXDIR", "MESHDIR", "OUTDIR"),
                    help="仅用解包好的 Texture2D/Mesh 目录做分层还原")
    ap.add_argument("--gui", action="store_true", help="打开图形界面")
    a = ap.parse_args()

    if a.gui or len(sys.argv) == 1:
        gui()
        return
    if a.unpacked:
        restore_unpacked(*a.unpacked, only=a.only)
        return
    if a.bundles:
        only = [s.strip() for s in a.only.split(",")] if a.only else None
        run(a.bundles, a.out or os.path.join(a.bundles, "..", "还原输出"),
            only=only, want_layer=a.layer, want_face=not a.no_face, jobs=a.jobs,
            face_align=not a.no_face_align)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
