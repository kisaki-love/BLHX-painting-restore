from pathlib import Path
import os
import sys
import shutil
import importlib.util
from PyInstaller.utils.hooks import collect_all, collect_data_files
root = Path(SPECPATH)
# Resolve Windows DLLs only from this Python and Windows, not unrelated host apps.
if os.name == "nt":
    windows = Path(os.environ["WINDIR"])
    os.environ["PATH"] = os.pathsep.join(map(str, (
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        windows / "System32", windows,
    )))
data, binaries, hidden = [], [], []
for package in ("astc_encoder", "etcpak", "texture2ddecoder", "tpk_ar"):
    files, libs, modules = collect_all(package)
    data.extend(files)
    binaries.extend(libs)
    hidden.extend(modules)
for package in ("UnityPy", "archspec"):
    data.extend(collect_data_files(package))
# UnityPy eagerly imports its audio exporter when a texture is requested.
# This image-only distribution ships the upstream graphics exporters, without FMOD.
graphics_root = Path(workpath) / "graphics-only"
package = graphics_root / "UnityPy"
upstream = Path(importlib.util.find_spec("UnityPy").origin).parent
shutil.copytree(upstream, package, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
initializer = package / "export" / "__init__.py"
initializer.write_text(initializer.read_text(encoding="utf8").replace("    AudioClipConverter,\n", "").replace('    "AudioClipConverter",\n', ""), encoding="utf8")
a = Analysis([str(root / "al_restore.py")], pathex=[str(graphics_root),str(root)],
             datas=data, binaries=binaries, hiddenimports=hidden,
             excludes=["fmod_toolkit", "pyfmodex", "UnityPy.export.AudioClipConverter", "tkinter", "matplotlib", "scipy", "IPython", "pytest"])
assert any(n == "UnityPy.export" and str(graphics_root) in path for n,path,_ in a.pure), "graphics-only UnityPy was not selected"
pyz = PYZ(a.pure)
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PaintingRestore", console=False, upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="PaintingRestore-CLI", console=True, upx=False)
dist = COLLECT(gui, cli, a.binaries, a.datas, name="PaintingRestore-v2.1", upx=False)
