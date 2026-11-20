from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PySide6.QtCore import QPoint, QSize, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap, QImage, QColor
from PIL import Image
import rendering

ICON_SIZE = 44


def decode(root, name):
    path = root / "squareicon" / name
    if not path.is_file():
        return None
    _, objects = rendering._load_ab(str(path))
    sprites = [obj for obj in objects.values() if obj.type.name == "Sprite"]
    for obj in sprites:
        sprite = obj.read()
        if len(sprites) == 1 or sprite.m_Name.casefold() == name.casefold():
            return sprite.image.convert("RGBA")
    return None


def raster(root, name):
    try:
        image = decode(root, name)
        if image is not None:
            image.thumbnail((ICON_SIZE*2, ICON_SIZE*2),Image.Resampling.LANCZOS)
            return QImage(image.tobytes(),image.width,image.height,image.width*4,QImage.Format.Format_RGBA8888).copy()
    except Exception:
        pass
    return QImage()


class Thumbnails:
    def __init__(self, window):
        self.window = window
        self.size = QSize(ICON_SIZE, ICON_SIZE)
        self.list = window.list
        self.list.setIconSize(self.size)
        empty = QPixmap(self.size)
        empty.fill(QColor("#e7ecf2"))
        self.blank = QIcon(empty)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pending = None
        self.cache = OrderedDict()
        self.active = set()
        self.timer = QTimer(self.list)
        self.timer.timeout.connect(self.tick)
        self.timer.start(55)

    def reset(self):
        self.active.clear()

    def tick(self):
        if self.pending and self.pending[1].done():
            key, future = self.pending
            image = future.result()
            self.cache[key] = self.blank if image.isNull() else QIcon(QPixmap.fromImage(image))
            self.pending = None
            if len(self.cache)>256:
                self.cache.popitem(last=False)
        if not self.list.isVisible() or not self.window.root or not self.list.count():
            return
        first = max(0,self.list.indexAt(QPoint(2,2)).row())
        last = self.list.indexAt(QPoint(2,self.list.viewport().height()-2)).row()
        if last < first:
            last=min(self.list.count()-1,first+2+self.list.viewport().height()//self.size.height())
        visible=set(range(first,last+1))
        self.list.blockSignals(True)
        try:
            for row in self.active-visible:
                item=self.list.item(row)
                if item: item.setIcon(self.blank)
            for row in sorted(visible):
                item=self.list.item(row)
                key=(self.window.root,item.text())
                if key in self.cache:
                    icon=self.cache[key]
                    if item.icon().cacheKey()!=icon.cacheKey():
                        item.setIcon(icon)
                    self.cache.move_to_end(key)
                elif self.pending is None:
                    self.pending=(key,self.pool.submit(raster,*key))
            self.active=visible
        finally:
            self.list.blockSignals(False)

    def close(self):
        self.timer.stop()
        self.pool.shutdown(wait=False,cancel_futures=True)
