# -*- coding: utf-8 -*-
"""KnowTalk 运行期设置：持久化、优先级解析、取值校验与密钥掩码。

取值优先级固定为：**设置文件（界面写入） > 环境变量 > 内置默认值**。
只持久化用户显式写入过的字段，因此从未在界面改过的部署在升级后行为完全不变。

生效时机分三档，由 `effect` 字段表达：
- ``immediate``   下一次调用即生效（LLM 供应商配置、系统提示词）
- ``new_session`` 对此后新建的会话生效，已存在会话不变（TTS、默认形象、口型微调）
- ``restart``     需重启对应进程后生效（主服务启动类参数、RAG 参数）
"""

import json
import os
import re
import tempfile
import threading
from urllib.parse import urlparse

from utils.logger import logger
from utils.platform_support import DEFAULT_TTS_FALLBACK

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTINGS_DIR = os.path.join(ROOT, "data", "settings")
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

EFFECT_IMMEDIATE = "immediate"
EFFECT_NEW_SESSION = "new_session"
EFFECT_RESTART = "restart"

# 与 avatars/base_avatar.py 的 _tts_modules 保持一致。
# 刻意不含 indextts2：该插件缺少 @register 装饰器，选中它必然抛 ValueError
# （见 docs/audit/ 的记录），因此不在界面上提供这个选项。
# sapi 是 Windows 系统语音，完全离线、音质较机械，用作在线引擎不可用时的兜底。
TTS_ENGINES = (
    "edgetts", "sapi", "gpt-sovits", "xtts", "cosyvoice", "fishtts",
    "tencent", "doubao", "azuretts", "qwentts",
)

DEFAULT_SYSTEM_PROMPT = (
    "你是一个知识助手，尽量以简短、口语化的方式输出。不要使用 Markdown，"
    "不要输出星号、井号、反引号、项目符号或其他格式标记，直接输出适合朗读的纯文本。"
    "可以结合上下文对话历史理解用户的追问，但不要复述历史。"
)

AVATAR_ID_PATTERN = r"^[A-Za-z0-9_.-]+$"
TTS_RATE_PATTERN = r"^[+-]\d{1,3}%$"

# 只读的启动类参数：仅展示，写入会被拒绝
READONLY_STARTUP_FIELDS = (
    "model", "transport", "modelres", "batch_size",
    "listenport", "fps", "max_session",
)

RESTART_HINT_MAIN = "需重启主服务后生效"
RESTART_HINT_RAG = "需重启 RAG 服务后生效（可在设置页点“重启 RAG”）"

# 可写字段的声明式定义。dotted key = "<group>.<field>"
FIELD_SPECS = {
    # ── LLM：立即生效 ──────────────────────────────────────────
    "llm.api_key": {
        "type": "secret", "effect": EFFECT_IMMEDIATE, "env": "DEEPSEEK_API_KEY",
        "default": "", "label": "API Key", "help": "对话模型的密钥，任何 OpenAI 兼容端点均可",
    },
    "llm.base_url": {
        "type": "url", "effect": EFFECT_IMMEDIATE, "env": "DEEPSEEK_BASE_URL",
        "default": "https://api.deepseek.com/v1", "label": "接口地址",
        "help": "OpenAI 兼容的 /v1 地址",
    },
    "llm.model": {
        "type": "nonempty", "effect": EFFECT_IMMEDIATE, "env": "DEEPSEEK_MODEL",
        "default": "deepseek-v4-pro", "label": "模型名", "help": "例如 deepseek-v4-pro",
    },
    "llm.system_prompt": {
        "type": "text", "effect": EFFECT_IMMEDIATE, "env": "CHAT_SYSTEM_PROMPT",
        "default": DEFAULT_SYSTEM_PROMPT, "label": "系统提示词（人设）",
        "help": "留空则使用内置默认提示词",
    },
    # ── TTS / 形象 / 口型：对新建会话生效 ───────────────────────
    "tts.engine": {
        "type": "enum", "enum": TTS_ENGINES, "effect": EFFECT_NEW_SESSION,
        "env": "TTS_ENGINE", "default": "edgetts", "label": "TTS 引擎",
        "help": "对新建会话生效。edgetts 依赖微软在线服务，若持续返回 403 请改用 sapi（离线）。",
    },
    # 备用引擎：主引擎合成失败时改用它重试一次。
    # 空串是合法取值（表示不回退），因此候选里显式带上空串，
    # 否则界面上无法把已配置的回退再关掉。
    # 默认值跟着平台走（见 utils/platform_support.py），与 config.py 的
    # --TTS_FALLBACK 默认值同源：默认主引擎 edgetts 已被服务端持续拒绝，
    # 若这里默认仍是空，则"默认配置"恰好等于"必然失败 + 无兜底"。
    "tts.fallback": {
        "type": "enum", "enum": ("",) + TTS_ENGINES, "effect": EFFECT_NEW_SESSION,
        "env": "TTS_FALLBACK", "default": DEFAULT_TTS_FALLBACK, "empty_is_value": True,
        "label": "备用引擎（失败时回退）",
        "help": "主引擎失败时自动改用该引擎重试一次；选「（不回退）」可关闭。"
                "默认值：提供离线语音能力的平台为 sapi，否则不回退。",
    },
    "tts.rate": {
        "type": "regex", "regex": TTS_RATE_PATTERN, "effect": EFFECT_NEW_SESSION,
        "env": "TTS_RATE", "default": "+0%", "label": "语速",
        "help": "形如 -20% / +0% / +20%，对新建会话生效",
    },
    "tts.ref_file": {
        "type": "str", "effect": EFFECT_NEW_SESSION, "env": "TTS_REF_FILE",
        "default": "zh-CN-YunjianNeural", "label": "参考音色",
        "help": "EdgeTTS 的语音名，或声音克隆产生的参考音频路径",
    },
    "tts.ref_text": {
        "type": "str", "effect": EFFECT_NEW_SESSION, "env": "TTS_REF_TEXT",
        "default": "", "label": "参考文本", "help": "声音克隆时对应参考音频的文本",
    },
    "avatar.id": {
        "type": "regex", "regex": AVATAR_ID_PATTERN, "effect": EFFECT_NEW_SESSION,
        "env": "AVATAR_ID", "default": "", "label": "默认数字人形象",
        "help": "留空则沿用启动参数；对新建会话生效",
    },
    # ── 桌面端音频输出：立即生效（桌面端自己读，不进主服务）────────
    # 输出设备是"无声"排查的第二大坑：Windows 在多显示器/接 HDMI 时，
    # 系统默认输出常常是显示器而不是音箱，桌面上看着一切正常就是不响。
    # 留空 = 用系统默认设备。
    "audio.output_device": {
        "type": "str", "effect": EFFECT_IMMEDIATE, "env": "AUDIO_OUTPUT_DEVICE",
        "default": "", "label": "音频输出设备",
        "help": "留空则用系统默认输出设备。多屏/HDMI 场景若无声，请手动选择实际接音箱的设备。",
    },
    # 桌面端播放测试提示音，用于在真机上快速判断"是数字人没出声，还是这台机器根本没声音"
    "audio.test_tone": {
        "type": "bool", "effect": EFFECT_IMMEDIATE, "env": "AUDIO_TEST_TONE",
        "default": True, "label": "启动时播放测试提示音",
        "help": "桌面端连上主服务后播一声短提示音，用于确认本机扬声器通路正常。",
    },
    "mouth.alpha": {
        "type": "float", "min": 0.0, "max": 1.0, "effect": EFFECT_NEW_SESSION,
        "env": "MOUTH_ALPHA", "default": 0.96, "label": "口型幅度",
        "help": "0=不动嘴，1=原始幅度",
    },
    "mouth.offset_x": {
        "type": "float", "min": -1.0, "max": 1.0, "effect": EFFECT_NEW_SESSION,
        "env": "MOUTH_OFFSET_X", "default": 0.15, "label": "嘴部水平微调",
        "help": "负=左移，正=右移",
    },
    "mouth.offset_y": {
        "type": "float", "min": -1.0, "max": 1.0, "effect": EFFECT_NEW_SESSION,
        "env": "MOUTH_OFFSET_Y", "default": -0.15, "label": "嘴部垂直微调",
        "help": "负=上移，正=下移",
    },
    # ── 动作制作（MiniMax H3 图生视频）：立即生效 ───────────────
    # 出片是异步任务、按次计费，所以时长/分辨率都做成可调，默认取最省的一档。
    "minimax.api_key": {
        "type": "secret", "effect": EFFECT_IMMEDIATE, "env": "MINIMAX_API_KEY",
        "default": "", "label": "MiniMax API Key",
        "help": "「制作新动作」用它出片；留空则制作入口不可用。密钥只存在本机设置文件里。",
    },
    "minimax.base_url": {
        "type": "url", "effect": EFFECT_IMMEDIATE, "env": "MINIMAX_BASE_URL",
        "default": "https://api.minimax.cn", "label": "接口地址",
        "help": "国内账号用 https://api.minimax.cn；海外账号用 https://api.minimax.io",
    },
    "minimax.video_model": {
        "type": "nonempty", "effect": EFFECT_IMMEDIATE, "env": "MINIMAX_VIDEO_MODEL",
        "default": "MiniMax-H3", "label": "视频模型",
        "help": "图生视频的模型名，例如 MiniMax-H3",
    },
    "minimax.resolution": {
        "type": "enum", "enum": ("768P", "2K"), "effect": EFFECT_IMMEDIATE,
        "env": "MINIMAX_RESOLUTION", "default": "768P", "label": "出片分辨率",
        "help": "768P 更快更省（切帧后已足够），2K 更清晰但更慢更贵",
    },
    "minimax.duration": {
        "type": "int", "min": 4, "max": 15, "effect": EFFECT_IMMEDIATE,
        "env": "MINIMAX_DURATION", "default": 5, "label": "出片时长（秒）",
        "help": "4–15 的整数。动作类 5 秒就够，越长越贵",
    },
    # ── RAG：重启 RAG 服务后生效 ────────────────────────────────
    "rag.RAG_LLM_MODEL": {
        "type": "nonempty", "effect": EFFECT_RESTART, "env": "RAG_LLM_MODEL",
        "default": "qwen3:8b", "label": "生成模型（Ollama）", "help": RESTART_HINT_RAG,
    },
    "rag.RAG_EMBED_MODEL": {
        "type": "nonempty", "effect": EFFECT_RESTART, "env": "RAG_EMBED_MODEL",
        "default": "qwen3-embedding:4b", "label": "向量化模型（Ollama）", "help": RESTART_HINT_RAG,
    },
    "rag.RAG_TOKENIZER_NAME": {
        "type": "nonempty", "effect": EFFECT_RESTART, "env": "RAG_TOKENIZER_NAME",
        "default": "Qwen/Qwen3-Embedding-4B", "label": "分词器路径或仓库名",
        "help": "可直接填本地目录，例如 D:/models/Qwen3-Embedding-4B",
    },
    "rag.RAG_RERANKER_NAME": {
        "type": "nonempty", "effect": EFFECT_RESTART, "env": "RAG_RERANKER_NAME",
        "default": "BAAI/bge-reranker-base", "label": "重排模型路径或仓库名",
        "help": "可直接填本地目录，例如 D:/models/bge-reranker-base",
    },
    "rag.RAG_CHUNK_SIZE": {
        "type": "int", "min": 64, "max": 4096, "effect": EFFECT_RESTART,
        "env": "RAG_CHUNK_SIZE", "default": 420, "label": "分片长度（token）",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_CHUNK_OVERLAP": {
        "type": "int", "min": 0, "max": 1024, "effect": EFFECT_RESTART,
        "env": "RAG_CHUNK_OVERLAP", "default": 55, "label": "分片重叠（token）",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_VECTOR_TOP_K": {
        "type": "int", "min": 1, "max": 100, "effect": EFFECT_RESTART,
        "env": "RAG_VECTOR_TOP_K", "default": 10, "label": "向量召回条数",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_BM25_TOP_K": {
        "type": "int", "min": 1, "max": 100, "effect": EFFECT_RESTART,
        "env": "RAG_BM25_TOP_K", "default": 10, "label": "BM25 召回条数",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_RERANK_TOP_K": {
        "type": "int", "min": 1, "max": 50, "effect": EFFECT_RESTART,
        "env": "RAG_RERANK_TOP_K", "default": 3, "label": "重排保留条数",
        "help": RESTART_HINT_RAG + "（注意：/rag/ask 当前固定用 3）",
    },
    "rag.RAG_VECTOR_THRESHOLD": {
        "type": "float", "min": 0.0, "max": 1.0, "effect": EFFECT_RESTART,
        "env": "RAG_VECTOR_THRESHOLD", "default": 0.55, "label": "向量相似度阈值",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_REQUEST_TIMEOUT": {
        "type": "float", "min": 1.0, "max": 3600.0, "effect": EFFECT_RESTART,
        "env": "RAG_REQUEST_TIMEOUT", "default": 120, "label": "RAG 请求超时（秒）",
        "help": RESTART_HINT_RAG,
    },
    "rag.OLLAMA_BASE_URL": {
        "type": "url", "effect": EFFECT_RESTART, "env": "OLLAMA_BASE_URL",
        "default": "http://127.0.0.1:11434", "label": "Ollama 地址",
        "help": RESTART_HINT_RAG,
    },
    "rag.RAG_BASE_URL": {
        "type": "url", "effect": EFFECT_RESTART, "env": "RAG_BASE_URL",
        "default": "http://127.0.0.1:8090", "label": "RAG 服务地址",
        "help": "指向非本机地址时主服务不会代为重启 RAG",
    },
    "rag.RAG_ENABLED": {
        "type": "bool", "effect": EFFECT_RESTART, "env": "RAG_ENABLED",
        "default": True, "label": "启用 RAG", "help": "关闭后主服务不再自动拉起 RAG 服务",
    },
}


class SettingsError(ValueError):
    """取值校验失败。`field` 指明不合法的字段名。"""

    def __init__(self, field, message):
        super().__init__("{0}: {1}".format(field, message))
        self.field = field
        self.message = message


def mask_secret(value):
    """把密钥掩码成 `sk-1****8056` 形式：保留前 4 位与后 4 位。"""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if len(text) < 12:
        return "****"
    return text[:4] + "****" + text[-4:]


def is_masked_or_empty(value, current):
    """判断提交值是否表示“不修改”：空串、None、或等于当前掩码。"""
    if value is None or value == "":
        return True
    return value == mask_secret(current)


def _as_bool(value):
    """把设置值解析为布尔值。

    字符串只接受明确的真假字面量——原先"非空即真"会把 ``"not-a-bool"``
    也当成 True，等于布尔字段没有任何校验。现在拼错的���值会明确报错。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        raise ValueError("无法解析为布尔值")
    if isinstance(value, (int, float)):
        return bool(value)
    raise ValueError("无法解析为布尔值")


class SettingsStore:
    """设置存储。线程安全（写入用可重入锁保护）。"""

    def __init__(self, path=None):
        self.path = path or SETTINGS_FILE
        self._lock = threading.RLock()
        self._data = {}
        self.load()

    # ── 文件读写 ───────────────────────────────────────────────
    def load(self):
        """宽容解析：文件缺失用默认值；损坏则保留原文件并改用默认值。"""
        with self._lock:
            self._data = {}
            if not os.path.isfile(self.path):
                return
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
            except Exception as exc:  # noqa: BLE001 - 任何解析失败都降级为默认值
                logger.error(
                    "[设置] %s 无法解析，改用默认值启动；原文件保持不变：%s", self.path, exc
                )
                return
            if not isinstance(raw, dict):
                logger.error("[设置] %s 结构不是 JSON 对象，改用默认值启动", self.path)
                return
            cleaned = {}
            for key, value in raw.items():
                # 早期版本把访问口令写在设置文件里；口令机制退役后该键被丢弃，
                # 下次 save() 时自然从文件中消失（保留它只会让读者以为还有凭据要管）。
                if key == "access_token":
                    continue
                if isinstance(value, dict):
                    cleaned[key] = dict(value)
            self._data = cleaned

    def save(self):
        """原子写入，避免写一半被读到。"""
        with self._lock:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            handle_fd, tmp_path = tempfile.mkstemp(
                dir=directory or ".", prefix=".settings-", suffix=".tmp"
            )
            try:
                with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                    json.dump(self._data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                os.replace(tmp_path, self.path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    # ── 取值解析 ───────────────────────────────────────────────
    def _stored(self, dotted):
        group, _, key = dotted.partition(".")
        if not key:
            return self._data.get(dotted)
        group_data = self._data.get(group)
        if isinstance(group_data, dict):
            return group_data.get(key)
        return None

    def resolve(self, dotted):
        """按 设置文件 > 环境变量 > 内置默认值 解析一项设置。

        默认把空串当作「未设置」继续往下层找；声明了 `empty_is_value` 的字段
        例外——对它们空串本身就是有意义的取值（如 tts.fallback 的空 = 不回退），
        必须原样返回，否则用户关掉回退的意图会被默认值吞掉。
        """
        spec = FIELD_SPECS.get(dotted)
        stored = self._stored(dotted)
        if stored is not None and (stored != "" or (spec or {}).get("empty_is_value")):
            return stored
        env_name = spec.get("env") if spec else None
        if env_name:
            env_value = os.getenv(env_name)
            if env_value not in (None, ""):
                return env_value
        return spec.get("default") if spec else None

    def get(self, dotted):
        return self.resolve(dotted)

    def get_str(self, dotted):
        value = self.resolve(dotted)
        return "" if value is None else str(value)

    def get_float(self, dotted):
        value = self.resolve(dotted)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def get_bool(self, dotted):
        value = self.resolve(dotted)
        try:
            return _as_bool(value)
        except ValueError:
            return False

    # ── 运行时应用 ─────────────────────────────────────────────
    # 可被运行期设置覆盖的「对新建会话生效」字段：dotted 设置名 -> opt 属性名
    OPT_MAPPING = (
        ("tts.engine", "tts"),
        ("tts.fallback", "TTS_FALLBACK"),
        ("tts.rate", "TTS_RATE"),
        ("tts.ref_file", "REF_FILE"),
        ("tts.ref_text", "REF_TEXT"),
        ("avatar.id", "avatar_id"),
        ("mouth.alpha", "mouth_alpha"),
        ("mouth.offset_x", "mouth_offset_x"),
        ("mouth.offset_y", "mouth_offset_y"),
    )

    def _is_explicit(self, dotted):
        """该字段是否被显式设置过（设置文件或环境变量），而非仅取内置默认值。"""
        return self._source_of(dotted) in ("settings", "env")

    def apply_to_opt(self, opt, cli_locked=()):
        """把 `new_session` 档的设置写入启动参数对象。

        调用点必须是"构造新会话之前"，这样已存在的会话不受影响。

        两条优先级规则（缺一不可）：

        1. **显式命令行参数优先**：`cli_locked` 里列出的选项不被覆盖。否则用户
           明确传的 `--tts sapi` 会被设置文件或默认值悄悄改回去。
        2. **内置默认值不覆盖任何东西**：只应用显式设置过的字段。否则默认值
           （如 `tts.engine` 的 `edgetts`）会把命令行参数一并覆盖掉。
        """
        locked = {str(name).lstrip("-").lower() for name in (cli_locked or ())}
        for dotted, attr in self.OPT_MAPPING:
            if attr.lower() in locked:
                continue
            if not self._is_explicit(dotted):
                continue
            if dotted == "tts.ref_text":
                opt.REF_TEXT = self.get_str(dotted) or None
                continue
            if dotted == "tts.fallback":
                # 显式留空 = 关闭回退，必须真的写回空串。
                # 若走下面的「空值跳过」分支，opt 会残留上一次的值，
                # 造成界面上关掉回退但实际仍在回退。
                setattr(opt, attr, self.get_str(dotted))
                continue
            spec = FIELD_SPECS[dotted]
            if spec.get("type") in ("int", "float"):
                value = self.get_float(dotted)
                if value is None:
                    continue
            else:
                value = self.get_str(dotted)
                if value == "":
                    continue
            setattr(opt, attr, value)

    def rag_env(self):
        """返回要注入 RAG 子进程的环境变量（显式设置过的项）。"""
        env = {}
        for dotted in FIELD_SPECS:
            group, _, key = dotted.partition(".")
            if group != "rag":
                continue
            stored = self._stored(dotted)
            if stored is None or stored == "":
                continue
            if isinstance(stored, bool):
                env[key] = "true" if stored else "false"
            else:
                env[key] = str(stored)
        return env

    def rag_source(self):
        """RAG 参数的来源：界面上写过就是 settings，否则是 env。"""
        return "settings" if self.rag_env() else "env"

    def rag_env_file_values(self):
        """解析 .env.rag 中的 RAG_* 实际值（RAG 服务权威配置，界面未覆盖时展示用）。"""
        path = os.path.join(ROOT, ".env.rag")
        values = {}
        if not os.path.isfile(path):
            return values
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    k = k.strip()
                    if k.startswith("RAG_") or k in ("OLLAMA_BASE_URL", "OLLAMA_LLM_LIBRARY"):
                        values[k] = v.strip()
        except Exception:  # noqa: BLE001
            pass
        return values

    def export_rag_env(self):
        """把设置里的 RAG 参数写进进程环境。

        设置文件优先于环境变量，因此这里直接覆盖；RAG 子进程会继承这份环境，
        从而不必改写用户的 `.env.rag`。
        """
        overrides = self.rag_env()
        if overrides:
            os.environ.update(overrides)
        return overrides

    # ── 校验 ───────────────────────────────────────────────────
    def _validate(self, dotted, value, spec):
        kind = spec.get("type", "str")
        label = spec.get("label", dotted)
        if kind in ("secret", "str", "text"):
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise SettingsError(label, "必须是字符串")
            return str(value)
        if kind == "nonempty":
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                raise SettingsError(label, "必须是字符串")
            text = str(value).strip()
            if not text:
                raise SettingsError(label, "不能为空")
            return text
        if kind == "url":
            if not isinstance(value, str) or not value.strip():
                raise SettingsError(label, "必须是字符串形式的 URL")
            parsed = urlparse(value.strip())
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise SettingsError(label, "必须是 http/https 开头的合法地址")
            return value.strip()
        if kind == "enum":
            allowed = spec.get("enum", ())
            if value not in allowed:
                raise SettingsError(label, "取值必须是 {0} 之一".format("、".join(allowed)))
            return value
        if kind == "regex":
            if not isinstance(value, str) or not re.fullmatch(spec["regex"], value):
                raise SettingsError(label, "格式不合法（要求 {0}）".format(spec["regex"]))
            return value
        if kind == "bool":
            try:
                return _as_bool(value)
            except ValueError:
                raise SettingsError(label, "必须是布尔值")
        if kind in ("int", "float"):
            if isinstance(value, bool):
                raise SettingsError(label, "必须是数值")
            try:
                number = int(value) if kind == "int" else float(value)
            except (TypeError, ValueError):
                raise SettingsError(label, "必须是{0}".format("整数" if kind == "int" else "数值"))
            low, high = spec.get("min"), spec.get("max")
            if low is not None and number < low:
                raise SettingsError(label, "不得小于 {0}".format(low))
            if high is not None and number > high:
                raise SettingsError(label, "不得大于 {0}".format(high))
            return number
        return value

    # ── 写入 ───────────────────────────────────────────────────
    def update(self, payload):
        """按字段写入。任一项不合法则整次拒绝，不产生部分生效。"""
        if not isinstance(payload, dict):
            raise SettingsError("body", "请求体必须是 JSON 对象")
        clear_list = payload.get("clear") or []
        if not isinstance(clear_list, list):
            raise SettingsError("clear", "必须是数组")

        pending = {}
        for group, fields in payload.items():
            if group == "clear":
                continue
            if group in READONLY_STARTUP_FIELDS or group == "startup":
                raise SettingsError(group, "这是启动类参数，仅可查看；需重启主服务才能修改")
            if not isinstance(fields, dict):
                raise SettingsError(group, "必须是对象")
            for key, value in fields.items():
                dotted = "{0}.{1}".format(group, key)
                spec = FIELD_SPECS.get(dotted)
                if spec is None:
                    continue  # 未知字段静默忽略
                pending[dotted] = value

        for dotted in clear_list:
            if dotted in FIELD_SPECS:
                pending[dotted] = None

        normalized = {}
        for dotted, value in pending.items():
            spec = FIELD_SPECS[dotted]
            if value is None:
                normalized[dotted] = None
                continue
            if spec.get("type") == "secret" and is_masked_or_empty(value, self.resolve(dotted)):
                continue  # 掩码或空值表示不修改
            normalized[dotted] = self._validate(dotted, value, spec)

        with self._lock:
            for dotted, value in normalized.items():
                group, _, key = dotted.partition(".")
                if value is None:
                    group_data = self._data.get(group)
                    if isinstance(group_data, dict):
                        group_data.pop(key, None)
                        if not group_data:
                            self._data.pop(group, None)
                else:
                    self._data.setdefault(group, {})[key] = value
            if normalized:
                self.save()
        return normalized

    # ── 读取（供接口使用） ─────────────────────────────────────
    def read_all(self, opt=None):
        result = {}
        for dotted, spec in FIELD_SPECS.items():
            group, _, key = dotted.partition(".")
            value = self.resolve(dotted)
            if spec.get("type") == "secret":
                shown = mask_secret(value)
            elif spec.get("type") == "bool":
                try:
                    shown = _as_bool(value)
                except ValueError:
                    shown = bool(spec.get("default"))
            elif spec.get("type") in ("int", "float"):
                shown = self._coerce_number(dotted, spec)
            else:
                shown = "" if value is None else value
            result.setdefault(group, {})[key] = {
                "value": shown,
                "effect": spec.get("effect", EFFECT_RESTART),
                "label": spec.get("label", key),
                "help": spec.get("help", ""),
                "readonly": False,
                "source": self._source_of(dotted),
            }
        if opt is not None:
            startup = {}
            for name in READONLY_STARTUP_FIELDS:
                startup[name] = {
                    "value": getattr(opt, name, None),
                    "effect": EFFECT_RESTART,
                    "label": name,
                    "help": RESTART_HINT_MAIN,
                    "readonly": True,
                    "source": "startup",
                }
            result["startup"] = startup
        return result

    def _coerce_number(self, dotted, spec):
        value = self.resolve(dotted)
        if spec.get("type") == "int":
            try:
                return int(value)
            except (TypeError, ValueError):
                return spec.get("default")
        try:
            return float(value)
        except (TypeError, ValueError):
            return spec.get("default")

    def _source_of(self, dotted):
        spec = FIELD_SPECS.get(dotted) or {}
        stored = self._stored(dotted)
        # 与 resolve 保持一致：empty_is_value 的字段把空串视为显式取值
        if stored is not None and (stored != "" or spec.get("empty_is_value")):
            return "settings"
        env_name = spec.get("env")
        if env_name and os.getenv(env_name) not in (None, ""):
            return "env"
        return "default"


_store = None


def init_settings(path=None):
    """初始化全局设置存储（幂等）。"""
    global _store
    if _store is None:
        _store = SettingsStore(path)
    return _store


def get_store():
    """取得全局设置存储；未显式初始化时按默认路径初始化。"""
    global _store
    if _store is None:
        _store = SettingsStore()
    return _store
