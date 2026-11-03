from typing import Any

from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    # 不指定课程时由服务端自动挑（当前只有一门启用课程就直接用这门）。
    # 这里原来是硬编码的 "rail-passenger-traffic"，那门课早已不存在，
    # 导致所有不传 course_id 的调用方（端侧菜单按钮、语音提问）统统拿不到片段。
    course_id: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]+$")
    session_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=20)
    speak: bool = False


class IndexRequest(BaseModel):
    course_id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")


class Citation(BaseModel):
    source_file: str
    source_type: str
    page: int | None = None
    section: str | None = None
    video_start: float | None = None
    video_end: float | None = None
    score: float


class AskData(BaseModel):
    answer: str
    speak_text: str
    citations: list[Citation]
    retrieval: dict[str, Any]
