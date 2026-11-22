# 更新记录

> 最新版本在最前面。v1.0 是首个公开发布，里面也记录了开发过程中修掉的关键问题。

## v2.2 — Project organization and license

- The application modules are in `painting_restore/`. The desktop app, CLI and Windows build share one version value.
- Windows builds include the v2.2 license for personal, non-commercial use of the tool.

### 中文

- 应用内部模块归入 `painting_restore/`。桌面端、命令行和 Windows 构建统一读取版本号。
- Windows 构建附带 v2.2 工具使用许可，说明个人非商业使用的权限与游戏资源的授权范围。

## v2.1 — Live2D motion conversion and background export

- Export model opacity and supported lip-sync and eye-blink tracks. Resolve parameter and part IDs against the source prefab and compiled model. Keep the original Unity clips and report missing targets per motion.
- Export scene backgrounds, loading art and login screens directly as PNGs. Additional textures use numbered suffixes.
- Download the Windows x64 ZIP, extract the whole folder and run `PaintingRestore.exe`. The CLI is included.

### 中文

- Live2D 动作转换支持模型透明度、可匹配的口型与眨眼控制器；按源预制体与编译模型核对参数和部件。无对应目标、空动作和未解决的绑定在清单中分别列出，原始 AnimationClip 保留。
- 新增过场与剧情背景、启动与登录图导出。PNG 平铺保存到所选目录，同包多张贴图追加编号。
- Windows x64 便携包完整解压后运行 `PaintingRestore.exe`，命令行入口为 `PaintingRestore-CLI.exe`。

## v2.0-preview — Windows x64

Restore paintings or extract Spine and Live2D assets through three desktop entries. The portable ZIP includes the GUI and CLI. Live2D exports preserve original Unity AnimationClips, convert supported curves to Cubism motion3 files and register the motions in the generated model3 entry.

三个入口分别处理静态立绘、Spine 和 Live2D。桌面端支持搜索、多选、素材检查、取消与失败重试。Live2D 同时导出原始动作与可供 Cubism Viewer 读取的转换动作；Spine 按选中模型提取素材。v2.0 新增自有代码和文档采用非商业学习研究许可。

**Windows:** Extract the full ZIP and run `PaintingRestore.exe`. 完整解压便携包，双击 `PaintingRestore.exe`。游戏专有互动和语音菜单需另外配置。

## v1.2 — Expression Rendering / 表情合成优化

### English

Each illustration’s base layers are composed once. Before drawing a face, the tool skips identical source textures and the default face if it has already been applied to the main artwork.

Expressions are numbered from `_exp_1.png`. Re-exporting refreshes the numbered files and removes the legacy `_exp.png` copy. Using `--no-face` clears previously exported expressions for that illustration.

The repository introduction and desktop window now introduce the tool in English first, followed by Chinese. Tested on four illustrations from asset version `0.0.1820`, producing four main images and 43 expression variants.

### 中文

每套立绘只合成一次底图。工具在贴脸前跳过相同的源贴图，已用于补全主图的默认表情也只绘制一次。

表情差分从 `_exp_1.png` 连续编号。重新导出时会清理旧版 `_exp.png` 副本和多余编号。使用 `--no-face` 重新导出时，会清空该立绘之前导出的表情。

仓库首页和工具窗口采用英文在前、中文在后的简介。使用立绘资源版本 `0.0.1820` 实测 `banerwei_4`、`nabulesi_3`、`u2501_2` 和 `xinaoerliang_3`，共导出 4 张主图与 43 张表情差分。

**v1.1（修「脸上一个大白框」+ 增量素材的小问题）**

症状：少数立绘（如 `u2501_2`、`leiniya_2`、`z13`、`silverfox`、`bote`、`alabama_3`）
成品脸部是一片**纯色方块**，表情也贴不上去 —— 看起来像脸上盖了张白纸。

原因：新一批立绘把脸拆成了独立 Sprite（`paintingface/<立绘名>` 里的 0 号表情），
**人物层在脸部是挖空的矩形洞**。v1.0 只把 `face` 节点当"表情框"用、从不贴它引用的
Sprite，于是基础图（`<立绘名>.png`）在洞的位置透出了背景层 —— 背景是浅色就成"白框"，
是天空就成"蓝框"。老立绘（`2b` / `aerbien` 等）脸直接画在人物层里，所以看不出问题，
这就是为什么只有很小一部分立绘有毛病。

修复：

- 读配置包时记录 `face` 节点引用的 Sprite PathID（`PaintingPrefab.face_sprite_pid`）
- 合成时顺手判定"这个洞是不是真的存在"（`_probe_hole`：该图层在 face 矩形内几乎全透明、
  矩形外一圈明显有内容）—— 只有真的挖了洞才补，避免给老立绘糊上第二张脸
- 补的那张图按 Sprite PathID 在 `paintingface` 里精确定位（不再是"随便取第一张"）；
  变体自己没有表情包时（`jian_3_hx` / `zhaohe_4_hx` / `baoduoliuhua_wjz`）按 PathID
  向基础立绘的表情包借用；表情包从 1 开始编号时用第一张当默认脸
- 全库扫描 3036 个立绘：**48 个**判定为"脸部挖洞"，其中 43 个已自动补脸（19 个按 prefab 的
  Sprite PathID 定位，其余 24 个是配置包没写引用、按 0 号/首张贴图兜底）；剩下 5 个
  （`canglong` / `fulami` / `hdn302_1` / `u556_3_hx` / `zhenming_2`）本来就没有 `paintingface`
  表情包，属于占位用途的小图，不是真的缺脸
- 判过之后仍然不满足"洞"特征的（`2b` / `aerbien` / `z52` 等老式做法）输出与 v1.0 **逐像素一致**
- **图层名拼写错位**：新版立绘里 `leiyierbei_2` 的图层叫 `leiyibeier_2_rw`，但贴图包叫
  `leiyierbei_2_rw_tex`，原来的精确/前缀匹配都找不到 → 人物层缺失（只剩背景）。
  现在会再找一轮「字符相同、顺序不同」的 `_tex` 包，并**用 Sprite PathID 确认是同一个
  资源**才采用（`_typo_tex_path`），校验不过仍然跳过
- 增量素材里只放了配置包、没放贴图包的立绘（`bisimai` / `shaenhuosite` / `u556` /
  `yueke_ger`），仍可从完整素材目录取旧贴图 —— 用「更新目录 + 缺的包」合成一个临时目录
  跑即可，工具本身不用改
- 顺带：`_exp.png` 不再重复合成一遍（直接用第 1 张表情），单张立绘省一次全图层合成

验证：表情差分文件与 v1.0 **逐字节一致**（`u2501_2` 18 张、`leiniya_2` 9 张全部相同），
基础图的变化严格限制在 face 矩形内；抽查 8 个"有洞"与 8 个"无洞"立绘，逐一目视确认。

**v1.0（首个公开发布）**

功能：

- **完整还原**：读游戏自己的布局数据（画布尺寸 / 每层矩形与缩放 / 表情框）+ 按 Mesh 拼回图层
  → 人物、背景、前景的相对位置都是对的
- **表情差分**：`paintingface` 的贴图自动贴到 `face` 节点，输出 `<立绘名>_exp_N.png`
- **图形界面**：立绘清单可搜索、`Ctrl/Shift` 多选、全选(当前列表)/反选/清空、进度条、随时停止
- **命令行**：`--bundles / --out / --only / --layer / --no-face / --unpacked / --gui`
- **分层模式**：只有解包好的 `Texture2D` / `Mesh`（没有配置包）时，也能还原出每一层
- **素材体检** `check_materials.py`：列出哪些立绘缺哪一层的贴图包、哪些没有表情差分
- **表情贴图自动对齐**：兜底机制；图层位置正确时它不会改动任何东西

开发中修掉的关键坑（都很隐蔽，记下来避免以后再踩）：

1. **有 Mesh 的图层纵向定位**。Mesh 顶点是「图层原稿」的像素坐标，原点 `(0,0)` 就是该 Image
   矩形的**左下角**，图像顶边在原点上方 `ymax` 处 —— 必须用 `ymax` 定位，不能图省事用矩形高度；
   否则原稿比矩形大的图层会整段错位。实测最坏偏移 3266px，全库 **16.2%** 的立绘受影响
   （如 `baifeng` 人物低 447px、扇子低 684px）。
2. **无 Mesh 的图层**（图集里只有一整张图，如 `a2_2_tex`）走另一条分支：贴图/Sprite 裁剪结果
   已经是画布单位，位置直接取矩形高度，**不能再乘一次缩放**，否则会被推出画布、
   表现为「背景缺失／只剩上半张」。全库 **13.7%** 的立绘受影响。
3. **UI 节点不是画面内容**：`Touch` / `frameContain` 子树、`shop_hx*`（商店和谐标签）、
   `shadow` 都要整支跳过；而且某个图层取不到贴图时**不能兜底用背景包**，
   否则小矩形里会被糊上一块背景（`2b_2` 曾出现这种色块）。
4. **变体复用基础贴图**：`aerbien_hx` / `bangkeshan_2_hx` / `aimudeng_5_asmr` 这类变体的主图层，
   贴图并不在 `<图层名>_tex` 里，而是在基础立绘的包中（配置包引用的 Sprite PathID 完全一致）。
   现在会剥掉变体后缀去找，并**用 Sprite PathID 校验**确认是同一个资源才采用（校验不过就跳过，不猜）。
5. **表情框本身是准的**。`face` 节点的矩形没问题；早期看到的"表情贴歪"其实是上面第 1 条
   导致的人物层错位 —— 图层位置修对后，抽样 59 个带表情立绘，表情框与头部**全部 0 偏差**。

验证方式：拿**官方宣传图**当基准逐像素比对（`baifeng` 差异 `diff>150` 仅 0.09%、
`a2_2` 0.35%、平均色与官方一致），并用全库扫描量化每个 bug 的影响范围。

---
