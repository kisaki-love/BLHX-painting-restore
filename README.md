# Azur Lane Painting Restore

Restore static illustrations and export Spine or Live2D assets from your local *Azur Lane* game files. This repository contains the v2.2 source for a Windows x64 desktop app and a command-line exporter. The interface is in Simplified Chinese.

**[Download the v2.1 Windows x64 portable ZIP](https://github.com/kisaki-love/BLHX-painting-restore/releases/download/v2.1/PaintingRestore-v2.1-windows-x64.zip)** · [v2.1 release notes](https://github.com/kisaki-love/BLHX-painting-restore/releases/tag/v2.1) · [Changelog](CHANGELOG.md) · [中文说明](#碧蓝航线立绘还原工具)

![Resource selection in the Windows desktop app](docs/界面.png)

| Entry | Output |
|---|---|
| Backgrounds | Story scenes, loading illustrations and layered textures as individual PNGs. |
| Startup / login | Login images and available update backgrounds as PNGs. |
| Painting | Full-size transparent artwork, numbered expression variants and optional layers. Identical source faces are skipped before composition. |
| Spine | Skeleton, atlas and texture files extracted for the selected model. |
| Live2D | Compiled model, texture atlases, original Unity animation clips and converted Cubism motions. Physics, poses and expressions are included when available. |

Background and login images are saved directly in the chosen folder. Additional textures in a bundle get numbered suffixes (`~2`, `~3`, …).

### Get started

1. Extract the entire ZIP and run `PaintingRestore.exe`. Keep the adjacent `_internal` directory. The portable build includes Python and its dependencies.
2. Choose a resource type and select the game package folder, `files`, or `AssetBundles`. For example, select `com.bilibili.azurlane/files/AssetBundles` so that sibling resource folders can be found.
3. Search for a skin, select it, choose an output directory and start the task. Expand a result to see missing assets or conversion issues.

For Live2D, open the generated `<skin>.model3.json` in Cubism Viewer. The exporter keeps the serialized clips in `AnimationClip/` and writes supported parameter and part-opacity curves to `motions/*.motion3.json`. It also exports model opacity and supported eye/mouth controller curves. Source paths with no matching target are recorded in `manifest.json`, with path and property hashes for diagnosis. The game’s touch rules, voice menus and EX Studio interactions require separate configuration.

### Command line

```powershell
.\PaintingRestore-CLI.exe --asset-type live2d --bundles "D:\Game\files\AssetBundles" --out "D:\Exports" --only nabulesi_3
```

Other asset types include `painting`, `spine`, `background`, and `startup`. Run `--help` for all options.

## 碧蓝航线立绘还原工具

从本地《碧蓝航线》资源还原静态立绘，或提取 Spine、Live2D 动态素材。当前源码版本为 v2.2。**软件免费，请勿付费购买。** [下载 v2.1 Windows x64 便携包](https://github.com/kisaki-love/BLHX-painting-restore/releases/download/v2.1/PaintingRestore-v2.1-windows-x64.zip) · [GitHub 更新地址](https://github.com/kisaki-love/BLHX-painting-restore/releases) · [更新记录](CHANGELOG.md)

1. 完整解压 ZIP，双击 `PaintingRestore.exe`。保留同目录的 `_internal` 文件夹，无需另装 Python。
2. 在首页选择资源类型，拖入游戏包根目录、`files` 或 `AssetBundles`。目录示例为 `com.bilibili.azurlane/files/AssetBundles`。
3. 搜索并勾选资源，设置输出目录后开始。勾选状态会跨搜索保留；启用「隐藏 _hx 和谐版」会从本次处理范围排除这些变体。任务结束后可展开查看问题或重试失败项。

| 入口 | 能导出什么 |
|---|---|
| 过场与剧情背景 | 剧情与加载贴图按原尺寸平铺导出 PNG。 |
| 启动与登录图 | 登录与更新背景平铺导出，同包多图追加 `~2`、`~3` 等后缀。 |
| 静态立绘 | 主图、从 `_exp_1.png` 开始编号的表情差分，可选分层 PNG。每套底图只合成一次，贴脸前跳过重复源表情。 |
| Spine | 骨骼、图集描述及贴图。按模型引用从共享资源包提取所选变体的素材。 |
| Live2D | `.moc3`、贴图、原始 `.animclip.bin` 和转换后的 `.motion3.json`。模型和贴图索引完整时生成 `.model3.json` 入口，物理、姿势、表情及遮罩按资源提供。 |

要预览 Live2D，请在 Cubism Viewer 中打开导出目录里的 `<皮肤名>.model3.json`。动作导出支持模型透明度及可匹配的眨眼、口型轨道。清单分别记录源模型无对应目标、无绑定动作和未解决的转换问题，并保留原始动作。游戏点击、拖动、语音菜单及 EX Studio 互动需另外配置。命令行参数见上方示例或 `PaintingRestore-CLI.exe --help`。

### Run from source / 从源码运行

Python 3.12

```sh
python -m pip install -r requirements.txt
python al_restore.py --gui
```

[Application modules / 应用模块](painting_restore/) · [Tests / 测试](tests/) · [Build notes / 构建说明](AGENTS.md)

### License / 使用许可

The v2.2 project license permits personal, non-commercial use of the tool. Game files and game content in exported results are subject to applicable laws, service terms and rights holders’ permissions.

v2.2 的项目许可允许个人非商业使用工具。游戏资源及导出内容中包含的游戏素材，适用相关法律、游戏服务协议和权利人的授权。

[Project license / 许可全文](LICENSE)

### Credits / 致谢

Developed with AI assistance. The approach to composing artwork layers draws on ALPA. 本工具由 AI 辅助开发，立绘图层的拼合思路参考了 [ALPA](https://github.com/Deficuet/AzurLanePaintingAnalysis-Kt)。
