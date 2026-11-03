import re
from pathlib import Path

from .cleaning import clean_text


def _element(text, metadata, element_type="paragraph"):
    text = clean_text(text)
    if not text:
        return None
    item = dict(metadata)
    item["element_type"] = element_type
    return {"text": text, "metadata": item}


def parse_ppt(path: Path):
    from pptx import Presentation

    result = []
    prs = Presentation(str(path))
    for page, slide in enumerate(prs.slides, 1):
        parts = []
        title = ""
        for shape in slide.shapes:
            if not hasattr(shape, "text") or not shape.text.strip():
                continue
            value = shape.text.strip()
            if not title and getattr(shape, "is_placeholder", False):
                title = value
            parts.append(value)
        item = _element("\n".join(parts) or f"第{page}页未提取到可检索文本。", {
            "source_type": "ppt",
            "source_file": path.name,
            "page": page,
            "section": title or f"第{page}页",
            "heading_path": [title] if title else [f"第{page}页"],
            "video_start": None,
            "video_end": None,
        })
        if item:
            result.append(item)
    return result


def parse_pdf(path: Path):
    import fitz

    result = []
    document = fitz.open(str(path))
    for page_number, page in enumerate(document, 1):
        item = _element(page.get_text("text"), {
            "source_type": "pdf",
            "source_file": path.name,
            "page": page_number,
            "section": f"第{page_number}页",
            "heading_path": [f"第{page_number}页"],
            "video_start": None,
            "video_end": None,
        })
        if item:
            result.append(item)
    return result


def parse_docx(path: Path):
    from docx import Document

    result = []
    document = Document(str(path))
    section = ""
    paragraphs = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = paragraph.style.name.lower() if paragraph.style else ""
        if "heading" in style:
            if paragraphs:
                item = _element("\n".join(paragraphs), _doc_metadata(path, section))
                if item:
                    result.append(item)
                paragraphs = []
            section = text
        else:
            paragraphs.append(text)
    if paragraphs:
        item = _element("\n".join(paragraphs), _doc_metadata(path, section))
        if item:
            result.append(item)
    return result


def _doc_metadata(path, section):
    return {
        "source_type": "docx",
        "source_file": path.name,
        "page": None,
        "section": section or "正文",
        "heading_path": [section] if section else ["正文"],
        "video_start": None,
        "video_end": None,
    }


def _parse_timestamp(value: str) -> float:
    value = value.replace(",", ".")
    parts = value.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    return int(parts[0]) * 60 + float(parts[1])


def parse_subtitle(path: Path):
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    blocks = re.split(r"\n\s*\n", raw.strip())
    result = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start, end = [part.strip() for part in lines[timing_index].split("-->", 1)]
        text = " ".join(lines[timing_index + 1:])
        item = _element(text, {
            "source_type": "subtitle",
            "source_file": path.name,
            "page": None,
            "section": "视频字幕",
            "heading_path": ["视频字幕"],
            "video_start": _parse_timestamp(start),
            "video_end": _parse_timestamp(end),
        })
        if item:
            result.append(item)
    return result


def parse_text(path: Path):
    raw = path.read_text(encoding="utf-8-sig", errors="ignore")
    sections = []
    current_heading = "正文"
    buffer = []
    for line in raw.splitlines():
        if re.match(r"^\s{0,3}#{1,6}\s+", line):
            if buffer:
                item = _element("\n".join(buffer), {
                    "source_type": "markdown" if path.suffix.lower() == ".md" else "text",
                    "source_file": path.name,
                    "page": None,
                    "section": current_heading,
                    "heading_path": [current_heading],
                    "video_start": None,
                    "video_end": None,
                })
                if item:
                    sections.append(item)
                buffer = []
            current_heading = re.sub(r"^\s{0,3}#{1,6}\s+", "", line).strip()
        else:
            buffer.append(line)
    if buffer:
        item = _element("\n".join(buffer), {
            "source_type": "markdown" if path.suffix.lower() == ".md" else "text",
            "source_file": path.name,
            "page": None,
            "section": current_heading,
            "heading_path": [current_heading],
            "video_start": None,
            "video_end": None,
        })
        if item:
            sections.append(item)
    return sections


def parse_document(path: Path):
    """RAG 的统一解析入口。

    RAG 知识库只允许 PDF/文本/字幕类资料通过此入口；PPT/DOC/旧 .ppt/.doc
    等走单独的演示库或其他模块调用，不要进 RAG。即使上层白名单被绕过，
    这里仍以扩展名为最终拦截线。
    """
    suffix = path.suffix.lower()
    # 兜底拦截：无论 SUPPORTED 集合怎么被改，PPT/DOC 一律拒绝入 RAG。
    if suffix in {".ppt", ".pptx", ".doc", ".docx"}:
        raise ValueError(
            f"RAG 知识库只接收 PDF/文本/字幕类资料，{suffix} 请走 PPT 演示库或单独模块处理"
        )
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix in {".srt", ".vtt"}:
        return parse_subtitle(path)
    if suffix in {".md", ".txt"}:
        return parse_text(path)
    raise ValueError(f"不支持的资料格式: {suffix}")
