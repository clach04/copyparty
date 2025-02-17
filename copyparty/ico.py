# coding: utf-8
from __future__ import print_function, unicode_literals

import argparse  # typechk
import colorsys
import hashlib

from .__init__ import PY2
from .th_srv import HAVE_PIL
from .util import BytesIO


class Ico(object):
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args

    def get(self, ext: str, as_thumb: bool, chrome: bool) -> tuple[str, bytes]:
        """placeholder to make thumbnails not break"""

        zb = hashlib.sha1(ext.encode("utf-8")).digest()[2:4]
        if PY2:
            zb = [ord(x) for x in zb]

        c1 = colorsys.hsv_to_rgb(zb[0] / 256.0, 1, 0.3)
        c2 = colorsys.hsv_to_rgb(zb[0] / 256.0, 1, 1)
        ci = [int(x * 255) for x in list(c1) + list(c2)]
        c = "".join(["{:02x}".format(x) for x in ci])

        w = 100
        h = 30
        if not self.args.th_no_crop and as_thumb:
            sw, sh = self.args.th_size.split("x")
            h = int(100 / (float(sw) / float(sh)))
            w = 100

        if chrome and as_thumb:
            # cannot handle more than ~2000 unique SVGs
            if HAVE_PIL:
                # svg: 3s, cache: 6s, this: 8s
                from PIL import Image, ImageDraw

                h = int(64 * h / w)
                w = 64
                img = Image.new("RGB", (w, h), "#" + c[:6])
                pb = ImageDraw.Draw(img)
                tw, th = pb.textsize(ext)
                pb.text(((w - tw) // 2, (h - th) // 2), ext, fill="#" + c[6:])
                img = img.resize((w * 3, h * 3), Image.NEAREST)

                buf = BytesIO()
                img.save(buf, format="PNG", compress_level=1)
                return "image/png", buf.getvalue()

            elif False:
                # 48s, too slow
                import pyvips

                h = int(192 * h / w)
                w = 192
                img = pyvips.Image.text(
                    ext, width=w, height=h, dpi=192, align=pyvips.Align.CENTRE
                )
                img = img.ifthenelse(ci[3:], ci[:3], blend=True)
                # i = i.resize(3, kernel=pyvips.Kernel.NEAREST)
                buf = img.write_to_buffer(".png[compression=1]")
                return "image/png", buf

        svg = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg version="1.1" viewBox="0 0 100 {}" xmlns="http://www.w3.org/2000/svg"><g>
<rect width="100%" height="100%" fill="#{}" />
<text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" xml:space="preserve"
  fill="#{}" font-family="monospace" font-size="14px" style="letter-spacing:.5px">{}</text>
</g></svg>
"""
        svg = svg.format(h, c[:6], c[6:], ext)

        return "image/svg+xml", svg.encode("utf-8")
