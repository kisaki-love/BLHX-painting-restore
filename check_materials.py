#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""素材完整性检查 —— 列出「哪些立绘缺哪一层的贴图包」。

用法（三种任选）：
    1) 双击 检查素材.bat                     ← 最省事，会问你素材在哪里
    2) python check_materials.py --bundles "...\\AssetBundles"
    3) 把 AssetBundles 文件夹拖到 检查素材.bat 上

可选参数：
    --out 文件      把缺失清单写到这个文本文件（默认 素材缺失清单.txt）
    --only a,b,c    只检查这几个立绘
    --jobs N        只检查前 N 个（调试用）

它做的事和还原工具一致：读 `painting\\<立绘名>` 配置包 → 列出每个图层
→ 按工具规则（含变体回退 + Sprite PathID 校验）去找 `<图层名>_tex`。
找不到的层，就是还原时成品会缺的那一块。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import UnityPy  # noqa: E402
UnityPy.config.FALLBACK_UNITY_VERSION = "2022.3.0"
from al_restore import check_materials_report  # noqa: E402

SAVED = os.path.join(HERE, "素材路径.txt")


def _valid(p):
    return bool(p) and os.path.isdir(os.path.join(p, "painting"))


def resolve_bundles(explicit):
    """按 命令行 → 上次记住的路径 → 常见位置 的顺序找 AssetBundles 根目录。"""
    if explicit:
        return explicit.strip().strip('"').strip("'")
    if os.path.isfile(SAVED):
        try:
            p = open(SAVED, encoding="utf-8").read().strip()
            if _valid(p):
                print("沿用上次的素材目录: %s" % p)
                return p
        except OSError:
            pass
    # 常见摆放位置(脚本旁边 / 各个盘的根目录)
    cands = [os.path.join(HERE, "AssetBundles"), os.path.join(os.path.dirname(HERE), "AssetBundles")]
    for d in "CDEFGHIJ":
        cands += [d + ":\\AssetBundles", d + ":\\包\\AssetBundles"]
    for cand in cands:
        if _valid(cand):
            print("自动找到素材目录: %s" % cand)
            return cand
    # 交互式问一次
    print("没找到 AssetBundles 根目录。")
    print("请输入它的完整路径（就是包含 painting 子文件夹的那一层），直接回车放弃：")
    try:
        p = input("> ").strip().strip('"').strip("'")
    except EOFError:
        p = ""
    return p


def main():
    ap = argparse.ArgumentParser(description="检查立绘素材完整性")
    ap.add_argument("--bundles", help="AssetBundles 根目录(含 painting/)，省略则自动查找")
    ap.add_argument("--out", help="缺失清单写到哪里(默认 素材缺失清单.txt)")
    ap.add_argument("--only", help="只检查这些立绘, 逗号分隔")
    ap.add_argument("--jobs", type=int, help="只检查前 N 个(调试用)")
    a = ap.parse_args()

    b = resolve_bundles(a.bundles)
    if not _valid(b):
        print("[错误] 这不是有效的 AssetBundles 根目录（里面要有 painting 文件夹）: %r" % b)
        print('       例: --bundles "H:\\<你的目录>\\AssetBundles"')
        return 1

    try:                                    # 记住路径, 下次不用再输
        with open(SAVED, "w", encoding="utf-8") as f:
            f.write(os.path.abspath(b))
    except OSError:
        pass

    only = [s.strip() for s in a.only.split(",")] if a.only else None
    out_txt = a.out or os.path.join(HERE, "素材缺失清单.txt")
    r = check_materials_report(b, out_txt=out_txt, only=only, jobs=a.jobs)
    print("检查完毕：真缺主图层素材 %d 个 / 共 %d 个" % (len(r["real"]), r["total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
