# 项目约定

- 保留根目录的 `al_restore.py` 和 `check_materials.py` 作为源码入口。应用内部模块放在 `painting_restore/` 并使用包内相对导入。命令行只在启动桌面界面时加载 Qt。
- `painting_restore/tasks.py` 负责扫描、执行和发布。导出先写暂存目录，再由发布流程更新用户目录。用 `python -m unittest discover -s tests` 运行现有测试，无需提供游戏资源。
- 在 Windows x64 上用 Python 3.12 安装 `requirements.txt` 和 `pyinstaller`，运行 `python -m PyInstaller portable.spec`。构建保留图形和命令行两个 EXE、共享 `_internal/` 以及项目许可文件。发布 ZIP 时随包附上实际捆绑组件的第三方许可，存放于 `ThirdPartyLicenses/`。
- 当前源码的自有代码和文档适用根目录的 `LICENSE`。历史版本的许可见对应标签。
