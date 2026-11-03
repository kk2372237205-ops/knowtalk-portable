# -*- coding: utf-8 -*-
"""Ollama 客户端的「用完即卸」与「失败原因可操作」测试。

对应 openspec/changes/harden-rag-failure-reporting/tasks.md 第 1、2、4 组。

为什么重要：本机 16GB 内存 + 8GB 显存，8B 模型一加载就占 6.2GB 内存 / 4.6GB 显存，
主程序（torch + wav2lip）与 RAG 侧车会被挤死（实测主程序 import 时 MemoryError 退出、
生成模型 cudaMalloc 失败 → /rag/ask 500）。因此请求必须带 keep_alive 用完即卸，
且失败时必须给出能照着做的提示，而不是 requests 的原始异常。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import requests

from rag_service import ollama_client


class FakeResponse(object):
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text if text is not None else ""

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(
                "%d Server Error: Internal Server Error for url: %s"
                % (self.status_code, "http://127.0.0.1:11434/api/chat"))


class KeepAliveTestCase(unittest.TestCase):
    """1. 嵌入与生成请求都必须带 keep_alive。"""

    def setUp(self):
        self.patcher = mock.patch.object(ollama_client.requests, "post")
        self.post = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_embed_request_carries_keep_alive(self):
        self.post.return_value = FakeResponse(payload={"embeddings": [[0.1, 0.2]]})
        ollama_client.embed_query("python 语法")
        _url, kwargs = self.post.call_args
        self.assertIn("keep_alive", kwargs["json"],
                      "嵌入请求必须带 keep_alive，否则模型长期驻留显存")
        self.assertEqual(kwargs["json"]["keep_alive"], 0,
                         "默认必须用完即卸（keep_alive=0）")

    def test_chat_request_carries_keep_alive(self):
        self.post.return_value = FakeResponse(
            payload={"message": {"content": "答案"}})
        ollama_client.chat([{"role": "user", "content": "问题"}])
        _url, kwargs = self.post.call_args
        self.assertIn("keep_alive", kwargs["json"],
                      "生成请求必须带 keep_alive，否则 8B 模型长期霸占显存与内存")
        self.assertEqual(kwargs["json"]["keep_alive"], 0)

    def test_keep_alive_is_configurable(self):
        with mock.patch.object(ollama_client, "OLLAMA_KEEP_ALIVE", "120"):
            self.assertEqual(ollama_client._keep_alive_value(), 120)
        with mock.patch.object(ollama_client, "OLLAMA_KEEP_ALIVE", "5m"):
            self.assertEqual(ollama_client._keep_alive_value(), "5m")
        with mock.patch.object(ollama_client, "OLLAMA_KEEP_ALIVE", "0"):
            self.assertEqual(ollama_client._keep_alive_value(), 0)


class ChatFailureTestCase(unittest.TestCase):
    """2. 生成失败必须给出可操作的中文提示。"""

    def setUp(self):
        self.patcher = mock.patch.object(ollama_client.requests, "post")
        self.post = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_ollama_unreachable_gives_actionable_message(self):
        self.post.side_effect = requests.ConnectionError("Connection refused")
        with self.assertRaises(RuntimeError) as ctx:
            ollama_client.chat([{"role": "user", "content": "问题"}])
        message = str(ctx.exception)
        self.assertIn("Ollama", message)
        self.assertIn("启动", message, "必须告诉用户去启动 Ollama")
        self.assertIn("解决", message, "必须给出处置办法")
        self.assertFalse(message.startswith("Connection refused"),
                         "不得只回传 requests 的原始异常文本")

    def test_cuda_oom_gives_actionable_message(self):
        oom_detail = ("ggml_backend_cuda_buffer_type_alloc_buffer: allocating 4643.78 MiB "
                      "on device 0: cudaMalloc failed: out of memory")
        self.post.return_value = FakeResponse(
            status_code=500, payload={"error": oom_detail},
            text='{"error": "%s"}' % oom_detail)
        with self.assertRaises(RuntimeError) as ctx:
            ollama_client.chat([{"role": "user", "content": "问题"}])
        message = str(ctx.exception)
        self.assertIn("显存", message, "必须点名是显存不足")
        self.assertIn(ollama_client.OLLAMA_LLM_MODEL, message,
                      "必须点名是哪个模型")
        self.assertIn("ollama stop", message, "必须给出释放显存的具体命令")

    def test_other_http_error_keeps_reason(self):
        self.post.return_value = FakeResponse(
            status_code=500, payload={"error": "something odd"},
            text='{"error": "something odd"}')
        with self.assertRaises(RuntimeError) as ctx:
            ollama_client.chat([{"role": "user", "content": "问题"}])
        self.assertIn("something odd", str(ctx.exception))
        self.assertNotIn("500 Server Error: Internal Server Error for url:",
                         str(ctx.exception),
                         "不得只回传 requests 的原始异常文本")


if __name__ == "__main__":
    unittest.main()
