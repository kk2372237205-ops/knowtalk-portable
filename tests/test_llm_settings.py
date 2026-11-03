# -*- coding: utf-8 -*-
"""验证 LLM 调用路径确实从运行期设置取值，且改完无需重启即生效。

`llm.py` 每次调用都重新读设置，本测试用打桩的 OpenAI 客户端捕获真实入参，
因此不需要网络、也不需要真实密钥。
"""

import os
import shutil
import tempfile
import unittest
from unittest import mock

from utils.settings_store import DEFAULT_SYSTEM_PROMPT, SettingsStore

import llm as llm_module


class FakeDelta(object):
    def __init__(self, content):
        self.content = content
        self.reasoning_content = None


class FakeChoice(object):
    def __init__(self, content):
        self.delta = FakeDelta(content)


class FakeChunk(object):
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeCompletions(object):
    def create(self, **kwargs):
        FakeOpenAI.requests.append(kwargs)
        return iter([FakeChunk("测试回答。")])


class FakeChat(object):
    def __init__(self):
        self.completions = FakeCompletions()


class FakeOpenAI(object):
    """捕获构造参数与 create 入参，并返回只吐一句固定文本的假流。"""

    constructed = []
    requests = []

    def __init__(self, **kwargs):
        FakeOpenAI.constructed.append(kwargs)
        self.chat = FakeChat()


class FakeSession(object):
    def __init__(self):
        self.speech_request_id = 1
        self.sessionid = "unit-test-session"
        self.llm_text = ""
        self.llm_done = False
        self.chat_history = []
        self.opt = type("Opt", (), {})()
        self.spoken = []

    def put_msg_txt(self, text, datainfo=None):
        self.spoken.append(text)


class LlmSettingsTestCase(unittest.TestCase):
    def setUp(self):
        FakeOpenAI.constructed = []
        FakeOpenAI.requests = []
        self.tmpdir = tempfile.mkdtemp(prefix="knowtalk-llm-")
        self.store = SettingsStore(os.path.join(self.tmpdir, "settings.json"))
        self.env_backup = {}
        for name in ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL", "CHAT_SYSTEM_PROMPT"):
            self.env_backup[name] = os.environ.pop(name, None)
        self.patches = [
            mock.patch.object(llm_module, "get_store", return_value=self.store),
            mock.patch("openai.OpenAI", FakeOpenAI),
            mock.patch.object(llm_module.mat_mgr, "check_and_update_slide", lambda *a, **k: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        for name, value in self.env_backup.items():
            os.environ.pop(name, None)
            if value is not None:
                os.environ[name] = value
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_once(self):
        session = FakeSession()
        llm_module.llm_response("你好", session, {"_speech_request_id": 1})
        return session

    def test_without_api_key_nothing_is_spoken(self):
        session = self.run_once()
        self.assertEqual(FakeOpenAI.constructed, [], "未配置密钥时不应构造客户端")
        self.assertEqual(session.spoken, [], "未配置密钥时不应产生朗读内容")
        self.assertTrue(session.llm_done)

    def test_settings_drive_client_and_request(self):
        self.store.update({
            "llm": {
                "api_key": "sk-test-0000000000000000",
                "base_url": "https://settings.example.com/v1",
                "model": "settings-model",
            }
        })
        session = self.run_once()
        client = FakeOpenAI.constructed[-1]
        self.assertEqual(client["api_key"], "sk-test-0000000000000000")
        self.assertEqual(client["base_url"], "https://settings.example.com/v1")
        self.assertEqual(FakeOpenAI.requests[-1]["model"], "settings-model")
        self.assertEqual(session.llm_text, "测试回答。")
        self.assertEqual(session.spoken, ["测试回答。"])
        self.assertIn("assistant", [m["role"] for m in session.chat_history])

    def test_model_change_takes_effect_without_restart(self):
        self.store.update({"llm": {"api_key": "sk-test-0000000000000000", "model": "first-model"}})
        self.run_once()
        self.assertEqual(FakeOpenAI.requests[-1]["model"], "first-model")

        # 不重启、不重建任何对象，直接改设置后再调一次
        self.store.update({"llm": {"model": "second-model"}})
        self.run_once()
        self.assertEqual(FakeOpenAI.requests[-1]["model"], "second-model",
                         "配置变更必须在下一次调用生效")

    def test_environment_variables_used_when_settings_absent(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-0000000000000000"
        os.environ["DEEPSEEK_BASE_URL"] = "https://env.example.com/v1"
        os.environ["DEEPSEEK_MODEL"] = "env-model"
        self.run_once()
        client = FakeOpenAI.constructed[-1]
        self.assertEqual(client["api_key"], "sk-env-0000000000000000")
        self.assertEqual(client["base_url"], "https://env.example.com/v1")
        self.assertEqual(FakeOpenAI.requests[-1]["model"], "env-model")

    def test_settings_override_environment_variables(self):
        os.environ["DEEPSEEK_MODEL"] = "env-model"
        self.store.update({"llm": {"api_key": "sk-test-0000000000000000", "model": "settings-model"}})
        self.run_once()
        self.assertEqual(FakeOpenAI.requests[-1]["model"], "settings-model")

    def test_system_prompt_default_then_override(self):
        self.store.update({"llm": {"api_key": "sk-test-0000000000000000"}})
        self.run_once()
        messages = FakeOpenAI.requests[-1]["messages"]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], DEFAULT_SYSTEM_PROMPT, "未配置时用内置默认提示词")
        self.assertIn("不要使用 Markdown", messages[0]["content"])

        self.store.update({"llm": {"system_prompt": "你是一位严谨的物理老师。"}})
        self.run_once()
        self.assertEqual(FakeOpenAI.requests[-1]["messages"][0]["content"], "你是一位严谨的物理老师。")


if __name__ == "__main__":
    unittest.main()
