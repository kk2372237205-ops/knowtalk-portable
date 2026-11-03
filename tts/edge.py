import time
import asyncio
import os
import numpy as np
import resampy
import soundfile as sf
import edge_tts
from io import BytesIO

from utils.logger import logger
from .base_tts import BaseTTS, State, TTSSynthesisError
from registry import register

# 该引擎依赖微软在线服务。若服务端收紧客户端版本校验（实测已发生），
# 合成会失败，此时必须抛出 TTSSynthesisError 让上层察觉并触发回退，
# 而不是静默返回让课堂表现为"数字人突然不说话"。
_EDGE_FAILURE_HINT = (
    "EdgeTTS 依赖微软在线语音服务，当前请求被拒绝。"
    "提供离线语音能力的平台默认会回退到离线引擎（sapi），无需额外配置；"
    "也可直接改用离线引擎（--tts sapi），或指定其它备用引擎（--TTS_FALLBACK）"
)

# 已知的 EdgeTTS 语音名称前缀（微软语音名称不包含路径分隔符和文件扩展名）
def _is_edgetts_voice(value: str) -> bool:
    """判断是否为合法的 EdgeTTS 语音名称（非文件路径）"""
    if not value:
        return False
    # 文件路径特征：包含路径分隔符或音频扩展名
    if os.path.sep in value or '/' in value:
        return False
    if value.lower().endswith(('.wav', '.mp3', '.webm', '.ogg', '.m4a', '.flac', '.aac')):
        return False
    return True

@register("tts", "edgetts")
class EdgeTTS(BaseTTS):
    def txt_to_audio(self,msg:tuple[str, dict]):
        text,textevent = msg

        ref_file = textevent.get('tts', {}).get('ref_file', self.opt.REF_FILE)
        # EdgeTTS 只接受微软语音名称（如 zh-CN-YunxiaNeural），不接受文件路径
        if _is_edgetts_voice(ref_file):
            voicename = ref_file
        else:
            voicename = self.opt.REF_FILE if _is_edgetts_voice(self.opt.REF_FILE) else 'zh-CN-YunxiaNeural'
            if ref_file and ref_file != voicename:
                logger.info(f'[TTS] EdgeTTS 不支持文件路径作为音色，使用默认语音: {voicename}')
        tts_rate = self.opt.TTS_RATE if hasattr(self.opt, 'TTS_RATE') else '+0%'
        slide_id = textevent.get('slide_id', '无')

        logger.info(f'[TTS] 开始合成: voice={voicename}, rate={tts_rate}, slide_id={slide_id}, text={text[:80]}...')

        # ✅ 使用独立的 BytesIO 避免与前后 TTS 调用冲突
        # 之前共享 self.input_stream 导致 flush_talk 打断时音频数据混乱
        local_stream = BytesIO()

        t = time.time()
        asyncio.new_event_loop().run_until_complete(self.__main(voicename,text,tts_rate,local_stream))
        logger.info(f'-------edge tts time:{time.time()-t:.4f}s, text_len={len(text)}')
        if local_stream.getbuffer().nbytes<=0: #edgetts err
            logger.error('edgetts 合成没有产出音频（本地缓冲为空）')
            raise TTSSynthesisError(
                'EdgeTTS 合成没有产出音频',
                hint=_EDGE_FAILURE_HINT,
            )
        
        local_stream.seek(0)
        stream = self.__create_bytes_stream(local_stream)
        streamlen = stream.shape[0]
        idx=0
        while streamlen > 0 and self.state==State.RUNNING:
            eventpoint={}
            frame = stream[idx:idx+self.chunk]
            actual_len = frame.shape[0]
            if actual_len < self.chunk:
                frame = np.pad(frame, (0, self.chunk - actual_len))
            streamlen -= actual_len
            if idx==0:
                eventpoint={'status':'start','text':text}
            if streamlen <= 0:
                eventpoint={'status':'end','text':text}
            eventpoint.update(**textevent) #eventpoint={'status':'end','text':text,'msgevent':textevent}
            self.parent.put_audio_frame(frame,eventpoint)
            idx += self.chunk
        # local_stream 在函数结束时自动释放，无需手动清理 

    def __create_bytes_stream(self,byte_stream):
        #byte_stream=BytesIO(buffer)
        stream, sample_rate = sf.read(byte_stream) # [T*sample_rate,] float64
        logger.info(f'[INFO]tts audio stream {sample_rate}: {stream.shape}')
        stream = stream.astype(np.float32)

        if stream.ndim > 1:
            logger.info(f'[WARN] audio has {stream.shape[1]} channels, only use the first.')
            stream = stream[:, 0]
    
        if sample_rate != self.sample_rate and stream.shape[0]>0:
            logger.info(f'[WARN] audio sample rate is {sample_rate}, resampling into {self.sample_rate}.')
            stream = resampy.resample(x=stream, sr_orig=sample_rate, sr_new=self.sample_rate)

        return stream
    
    async def __main(self,voicename: str, text: str, rate: str, output_stream: BytesIO):
        try:
            communicate = edge_tts.Communicate(text, voicename, rate=rate)

            #with open(OUTPUT_FILE, "wb") as file:
            first = True
            async for chunk in communicate.stream():
                if first:
                    first = False
                if chunk["type"] == "audio" and self.state==State.RUNNING:
                    #self.push_audio(chunk["data"])
                    output_stream.write(chunk["data"])
                    #file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    pass
        except Exception as e:
            # 不再吞掉异常：把失败上报为可被上层察觉的 TTSSynthesisError。
            # 上游服务返回 403 时 edge_tts 会在这里抛出握手错误。
            logger.error('edgetts 请求失败: %s', e)
            raise TTSSynthesisError(
                'EdgeTTS 请求失败: %s' % e,
                hint=_EDGE_FAILURE_HINT,
            )
