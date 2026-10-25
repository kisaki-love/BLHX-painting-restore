# 碧蓝航线 · 静态立绘还原工具

![version](https://img.shields.io/badge/version-1.1-blue)
![python](https://img.shields.io/badge/python-3.10%2B-blue)
![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![license](https://img.shields.io/badge/license-MIT-green)

把游戏里被切碎、散落在图集里的立绘，还原成**一张完整的透明底 PNG**（含表情差分）。

> [!WARNING]
> 仅供个人学习研究使用，请勿用于 MOD 制作、二次分发或商业用途。
> 游戏素材版权归 蛮啾网络 / 勇仕网络 所有。 · For personal study only. Assets © Manjuu / Yongshi.

## 快速开始 · Quick start

需要 Python 3.10+ / Requires Python 3.10+

- **Windows**：双击 `启动工具.bat`（缺依赖会自动安装 / deps auto-install）
- **Linux / macOS**：`pip install -r requirements.txt`，然后 `./run.sh --gui`

打开界面后：选 `AssetBundles` 根目录 → 点「扫描 / 刷新」→ 勾选立绘 → 点「开始还原」。
结果在 `<输出目录>/<立绘名>/`：一张主图 + `_exp_1..N.png` 表情差分。

## 命令行 · CLI

```bash
# 全部还原（约 3000 个，40 分钟左右）
python al_restore.py --bundles "D:\Game\AzurLane\AssetBundles" --out "D:\还原输出"

# 只还原指定的几个
python al_restore.py --bundles "...\AssetBundles" --out "D:\还原输出" --only "aerbien_2,sali_2"
```

| 参数 Argument | 说明 Description |
|---|---|
| `--bundles DIR` | AssetBundles 根目录（需含 `painting\`）/ asset root |
| `--out DIR` | 输出目录 / output folder |
| `--only a,b` | 只还原指定立绘 / only these paintings |
| `--layer` | 同时导出每个图层 / dump each layer |
| `--no-face` | 不导出表情差分 / skip expressions |
| `--jobs N` | 只跑前 N 个（调试）/ first N only |
| `--gui` | 打开图形界面 / open GUI |

## 常见问题 · FAQ

- **缺背景、少一块？** 先跑 `check_materials.py`，多半是资源里缺对应的 `_tex` 贴图包。
  Missing parts? Run `check_materials.py` — it lists the absent `_tex` packs.
- **和别人的结果对不上？** 素材版本不同，用哪版素材出来就是哪版。
  Different game version, different pixels.
- **输出尺寸特别大（8000+ px）？** 画布尺寸由游戏自己定，工具按原样输出。
  Canvas size comes from the game data.

## 说明 · Notes

- 只读取本地资源并重新拼合：**不修改游戏、不注入进程、不绕过任何校验**。
  Reads local assets and recomposes them only.
- 不收费、不以此牟利，也不附带任何游戏素材。
  Free of charge; ships no game assets.

## 致谢 · Credits

**DeepSeek-V4.1-Flash** —— 本项目代码由该大模型协助编写（图层定位逆向、全库批量验证、文档整理）。

图层拼图思路参考开源项目 [ALPA](https://github.com/Deficuet/AzurLanePaintingAnalysis-Kt)，
布局规则由本项目独立逆向验证（用官方宣传图逐像素比对）。

维护者 / Maintainer：[kisaki-love](https://github.com/kisaki-love) · 欢迎提 Issue / PR

## License

MIT
