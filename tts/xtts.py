import os
import time
import numpy as np
import resampy
import requests
from typing import Iterator

from utils.logger import logger
from .base_tts import BaseTTS, State, TTSSynthesisError
from registry import register

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

@register("tts", "xtts")
class XTTS(BaseTTS):
    def __init__(self, opt, parent):
        super().__init__(opt,parent)
        # 不在构造时强依赖克隆服务：服务可能晚启动、或本会话先于服务被创建。
        # 真正可用的 speaker 在「合成时」惰性获取（见 _ensure_speaker），
        # 这样服务后起或一度不可用时能自动恢复，而不是永久哑火。
        self.speaker = None
        self._speaker_ref = opt.REF_FILE
        self._speaker_text = opt.REF_TEXT

    def _ensure_speaker(self):
        """惰性获取并缓存一个有效的 speaker。

        失败时（克隆服务未起 / 参考音频缺失 / 克隆未成功）返回空 speaker_id 的字典；
        下次合成会重新尝试——避免「构造时失败一次 → 整个会话再也不出声」。
        """
        if self.speaker and self.speaker.get("speaker_id"):
            return self.speaker
        self.speaker = self.get_speaker(self._speaker_ref, self.opt.TTS_SERVER)
        return self.speaker

    def txt_to_audio(self,msg:tuple[str, dict]):
        text,textevent = msg
        speaker = self._ensure_speaker()
        if not speaker or not speaker.get("speaker_id"):
            # 必须抛错而非静默：base_tts 会据此回退到备用引擎（如 sapi），
            # 否则数字人会「画面在动、却一点声音都没有」，且无任何报错。
            raise TTSSynthesisError(
                "XTTS 参考音色不可用（speaker_id 为空）",
                hint="请确认「声音克隆」服务已启动、参考音频文件存在，且已成功克隆出音色")
        try:
            produced = self.stream_tts(
                self.xtts(
                    text,
                    speaker,
                    "zh-cn", #en args.language,
                    self.opt.TTS_SERVER, #"http://localhost:9000", #args.server_url,
                    "20" #args.stream_chunk_size
                ),
                msg
            )
        except Exception as exc:  # noqa: BLE001
            raise TTSSynthesisError(
                "XTTS 合成请求失败: %s" % exc,
                hint="克隆服务可能尚未就绪，已尝试回退其它引擎")
        if not produced:
            raise TTSSynthesisError(
                "XTTS 未产出任何音频",
                hint="克隆服务可能未就绪或参考音频无效，已尝试回退其它引擎")

    def get_speaker(self,ref_audio,server_url):
        # 参考音色在界面里存的是相对路径（data/voices/xxx.wav），
        # 必须按项目根解析——服务进程的 cwd 未必就是项目根。
        path = ref_audio
        if path and not os.path.isabs(path):
            path = os.path.join(ROOT, path.replace("/", os.sep))
        if not path or not os.path.isfile(path):
            logger.error("xtts 参考音频不存在: %s", path)
            return {"speaker_id": "", "error": "参考音频不存在: %s" % path}
        try:
            with open(path, "rb") as fh:
                files = {"wav_file": (os.path.basename(path), fh)}
                response = requests.post(f"{server_url}/clone_speaker", files=files)
            if response.status_code != 200:
                logger.error("xtts clone_speaker 失败(%s): %s",
                             response.status_code, response.text[:200])
                return {"speaker_id": "", "error": "克隆服务返回 %s" % response.status_code}
            return response.json()
        except Exception as exc:  # noqa: BLE001
            logger.error("xtts 连接克隆服务失败: %r（请先在「素材与设置」启动声音克隆服务）", exc)
            return {"speaker_id": "", "error": repr(exc)}

    def xtts(self,text, speaker, language, server_url, stream_chunk_size) -> Iterator[bytes]:
        start = time.perf_counter()
        speaker = dict(speaker)  # 不要污染缓存的 speaker 字典
        speaker["text"] = text
        speaker["language"] = language
        speaker["stream_chunk_size"] = stream_chunk_size  # you can reduce it to get faster response, but degrade quality
        try:
            res = requests.post(
                f"{server_url}/tts_stream",
                json=speaker,
                stream=True,
            )
        except Exception as e:
            # 连接失败必须上抛——让 txt_to_audio 转成 TTSSynthesisError 触发回退
            raise RuntimeError("连接克隆服务失败: %s" % e) from e
        end = time.perf_counter()
        logger.info(f"xtts Time to make POST: {end-start}s")

        if res.status_code != 200:
            raise RuntimeError("tts_stream 返回 %s: %s" % (res.status_code, res.text[:200]))

        first = True
        for chunk in res.iter_content(chunk_size=None): #24K*20ms*2
            if first:
                end = time.perf_counter()
                logger.info(f"xts Time to first chunk: {end-start}s")
                first = False
            if chunk:
                yield chunk

    def stream_tts(self,audio_stream,msg:tuple[str, dict]):
        text,textevent = msg
        first = True
        last_stream = np.array([],dtype=np.float32)
        produced = False
        for chunk in audio_stream:
            if chunk is not None and len(chunk)>0:
                produced = True
                stream = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32767
                stream = resampy.resample(x=stream, sr_orig=24000, sr_new=self.sample_rate)
                stream = np.concatenate((last_stream,stream))
                #byte_stream=BytesIO(buffer)
                #stream = self.__create_bytes_stream(byte_stream)
                streamlen = stream.shape[0]
                idx=0
                while streamlen >= self.chunk:
                    eventpoint={}
                    if first:
                        eventpoint={'status':'start','text':text}
                        first = False
                    eventpoint.update(**textevent)
                    self.parent.put_audio_frame(stream[idx:idx+self.chunk],eventpoint)
                    streamlen -= self.chunk
                    idx += self.chunk
                last_stream = stream[idx:] #get the remain stream
        eventpoint={'status':'end','text':text}
        eventpoint.update(**textevent)
        self.parent.put_audio_frame(np.zeros(self.chunk,np.float32),eventpoint)
        return produced
