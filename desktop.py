"""Light desktop workspace. All widget access is confined to the Qt event thread."""
from __future__ import annotations

import ctypes
import os
import sys
from collections import Counter
from pathlib import Path
from threading import Event as StopFlag

from PySide6.QtCore import Qt, QThread, Signal, QUrl, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QColor, QPolygonF
from PySide6.QtCore import QMargins, QPointF, QSettings
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QRadioButton, QButtonGroup, QFileDialog, QListWidget, QListWidgetItem,
    QStackedWidget, QProgressBar, QTreeWidget, QTreeWidgetItem, QHeaderView,
    QGraphicsOpacityEffect, QAbstractItemView, QScrollArea, QSizePolicy
)
from thumbs import Thumbnails
import dynamic_assets
from tasks import IMPORT_EXAMPLE, Job, choose, execute, locate

STEPS = ("导入资源", "选择模式", "设置导出", "执行与结果")
TITLES = ("导入游戏资源", "选择处理模式", "设置输出", "任务进度与结果")
STYLE = """
QWidget {background:#f3f6fa;color:#182f49;font:10pt "Microsoft YaHei UI";}
QFrame#side,QFrame#panel,QFrame#panel QWidget {background:white}
QFrame#side QLabel {background:transparent}
QFrame#side {border-right:1px solid #dbe3ed}
QFrame#panel {border:1px solid #d8e2ed;border-radius:12px}
QLabel#brand {font-size:15pt;font-weight:700;color:#163c6b}
QLabel#title {font-size:22pt;font-weight:700}
QLabel#lead,QLabel#hint {color:#415b71}
QLabel#folderGuide {font-size:9pt;color:#485c70}
QPushButton#step {text-align:left;padding:9px 10px;border:1px solid transparent;border-radius:7px;background:white;color:#51657a}
QPushButton#step:enabled:hover {background:#edf5ff;color:#134d91}
QPushButton#step:focus {border-color:#638cbd}
QPushButton#step:disabled {color:#64768a;background:white}
QPushButton#step[active="true"] {background:#dfedff;color:#134d91;font-weight:bold;border-radius:7px}
QLineEdit,QComboBox,QListWidget,QTreeWidget {background:white;border:1px solid #aebdd0;border-radius:6px;padding:5px}
QLineEdit:focus,QComboBox:focus,QListWidget:focus,QTreeWidget:focus {border-color:#1b68bb}
QPushButton {padding:6px 14px;background:white;border:1px solid #aebdd0;border-radius:6px;min-height:20px}
QPushButton:hover {background:#edf5ff;border-color:#638cbd}
QPushButton#entry {text-align:left;font-size:12pt;padding:8px 18px}
QPushButton#primary {background:#185ba5;color:white;border-color:#185ba5;font-weight:bold}
QPushButton#primary:hover {background:#104e92}
QPushButton:disabled,QPushButton#primary:disabled {background:#e6ecf2;border-color:#dce3eb;color:#66788a}
QFrame#drop {background:#f1f7ff;border:2px dashed #9cbadd;border-radius:10px}
QFrame#drop QLabel {background:transparent;color:#174779}
QProgressBar {min-height:10px;max-height:10px;border:0;border-radius:5px;background:#dce6ef}
QProgressBar::chunk {background:#276dc0;border-radius:5px}
QTreeWidget::item,QListWidget::item {min-height:23px}
QHeaderView::section {background:#eaf0f6;border:0;padding:4px;text-align:left}
"""


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1c589c"))
    painter.drawRoundedRect(0, 0, 64, 64, 15, 15)
    for y, color in ((37, "#8ebce9"), (30, "#cbe4fa"), (23, "#ffffff")):
        painter.setBrush(QColor(color))
        painter.drawPolygon(QPolygonF([QPointF(11,y), QPointF(32,y-11), QPointF(53,y), QPointF(32,y+11)]))
    painter.end()
    return QIcon(pix)


def motion_enabled():
    if sys.platform == "win32":
        enabled = ctypes.c_int()
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    return True


def label(text="", role=""):
    widget = QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(role)
    return widget


def button(text, slot):
    widget = QPushButton(text)
    widget.clicked.connect(slot)
    return widget


class Worker(QThread):
    update = Signal(object)
    def __init__(self, action, parent):
        super().__init__(parent)
        self.action = action
        self.stop = StopFlag()
        self.value = None
        self.error = ""

    def run(self):
        try:
            self.value = self.action(self.update.emit, self.stop.is_set)
        except Exception as exc:
            self.error = str(exc) or type(exc).__name__


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.auto_open = False
        self.setWindowTitle("碧蓝航线立绘还原工具 v2.0 预览版")
        self.setWindowIcon(icon())
        self.setAcceptDrops(True)
        self.resize(990, 675)
        self.setMinimumSize(690, 480)
        self.worker = None
        self.root = None
        self.catalog = ()
        self.names = ()
        self.selected = set()
        self.last_job = None
        self.results = {}
        self.close_pending = False
        self.motion = motion_enabled()
        shell = QVBoxLayout(self)
        shell.setContentsMargins(0,0,0,0)
        self.screens = QStackedWidget()
        shell.addWidget(self.screens)
        self.screens.addWidget(self.entry_page())
        workspace = QWidget()
        self.screens.addWidget(workspace)
        outer = QHBoxLayout(workspace)
        outer.setContentsMargins(0,0,0,0)
        outer.setSpacing(0)
        side = QFrame(objectName="side")
        side.setFixedWidth(190)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(18,27,18,20)
        mark = QLabel()
        mark.setPixmap(self.windowIcon().pixmap(42,42))
        nav.addWidget(mark)
        nav.addWidget(label("Painting Restore","brand"))
        nav.addWidget(label("v2.0  ·  Windows 预览版","hint"))
        self.section_name = label("", "hint")
        nav.addWidget(self.section_name)
        self.home_button = button("返回功能首页", lambda: self.screens.setCurrentIndex(0))
        nav.addWidget(self.home_button)
        nav.addSpacing(12)
        self.steps = []
        for i, title in enumerate(STEPS, 1):
            entry = QPushButton(f"{i:02}    {title}", objectName="step")
            entry.clicked.connect(lambda checked=False, target=i-1: self.page(target))
            nav.addWidget(entry)
            self.steps.append(entry)
        nav.addStretch()
        self.preferences = QSettings("BLHX", "PaintingRestore")
        self.hide_hx = QCheckBox("隐藏 _hx 和谐版")
        self.hide_hx.setChecked(self.preferences.value("hide_hx", False, type=bool))
        self.hide_hx.setToolTip("从列表、检查和导出中排除名称以 _hx 结尾的立绘")
        self.hide_hx.toggled.connect(self.hx_changed)
        nav.addWidget(self.hide_hx)
        nav.addWidget(label("非商业学习研究版","hint"))
        outer.addWidget(side)
        body = QVBoxLayout()
        outer.addLayout(body,1)
        body.setContentsMargins(23,18,23,14)
        self.title = label(role="title")
        body.addWidget(self.title)
        panel = QFrame(objectName="panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(15,13,15,13)
        self.stack = QStackedWidget()
        panel_layout.addWidget(self.stack)
        body.addWidget(panel,1)
        for build in (self.import_page,self.mode_page,self.output_page,self.run_page):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(3,3,3,3)
            layout.setSpacing(9)
            build(layout)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.stack.addWidget(scroll)
        self.note = label(role="hint")
        body.addWidget(self.note)
        foot = QHBoxLayout()
        self.back = button("上一步",self.previous)
        foot.addWidget(self.back)
        foot.addStretch()
        self.next = button("下一步",self.advance)
        self.next.setObjectName("primary")
        foot.addWidget(self.next)
        body.addLayout(foot)
        self.fade = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self.fade)
        self.fade.setOpacity(1)
        self.fade_anim = QPropertyAnimation(self.fade,b"opacity",self)
        self.fade_anim.setDuration(150)
        self.page(0)

    def entry_page(self):
        home = QWidget()
        layout = QVBoxLayout(home)
        layout.setContentsMargins(34,28,34,28)
        layout.addStretch()
        layout.addWidget(label("Azur Lane Painting Restore", "brand"))
        layout.addWidget(label("选择资源类型", "title"))
        self.entry_buttons = {}
        for kind, title, detail in (
            ("painting","静态立绘 · painting","还原主图、表情差分与图层"),
            ("spine","Spine 动态立绘","提取骨骼、图集描述与贴图"),
            ("live2d","Live2D 模型","提取模型、贴图、原始动作及模型入口文件"),
        ):
            entry = QPushButton(title + "   ｜   " + detail)
            entry.setObjectName("entry")
            entry.setMinimumHeight(48)
            entry.clicked.connect(lambda checked=False, k=kind: self.enter(k))
            self.entry_buttons[kind] = entry
            layout.addWidget(entry)
        layout.addStretch()
        layout.addWidget(label("本软件免费，请勿付费购买。", "hint"))
        return home

    def enter(self, kind):
        if self.worker:
            return
        if kind != self.asset_kind:
            self.selected.clear()
            self.search.clear()
            self.results.clear()
            self.tree.clear()
            self.last_job = None
        self.asset_kind = kind
        self.catalog = self.asset_catalogs.get(kind, ())
        self.mode.button(0).setChecked(True)
        self.check_all.setChecked(False)
        self.section_name.setText({"painting":"静态立绘", "spine":"Spine 动态立绘", "live2d":"Live2D 模型"}[kind])
        self.steps[1].setText("02    选择模式" if kind=="painting" else "02    选择素材")
        self.apply_scope()
        self.filter_names()
        self.screens.setCurrentIndex(1)
        self.page(1 if self.root else 0)
        if not self.root and self.source.text().strip():
            self.auto_open = True
            self.scan()

    def import_page(self, layout):
        layout.addWidget(label("Restore illustrations and extract animation assets from local Azur Lane bundles.","lead"))
        layout.addWidget(label("从本地游戏资源还原静态立绘，提取 Spine 和 Live2D 素材。","lead"))
        layout.addWidget(label("本软件免费，请勿付费购买。"))
        releases = "https://github.com/kisaki-love/BLHX-painting-restore/releases"
        updates = label(role="hint")
        updates.setTextFormat(Qt.TextFormat.RichText)
        updates.setText(f'GitHub 更新地址：<a href="{releases}">{releases}</a>')
        updates.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        updates.setOpenExternalLinks(True)
        layout.addWidget(updates)
        drop = QFrame(objectName="drop")
        box = QVBoxLayout(drop)
        box.setContentsMargins(20,20,20,20)
        box.addWidget(label("将游戏资源文件夹拖到这里"))
        box.addWidget(label("支持游戏包根目录、files 或 AssetBundles。"))
        box.addWidget(button("浏览文件夹…",self.browse_source),0,Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(drop)
        row = QHBoxLayout()
        self.source = QLineEdit(placeholderText="或粘贴本地目录并识别")
        self.source.returnPressed.connect(self.scan)
        self.source.textEdited.connect(self.invalidate)
        row.addWidget(self.source,1)
        row.addWidget(button("识别",self.scan))
        layout.addLayout(row)
        self.info = label(IMPORT_EXAMPLE,"hint")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        layout.addWidget(label(
            "选到 AssetBundles 即可，工具会自动读取以下子目录。\n"
            "painting　提供图层贴图、网格和布局，用来拼合人物与背景。\n"
            "paintingface　提供表情贴图，用来导出表情差分；部分立绘也用它补全主图的默认脸。\n"
            "squareicon　提供选择列表中的方形头像，仅用于预览。\n"
            "spinepainting、live2d　提供动态立绘和模型的原始素材。",
            "folderGuide",
        ))
        layout.addStretch()

    def mode_page(self, layout):
        self.asset_catalogs = {}
        self.asset_kind = "painting"
        self.mode = QButtonGroup(self)
        modes = QHBoxLayout()
        modes.setSpacing(12)
        for index, text in enumerate(("还原指定立绘", "还原全部立绘", "检查素材")):
            option = QRadioButton(text)
            self.mode.addButton(option, index)
            modes.addWidget(option)
        self.mode.button(0).setChecked(True)
        modes.addStretch()
        layout.addLayout(modes)
        self.check_all = QCheckBox("检查全部立绘")
        layout.addWidget(self.check_all)
        self.search = QLineEdit(placeholderText="按资源名搜索，例如 nabulesi_3")
        self.search.textChanged.connect(self.filter_names)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.thumbnails = Thumbnails(self)
        self.list.setUniformItemSizes(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self.check_item)
        layout.addWidget(self.list,1)
        row = QHBoxLayout()
        self.visible_btn = button("勾选筛选结果",lambda: self.bulk(True))
        self.clear_btn = button("清空选择",lambda: self.bulk(False))
        row.addWidget(self.visible_btn)
        row.addWidget(self.clear_btn)
        row.addStretch()
        layout.addLayout(row)
        self.count = label(role="hint")
        layout.addWidget(self.count)
        self.check_all.toggled.connect(self.refresh)
        self.mode.buttonClicked.connect(self.refresh)

    def output_page(self,layout):
        self.summary = label()
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        self.out = QLineEdit(placeholderText="输出目录")
        self.out.textChanged.connect(self.refresh)
        row.addWidget(self.out,1)
        row.addWidget(button("浏览…",self.browse_output))
        layout.addWidget(label("保存位置"))
        layout.addLayout(row)
        self.format = QComboBox()
        self.format.addItems(["主图＋全部表情","仅主图"])
        self.format.currentIndexChanged.connect(self.refresh)
        layout.addWidget(self.format)
        self.layers = QCheckBox("同时导出各图层 PNG")
        self.layers.toggled.connect(self.refresh)
        layout.addWidget(self.layers)
        self.example = label(role="hint")
        layout.addWidget(self.example)
        layout.addStretch()

    def run_page(self,layout):
        self.phase = label("等待开始")
        layout.addWidget(self.phase)
        self.bar = QProgressBar()
        self.bar.setRange(0,1000)
        self.bar.setTextVisible(False)
        self.progress_anim = QPropertyAnimation(self.bar,b"value",self)
        self.progress_anim.setDuration(150)
        layout.addWidget(self.bar)
        self.stats = label(role="hint")
        layout.addWidget(self.stats)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["立绘 / 问题详情","状态","阶段"])
        self.tree.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree,1)
        row = QHBoxLayout()
        self.retry = button("重试失败项",self.retry_failed)
        self.open_btn = button("打开输出目录",self.open_output)
        self.cancel = button("取消任务",self.cancel_task)
        for b in (self.cancel,self.retry,self.open_btn):
            row.addWidget(b)
        row.addStretch()
        layout.addLayout(row)

    def page(self, index):
        self.stack.setCurrentIndex(index)
        self.title.setText("选择要导出的素材" if index==1 and self.asset_kind!="painting" else TITLES[index])
        for i,entry in enumerate(self.steps):
            entry.setProperty("active", i == index)
            entry.style().unpolish(entry)
            entry.style().polish(entry)
        if self.motion:
            self.fade_anim.stop()
            self.fade_anim.setStartValue(.65)
            self.fade_anim.setEndValue(1.)
            self.fade_anim.start()
        self.refresh()

    def all_names(self):
        return self.mode.checkedId()==1 or (self.mode.checkedId()==2 and self.check_all.isChecked())

    def refresh(self,*_):
        if not hasattr(self,"next"):
            return
        step = self.stack.currentIndex()
        busy = self.worker is not None
        for index, entry in enumerate(self.steps):
            entry.setEnabled(not busy and index < step)
        self.home_button.setEnabled(not busy)
        for option in self.mode.buttons():
            option.setVisible(self.asset_kind=="painting")
        self.hide_hx.setEnabled(not busy)
        check = self.mode.checkedId()==2
        all_names = self.all_names()
        scope = self.target_names()
        count = len(scope)
        self.check_all.setVisible(check)
        for w in (self.search,self.list,self.visible_btn,self.clear_btn):
            w.setVisible(not all_names)
        self.count.setText(f"本次处理 {count} 套  ·  资源库共 {len(self.names)} 套")
        operation = self.mode.checkedButton().text() if self.asset_kind=="painting" else self.section_name.text()
        self.summary.setText(f"{operation}  ·  {count} 套")
        self.format.setVisible(not check and self.asset_kind=="painting")
        self.layers.setVisible(not check and self.asset_kind=="painting")
        if step==2:
            name = scope[0] if scope else "立绘名"
            names = [f"{name}.png"]
            if self.format.currentIndex()==0:
                names.append(f"{name}_exp_1.png …（有表情时）")
            if self.layers.isChecked():
                names.append(f"{name}__layer__图层名.png")
            self.example.setText("报告文件：素材检查报告.txt" if check else
                "文件示例\n"+"\n".join(names)+"\n\n重新导出会刷新该立绘的主图、旧表情和分层文件。其他文件保留。")
            if self.asset_kind != "painting":
                info = "骨骼、图集描述、PNG 贴图" if self.asset_kind=="spine" else "MOC3 模型、PNG 贴图、物理配置、原始 AnimationClip、motion3.json 和 model3.json"
                self.example.setText(f"{info}。清单记录每个文件的来源。\n输出：{dynamic_assets.SOURCES[self.asset_kind]}/{name}/；重复导出另存新目录。")
        self.next.setText(("下一步","下一步","开始检查" if check else ("开始导出" if self.asset_kind!="painting" else "开始还原"),"返回设置")[step])
        self.next.setEnabled(not busy and [bool(self.root),count>0,bool(self.out.text().strip()) and count>0,True][step])
        self.back.setVisible(step in (1,2))
        self.back.setEnabled(not busy)
        self.cancel.setEnabled(busy and not self.worker.stop.is_set() if busy else False)
        self.retry.setEnabled(not busy and any(r.status=="失败" and self.allowed(r.name) for r in self.results.values()))
        self.open_btn.setEnabled(not busy and self.last_job is not None and self.last_job.output.is_dir())
        self.note.setText(["识别成功后即可选择处理范围。","可跨搜索结果勾选，选择会保留。","确认保存位置后开始执行。","展开问题条目可查看具体原因。"][step])

    def previous(self):
        self.page(max(0,self.stack.currentIndex()-1))

    def advance(self):
        i = self.stack.currentIndex()
        if i==2:
            names = self.target_names()
            if names:
                self.start(Job(self.root,Path(self.out.text().strip()).resolve(),names,
                               self.mode.checkedId()==2,self.format.currentIndex()==0,self.layers.isChecked(),asset_kind=self.asset_kind))
        else:
            self.page(2 if i==3 else i+1)

    def invalidate(self):
        self.asset_catalogs.clear()
        self.root = None
        self.catalog = ()
        self.names = ()
        self.selected.clear()
        self.thumbnails.reset()
        self.list.clear()
        self.info.setText("请识别新的资源目录。")
        self.refresh()

    def browse_source(self):
        if self.worker:
            return
        path = QFileDialog.getExistingDirectory(self,"选择游戏资源目录")
        if path:
            self.source.setText(path)
            self.scan()

    def browse_output(self):
        path = QFileDialog.getExistingDirectory(self,"选择保存位置",self.out.text())
        if path:
            self.out.setText(path)

    def worker_start(self,action,finished):
        worker = Worker(action,self)
        self.worker = worker
        worker.update.connect(self.on_event)
        worker.finished.connect(finished)
        worker.start()
        self.refresh()

    def scan(self):
        if self.worker:
            return
        text = self.source.text().strip().strip('"')
        self.invalidate()
        self.info.setText("正在识别资源目录…")
        self.stack.currentWidget().setEnabled(False)
        def action(_, stopped):
            root = locate(text)
            catalogs = {k:dynamic_assets.catalog(root,k) for k in dynamic_assets.SOURCES}
            from rendering import find_paintings
            catalogs["painting"] = tuple(find_paintings(str(root/"painting")))
            return root,catalogs
        self.worker_start(action,self.scan_done)

    def take_worker(self):
        w = self.worker
        self.worker = None
        w.deleteLater()
        return w

    def scan_done(self):
        w = self.take_worker()
        self.stack.currentWidget().setEnabled(True)
        if w.error:
            self.info.setText(w.error)
        else:
            self.root,self.asset_catalogs = w.value
            self.catalog = self.asset_catalogs[self.asset_kind]
            self.apply_scope()
            if not self.out.text():
                self.out.setText(str(self.root.parent/"还原输出"))
            self.filter_names()
            if self.auto_open:
                self.auto_open = False
                self.page(1)
        self.refresh()
        if self.close_pending:
            self.close()

    def allowed(self, name):
        return not (self.hide_hx.isChecked() and name.casefold().endswith("_hx"))

    def target_names(self):
        if self.all_names():
            return self.names
        return tuple(sorted(n for n in self.selected if self.allowed(n)))

    def apply_scope(self):
        self.names = tuple(n for n in self.catalog if self.allowed(n))
        if self.root:
            face = "已找到表情目录" if (self.root/"paintingface").is_dir() else "缺少表情目录"
            self.info.setText(f"{self.root}\n可用 {len(self.names)} 套 · 隐藏 {len(self.catalog)-len(self.names)} 套 · {face}")

    def hx_changed(self, enabled):
        self.preferences.setValue("hide_hx", enabled)
        self.apply_scope()
        self.filter_names()
        if self.last_job:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                item.setHidden(not self.allowed(item.text(0)))
            self.update_stats()
            if not self.worker:
                self.phase.setText("任务已结束 · 当前筛选结果")
        self.refresh()

    def update_stats(self):
        counts = Counter(r.status for r in self.results.values() if self.allowed(r.name))
        self.stats.setText("     ".join(f"{k} {counts[k]}" for k in ("成功","部分完成","失败","取消")))

    def filter_names(self,*_):
        key = self.search.text().strip().casefold()
        self.list.blockSignals(True)
        self.thumbnails.reset()
        self.list.clear()
        for name in self.names:
            if key in name.casefold():
                item = QListWidgetItem(name)
                item.setIcon(self.thumbnails.blank)
                item.setSizeHint(self.thumbnails.size.grownBy(QMargins(0,4,0,4)))
                item.setCheckState(Qt.CheckState.Checked if name in self.selected else Qt.CheckState.Unchecked)
                self.list.addItem(item)
        self.list.blockSignals(False)

    def check_item(self,item):
        if item.checkState()==Qt.CheckState.Checked:
            self.selected.add(item.text())
        else:
            self.selected.discard(item.text())
        self.refresh()

    def bulk(self,yes):
        if yes:
            self.selected.update(self.list.item(i).text() for i in range(self.list.count()))
        else:
            self.selected.clear()
        self.filter_names()
        self.refresh()

    def start(self,job,retry=False):
        if self.worker:
            return
        self.last_job = job
        if not retry:
            self.results.clear()
            self.tree.clear()
        else:
            replaced = set(job.names)
            for i in reversed(range(self.tree.topLevelItemCount())):
                if self.tree.topLevelItem(i).text(0) in replaced:
                    self.tree.takeTopLevelItem(i)
        self.bar.setValue(0)
        self.phase.setText("正在准备任务…")
        self.stats.setText(f"本轮已处理 0 / {len(job.names)}")
        self.page(3)
        self.worker_start(lambda notify,stopped: execute(job,notify,stopped),self.finished)

    def on_event(self,event):
        if not self.worker.stop.is_set():
            self.phase.setText(f"{event.name}  ·  {event.stage}")
        if event.result:
            r=event.result
            self.results[r.name]=r
            if r.status!="取消" or r.stage!="尚未开始":
                item=QTreeWidgetItem([r.name,r.status,r.stage])
                self.tree.addTopLevelItem(item)
                if r.detail:
                    child=QTreeWidgetItem([r.detail])
                    item.addChild(child)
                    child.setFirstColumnSpanned(True)
                    child.setToolTip(0,r.detail)
            total=len(self.last_job.names)
            self.stats.setText(f"本轮已处理 {event.done} / {total}")
            target=round(1000*event.done/total)
            if self.motion:
                self.progress_anim.stop()
                self.progress_anim.setStartValue(self.bar.value())
                self.progress_anim.setEndValue(target)
                self.progress_anim.start()
            else:
                self.bar.setValue(target)

    def finished(self):
        w=self.take_worker()
        counts=Counter(r.status for r in self.results.values())
        self.phase.setText(("任务失败 · "+w.error) if w.error else
            "已取消" if w.stop.is_set() or counts["取消"] else
            "处理结束，有问题需要查看" if counts["失败"] or counts["部分完成"] else "处理完成")
        self.update_stats()
        self.refresh()
        if self.close_pending:
            self.close()

    def retry_failed(self):
        from dataclasses import replace
        names=tuple(n for n,r in self.results.items() if r.status=="失败" and self.allowed(n))
        if names:
            self.start(replace(self.last_job, names=names, report_name="素材检查重试报告.txt"), retry=True)

    def open_output(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_job.output)))

    def cancel_task(self):
        if self.worker:
            self.worker.stop.set()
            self.phase.setText("正在取消…")
            self.refresh()

    def dragEnterEvent(self,event):
        urls=event.mimeData().urls()
        if self.screens.currentIndex()==1 and not self.worker and len(urls)==1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_dir():
            event.acceptProposedAction()

    def dropEvent(self,event):
        if not self.worker:
            self.page(0)
            self.source.setText(event.mimeData().urls()[0].toLocalFile())
            self.scan()
            event.acceptProposedAction()

    def closeEvent(self,event):
        if self.worker:
            self.close_pending=True
            self.cancel_task()
            event.ignore()
        else:
            self.thumbnails.close()
            event.accept()


def gui():
    app=QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyleSheet(STYLE)
    window=Window()
    window.show()
    return app.exec()
