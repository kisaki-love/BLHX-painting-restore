#!/usr/bin/env bash
# Linux / macOS 启动脚本（Windows 用 启动工具.bat）
#   ./run.sh              打开图形界面
#   ./run.sh --bundles "..." --out "..."     命令行模式
# GUI 需要系统装了 tkinter（Debian/Ubuntu: sudo apt install python3-tk）
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

if ! "$PY" -c "import PIL, numpy, UnityPy" >/dev/null 2>&1; then
    echo "缺少依赖，正在安装 ..."
    "$PY" -m pip install -U -r requirements.txt
fi

export PYTHONIOENCODING=utf-8
if [ $# -eq 0 ]; then
    exec "$PY" al_restore.py --gui
fi
exec "$PY" al_restore.py "$@"
