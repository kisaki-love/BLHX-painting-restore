import argparse
import sys
from pathlib import Path
from rendering import VERSION, restore_unpacked
from tasks import Job, locate, choose, execute

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
    ap.add_argument("--asset-type",choices=("painting","spine","live2d"),default="painting",help="静态立绘或动态素材导出")
    a = ap.parse_args()

    if a.gui or len(sys.argv) == 1:
        from desktop import gui
        return gui()
    if a.unpacked:
        restore_unpacked(*a.unpacked, only=a.only)
        return
    if a.bundles:
        only = [s.strip() for s in a.only.split(",")] if a.only else None
        root = locate(a.bundles)
        names = choose(root, only, a.jobs, a.asset_type)
        job = Job(root, Path(a.out or root.parent / "还原输出"), names,
                  expressions=not a.no_face, layers=a.layer, align=not a.no_face_align, asset_kind=a.asset_type)
        def report(event):
            if event.kind == "result":
                print(f"[{event.done}/{len(names)}] {event.result}", flush=True)
        return int(any(r.status != "成功" for r in execute(job, report)))
    ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
