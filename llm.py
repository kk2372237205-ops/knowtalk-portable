import time
import os
import re
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from avatars.base_avatar import BaseAvatar
from utils.logger import logger
from utils import material_manager as mat_mgr
from utils.settings_store import DEFAULT_SYSTEM_PROMPT, get_store


def clean_llm_text(text):
    """去除适合阅读但不适合展示和朗读的 Markdown 标记。"""
    text = re.sub(r'```(?:\w+)?\s*', '', text)
    text = text.replace('```', '')
    text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'^\s{0,3}#{1,6}\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'[`*_~]', '', text)
    return text


def take_complete_sentences(buffer, force=False):
    """优先按完整句子切分，避免在逗号或冒号处截断。"""
    segments = []
    start = 0
    for index, char in enumerate(buffer):
        is_sentence_end = char in '。！？!?；;\n'
        is_period_end = char == '.' and (index + 1 == len(buffer) or buffer[index + 1].isspace())
        if is_sentence_end or is_period_end:
            segments.append(buffer[start:index + 1].strip())
            start = index + 1

    remainder = buffer[start:]
    if not force and len(remainder) > 120:
        # 仅在句子过长时兜底，且尽量选择靠后的逗号，不打断短句。
        cut_positions = [i for i, char in enumerate(remainder) if char in '，,、']
        if cut_positions:
            cut = max((i for i in cut_positions if i >= 70), default=None)
            if cut is not None:
                segments.append(remainder[:cut + 1].strip())
                remainder = remainder[cut + 1:]

    if force and remainder.strip():
        segments.append(remainder.strip())
        remainder = ''
    return [segment for segment in segments if segment], remainder

def llm_response(message,avatar_session:'BaseAvatar',datainfo:dict={}):
    avatar_session.llm_text = ''
    avatar_session.llm_done = False
    # 供桌面端「对话浮层」读取：显示这一轮问的是什么，看不到内部提示词。
    avatar_session.last_question = message
    avatar_session.llm_started_at = time.time()
    try:
        opt = avatar_session.opt
        sessionid = getattr(avatar_session, 'sessionid', '0')
        start = time.perf_counter()
        from openai import OpenAI
        # 运行期设置 > DEEPSEEK_* 环境变量 > 内置默认值（见 utils/settings_store.py）
        store = get_store()
        api_key = store.get_str("llm.api_key")
        if not api_key:
            logger.error("未配置 LLM API Key（设置页或环境变量 DEEPSEEK_API_KEY），Chat LLM 模式不可用")
            return
        client = OpenAI(
            api_key=api_key,
            base_url=store.get_str("llm.base_url"),
        )
        end = time.perf_counter()
        logger.info(f"llm Time init: {end-start}s,{message}")

        # ─── 多轮对话记忆 ──────────────────────────────────────────────
        # 每个 session 维护最近 N 轮（user/assistant）历史，让数字人能接住追问。
        history = getattr(avatar_session, 'chat_history', None)
        if history is None:
            history = []
            avatar_session.chat_history = history
        history_max_rounds = int(os.getenv("CHAT_HISTORY_ROUNDS", "10"))
        history.append({'role': 'user', 'content': message})
        # 截断到最近 N 轮（2*N 条消息）
        history = history[-(history_max_rounds * 2):]
        avatar_session.chat_history = history

        system_prompt = store.get_str("llm.system_prompt") or DEFAULT_SYSTEM_PROMPT
        messages = [
            {'role': 'system', 'content': system_prompt},
        ] + history

        completion = client.chat.completions.create(
            model=store.get_str("llm.model"),
            messages=messages,
            stream=True,
            stream_options={"include_usage": True}
        )
        result = ""
        first = True
        for chunk in completion:
            if len(chunk.choices)>0:
                if first:
                    end = time.perf_counter()
                    logger.info(f"llm Time to first chunk: {end-start}s")
                    first = False
                msg = chunk.choices[0].delta.content
                if msg is None:
                    continue
                msg = clean_llm_text(msg)
                avatar_session.llm_text += msg
                result += msg
                segments, result = take_complete_sentences(result)
                for segment in segments:
                    if datainfo.get('_speech_request_id') != avatar_session.speech_request_id:
                        return
                    logger.info(segment)
                    avatar_session.put_msg_txt(segment, datainfo)
                    # 检查关键词匹配，切换幻灯片
                    mat_mgr.check_and_update_slide(sessionid, segment)
        end = time.perf_counter()
        logger.info(f"llm Time to last chunk: {end-start}s")
        segments, result = take_complete_sentences(result, force=True)
        for segment in segments:
            if datainfo.get('_speech_request_id') != avatar_session.speech_request_id:
                return
            avatar_session.put_msg_txt(segment, datainfo)
            mat_mgr.check_and_update_slide(sessionid, segment)

        # 把助手回答写入记忆（仅当回答未被新请求打断时）。
        # 注意：上面 take_complete_sentences(force=True) 会把 result 排空为空串，
        # 所以回答全文只能用 llm_text —— 它在整个流式过程中被逐片累加。
        full_reply = avatar_session.llm_text.strip()
        if full_reply and datainfo.get('_speech_request_id') == avatar_session.speech_request_id:
            avatar_session.chat_history.append({'role': 'assistant', 'content': full_reply})
            avatar_session.chat_history = avatar_session.chat_history[-(history_max_rounds * 2):]

    except Exception as e:
        logger.exception('llm exception:')
        return   
    finally:
        avatar_session.llm_done = True
