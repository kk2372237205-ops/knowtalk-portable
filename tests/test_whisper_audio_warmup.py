# -*- coding: utf-8 -*-
"""MuseTalk 首批音频不得因特征预热而丢失。"""

from types import SimpleNamespace
import unittest

import numpy as np

from avatars.audio_features.whisper import WhisperASR


class _UnusedAudioProcessor:
    def audio2feat(self, _inputs):
        raise AssertionError("首批上下文不足时不应提取特征")


class WhisperWarmupAudioTestCase(unittest.TestCase):
    def test_first_batch_is_preserved_while_feature_context_warms_up(self):
        opt = SimpleNamespace(fps=20, batch_size=1, l=4, r=4)
        asr = WhisperASR(opt, parent=None, audio_processor=_UnusedAudioProcessor())
        asr.put_audio_frame(np.zeros(asr.chunk, dtype=np.float32), {"status": "start"})
        asr.put_audio_frame(np.zeros(asr.chunk, dtype=np.float32), {"status": "end"})

        asr.run_step()

        self.assertEqual(asr.output_queue.qsize(), 2)
        first = asr.output_queue.get_nowait()
        last = asr.output_queue.get_nowait()
        self.assertEqual(first.userdata["status"], "start")
        self.assertEqual(last.userdata["status"], "end")


if __name__ == "__main__":
    unittest.main()
