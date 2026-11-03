import re


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_markdown(text: str) -> str:
    text = re.sub(r"```(?:\w+)?\s*", "", text)
    text = text.replace("```", "")
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[`*_~]", "", text)
    return clean_text(text)


def speech_text(text: str) -> str:
    """去掉任意位置的'来源：xxx'元数据，再清 Markdown，适合 TTS 朗读。

    旧版只匹配行首的 '来源：'，遗漏了模型把来源接在正文末尾的情况
    （"Python是… 来源：项目2...pptx 第8页"），会导致数字人把元数据念出来。
    这里采用"从第一个出现的位置开始整段截断"——保留正文，丢弃来源元数据。
    """
    end = len(text)
    for marker in ("来源：", "来源:", "参考来源：", "参考来源:"):
        index = text.find(marker)
        if index >= 0 and index < end:
            end = index
    text = text[:end]
    text = re.sub(r"^\s*来源\s*[:：].*$", "", text, flags=re.MULTILINE)
    return clean_markdown(text)
