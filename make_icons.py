#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 APK 图标(512) 与启动图(512)。

依赖 Pillow:  pip install pillow
字体用 assets/NotoSansSC-Regular.otf (SIL OFL，已随包内置)
产物: assets/icon.png, assets/presplash.png
"""
import os

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT = os.path.join(HERE, "assets", "NotoSansSC-Regular.otf")

BLUE = (26, 79, 138)       # 与界面主色 #1a4f8a 一致
WHITE = (255, 255, 255)


def _center(d, size, text, font, y, fill):
    bbox = d.textbbox((0, 0), text, font=font)
    d.text(((size - (bbox[2] - bbox[0])) / 2 - bbox[0], y),
           text, font=font, fill=fill)


def make_icon(size=512, name="icon.png"):
    img = Image.new("RGBA", (size, size), BLUE + (255,))
    d = ImageDraw.Draw(img)
    d.rectangle([0, int(size * 0.72), size, size], fill=(14, 46, 84, 255))
    _center(d, size, "MCTC", ImageFont.truetype(FONT, int(size * 0.30)),
            int(size * 0.18), WHITE)
    _center(d, size, "电梯测试", ImageFont.truetype(FONT, int(size * 0.13)),
            int(size * 0.50), WHITE)
    cx = size / 2
    d.polygon([(cx, int(size * 0.66)),
               (cx - size * 0.06, int(size * 0.74)),
               (cx + size * 0.06, int(size * 0.74))], fill=WHITE + (255,))
    p = os.path.join(HERE, "assets", name)
    img.save(p)
    return p


def make_presplash(size=512, name="presplash.png"):
    img = Image.new("RGBA", (size, size), WHITE + (255,))
    d = ImageDraw.Draw(img)
    _center(d, size, "MCTC 电梯测试",
            ImageFont.truetype(FONT, int(size * 0.13)),
            int(size * 0.30), BLUE)
    _center(d, size, "正在启动…",
            ImageFont.truetype(FONT, int(size * 0.07)),
            int(size * 0.50), (100, 100, 100))
    p = os.path.join(HERE, "assets", name)
    img.save(p)
    return p


if __name__ == "__main__":
    print(make_icon())
    print(make_presplash())
