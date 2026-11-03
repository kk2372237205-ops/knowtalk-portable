# -*- coding: utf-8 -*-
"""utils.media_type 的判定逻辑。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils import media_type


class MediaTypeTest(unittest.TestCase):
    def test_image_extensions(self):
        for name in ("a.png", "b.JPG", "c.jpeg", "d.webp", "e.bmp", "f.gif"):
            self.assertTrue(media_type.is_image_file(name), name)

    def test_video_extensions(self):
        for name in ("a.mp4", "b.mkv", "c.avi", "d.mov", "e.webm", "f.flv"):
            self.assertTrue(media_type.is_video_file(name), name)

    def test_non_media_rejected(self):
        for name in ("a.pdf", "b.pptx", "c.docx", "d.txt", "e"):
            self.assertFalse(media_type.is_image_file(name), name)
            self.assertFalse(media_type.is_video_file(name), name)
            self.assertFalse(media_type.is_media_file(name), name)

    def test_is_media_file_union(self):
        self.assertTrue(media_type.is_media_file("x.mp4"))
        self.assertTrue(media_type.is_media_file("x.png"))


if __name__ == "__main__":
    unittest.main()
