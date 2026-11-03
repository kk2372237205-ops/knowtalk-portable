# -*- coding: utf-8 -*-
"""utils/settings_store.py 的单元测试（标准库 unittest，无额外依赖）。

运行： python -m unittest discover -s tests -t .
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from utils.platform_support import DEFAULT_TTS_FALLBACK, IS_WINDOWS
from utils.settings_store import (
    EFFECT_IMMEDIATE,
    EFFECT_NEW_SESSION,
    EFFECT_RESTART,
    FIELD_SPECS,
    SettingsError,
    SettingsStore,
    mask_secret,
)


class SettingsStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="knowtalk-settings-")
        self.path = os.path.join(self.tmpdir, "settings.json")
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        for spec in FIELD_SPECS.values():
            name = spec.get("env")
            if name:
                os.environ.pop(name, None)

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def read_raw(self):
        with open(self.path, "r", encoding="utf-8") as handle:
            return handle.read()

    # ── 文件生命周期 ───────────────────────────────────────────
    def test_missing_file_uses_defaults(self):
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("llm.model"), "deepseek-v4-pro")
        self.assertEqual(store.get_str("tts.engine"), "edgetts")
        self.assertFalse(os.path.exists(self.path), "只读取时不应创建文件")

    def test_first_write_creates_dir_and_file(self):
        nested = os.path.join(self.tmpdir, "a", "b", "settings.json")
        store = SettingsStore(nested)
        store.update({"llm": {"model": "my-model"}})
        self.assertTrue(os.path.isfile(nested))
        with open(nested, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["llm"]["model"], "my-model")

    def test_corrupted_file_keeps_original_and_falls_back(self):
        broken = "{ this is not json"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(broken)
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("llm.model"), "deepseek-v4-pro")
        self.assertEqual(self.read_raw(), broken, "损坏的原文件必须保留")

    def test_non_object_json_falls_back(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("[1, 2, 3]")
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("tts.engine"), "edgetts")

    def test_unknown_fields_are_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"llm": {"model": "m1", "legacy_field": "x"}, "ancient": {"a": 1}}, handle)
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("llm.model"), "m1")
        store.update({"llm": {"model": "m2"}})
        with open(self.path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["llm"]["model"], "m2")

    # ── 优先级 ─────────────────────────────────────────────────
    def test_priority_settings_over_env_over_default(self):
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("llm.model"), "deepseek-v4-pro")
        os.environ["DEEPSEEK_MODEL"] = "from-env"
        self.assertEqual(store.get_str("llm.model"), "from-env")
        store.update({"llm": {"model": "from-settings"}})
        self.assertEqual(store.get_str("llm.model"), "from-settings")

    def test_env_only_deployment_is_unchanged(self):
        os.environ["DEEPSEEK_API_KEY"] = "sk-env-only-key-123456"
        os.environ["DEEPSEEK_BASE_URL"] = "https://example.com/v1"
        store = SettingsStore(self.path)
        store.update({"llm": {"model": "deepseek-v4-pro"}})  # 写设置文件这一动作不得改变其它项
        self.assertEqual(store.get_str("llm.api_key"), "sk-env-only-key-123456")
        self.assertEqual(store.get_str("llm.base_url"), "https://example.com/v1")
        self.assertTrue(os.path.isfile(self.path))

    def test_missing_fields_default_per_item(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"llm": {"model": "only-this"}}, handle)
        store = SettingsStore(self.path)
        self.assertEqual(store.get_str("llm.model"), "only-this")
        self.assertEqual(store.get_str("tts.engine"), "edgetts")

    # ── 掩码 ───────────────────────────────────────────────────
    def test_mask_secret_shapes(self):
        self.assertEqual(mask_secret(""), "")
        self.assertEqual(mask_secret("short"), "****")
        self.assertEqual(mask_secret("sk-1TESTFIXTURE0000000000000000008056"), "sk-1****8056")

    def test_read_all_never_exposes_full_secret(self):
        secret = "sk-1TESTFIXTURE0000000000000000008056"
        store = SettingsStore(self.path)
        store.update({"llm": {"api_key": secret}})
        payload = json.dumps(store.read_all(), ensure_ascii=False)
        self.assertNotIn(secret, payload)
        self.assertIn("sk-1****8056", payload)

    # ── 写入与校验 ─────────────────────────────────────────────
    def test_masked_submit_does_not_overwrite(self):
        secret = "sk-1TESTFIXTURE0000000000000000008056"
        store = SettingsStore(self.path)
        store.update({"llm": {"api_key": secret}})
        before = self.read_raw()
        masked = mask_secret(secret)
        store.update({"llm": {"api_key": masked}})
        self.assertEqual(store.get_str("llm.api_key"), secret)
        self.assertEqual(self.read_raw(), before)

    def test_empty_string_does_not_overwrite_secret(self):
        secret = "sk-1TESTFIXTURE0000000000000000008056"
        store = SettingsStore(self.path)
        store.update({"llm": {"api_key": secret}})
        store.update({"llm": {"api_key": ""}})
        self.assertEqual(store.get_str("llm.api_key"), secret)

    def test_clear_flag_removes_value(self):
        store = SettingsStore(self.path)
        store.update({"llm": {"api_key": "sk-1TESTFIXTURE0000000000000000008056"}})
        store.update({"clear": ["llm.api_key"]})
        self.assertEqual(store.get_str("llm.api_key"), "")

    def test_invalid_value_rejects_whole_write(self):
        store = SettingsStore(self.path)
        store.update({"llm": {"model": "good-model"}})
        before = self.read_raw()
        with self.assertRaises(SettingsError) as ctx:
            store.update({"llm": {"model": "should-not-be-written"}, "rag": {"RAG_CHUNK_SIZE": "abc"}})
        self.assertEqual(ctx.exception.field, "分片长度（token）")
        self.assertEqual(self.read_raw(), before, "整次写入必须被拒绝且文件不变")
        self.assertEqual(store.get_str("llm.model"), "good-model")

    def test_out_of_range_rejected(self):
        store = SettingsStore(self.path)
        with self.assertRaises(SettingsError):
            store.update({"rag": {"RAG_VECTOR_THRESHOLD": 3.5}})
        with self.assertRaises(SettingsError):
            store.update({"mouth": {"alpha": -1}})

    def test_invalid_enum_rejected(self):
        store = SettingsStore(self.path)
        with self.assertRaises(SettingsError):
            store.update({"tts": {"engine": "not-a-real-engine"}})
        # indextts2 已知缺少 @register，刻意不在枚举内
        with self.assertRaises(SettingsError):
            store.update({"tts": {"engine": "indextts2"}})

    def test_invalid_url_rejected(self):
        store = SettingsStore(self.path)
        with self.assertRaises(SettingsError):
            store.update({"llm": {"base_url": "not-a-url"}})

    def test_invalid_tts_rate_rejected(self):
        store = SettingsStore(self.path)
        with self.assertRaises(SettingsError):
            store.update({"tts": {"rate": "20%"}})
        store.update({"tts": {"rate": "-20%"}})
        self.assertEqual(store.get_str("tts.rate"), "-20%")

    def test_unknown_field_in_write_is_ignored(self):
        store = SettingsStore(self.path)
        store.update({"llm": {"model": "m", "nope": 1}})
        self.assertEqual(store.get_str("llm.model"), "m")

    def test_startup_field_write_rejected(self):
        store = SettingsStore(self.path)
        before = os.path.exists(self.path)
        with self.assertRaises(SettingsError):
            store.update({"startup": {"model": "musetalk"}})
        with self.assertRaises(SettingsError):
            store.update({"model": "musetalk"})
        self.assertEqual(os.path.exists(self.path), before)

    # ── read_all 契约 ──────────────────────────────────────────
    def test_read_all_has_effect_for_every_item(self):
        store = SettingsStore(self.path)
        data = store.read_all()
        allowed = {EFFECT_IMMEDIATE, EFFECT_NEW_SESSION, EFFECT_RESTART}
        count = 0
        for group, fields in data.items():
            for key, item in fields.items():
                count += 1
                self.assertIn(item["effect"], allowed, "{0}.{1} 的 effect 非法".format(group, key))
                self.assertIn("value", item)
                self.assertIn("label", item)
        self.assertEqual(count, len(FIELD_SPECS))

    def test_read_all_marks_readonly_startup_fields(self):
        class Opt(object):
            model = "wav2lip"
            transport = "webrtc"
            modelres = 192
            batch_size = 16
            listenport = 8010
            fps = 25
            max_session = 1

        store = SettingsStore(self.path)
        data = store.read_all(opt=Opt())
        startup = data["startup"]
        self.assertEqual(startup["model"]["value"], "wav2lip")
        self.assertEqual(startup["model"]["effect"], EFFECT_RESTART)
        self.assertTrue(startup["model"]["readonly"])
        self.assertTrue(startup["model"]["help"])

    # ── apply_to_opt / rag_env ─────────────────────────────────
    def test_apply_to_opt_only_touches_new_session_params(self):
        class Opt(object):
            tts = "edgetts"
            TTS_RATE = "+0%"
            REF_FILE = "orig"
            REF_TEXT = "orig-text"
            avatar_id = "orig-avatar"
            mouth_alpha = 0.96
            mouth_offset_x = 0.15
            mouth_offset_y = -0.15

        store = SettingsStore(self.path)
        opt = Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "edgetts")
        self.assertEqual(opt.avatar_id, "orig-avatar", "未设置的形象不应被清空")
        store.update({"tts": {"engine": "qwentts", "rate": "+20%"}, "avatar": {"id": "Mr.Chen"}, "mouth": {"alpha": 0.5}})
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "qwentts")
        self.assertEqual(opt.TTS_RATE, "+20%")
        self.assertEqual(opt.avatar_id, "Mr.Chen")
        self.assertAlmostEqual(opt.mouth_alpha, 0.5)

    def test_rag_env_only_contains_stored_values(self):
        store = SettingsStore(self.path)
        self.assertEqual(store.rag_env(), {})
        self.assertEqual(store.rag_source(), "env")
        store.update({"rag": {"RAG_CHUNK_SIZE": 512, "RAG_ENABLED": False}})
        env = store.rag_env()
        self.assertEqual(env["RAG_CHUNK_SIZE"], "512")
        self.assertEqual(env["RAG_ENABLED"], "false")
        self.assertEqual(store.rag_source(), "settings")


class ApplyToOptPrecedenceTestCase(unittest.TestCase):
    """显式命令行参数必须优先于运行期设置与内置默认值。

    这是实测发现的缺陷：`apply_to_opt` 曾用内置默认值也无条件覆盖 opt，
    导致 `--tts sapi`、`--TTS_RATE`、`--REF_FILE`、`--avatar_id` 被静默改回去。
    """

    class Opt(object):
        def __init__(self):
            self.tts = "sapi"
            self.TTS_RATE = "+30%"
            self.REF_FILE = "custom-voice"
            self.REF_TEXT = "keep-me"
            self.TTS_FALLBACK = "cli-fallback"
            self.avatar_id = "X"
            self.mouth_alpha = 0.5
            self.mouth_offset_x = 0.1
            self.mouth_offset_y = -0.1

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="knowtalk-precedence-")
        self.path = os.path.join(self.tmpdir, "settings.json")
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        for spec in FIELD_SPECS.values():
            name = spec.get("env")
            if name:
                os.environ.pop(name, None)

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_defaults_must_not_override_cli(self):
        store = SettingsStore(self.path)
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "sapi", "内置默认值不得覆盖命令行传入的引擎")
        self.assertEqual(opt.TTS_RATE, "+30%")
        self.assertEqual(opt.REF_FILE, "custom-voice")
        self.assertEqual(opt.REF_TEXT, "keep-me", "未配置时不得清空 REF_TEXT")
        self.assertEqual(opt.mouth_alpha, 0.5)

    def test_explicit_settings_do_override(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"engine": "edgetts", "rate": "+0%"},
                      "avatar": {"id": "FromSettings"},
                      "mouth": {"alpha": 0.9}})
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "edgetts")
        self.assertEqual(opt.TTS_RATE, "+0%")
        self.assertEqual(opt.avatar_id, "FromSettings")
        self.assertAlmostEqual(opt.mouth_alpha, 0.9)

    def test_cli_locked_wins_over_explicit_settings(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"engine": "edgetts", "rate": "+0%"}})
        opt = self.Opt()
        store.apply_to_opt(opt, cli_locked={"--tts"})
        self.assertEqual(opt.tts, "sapi", "列入 cli_locked 的项必须保留命令行取值")
        self.assertEqual(opt.TTS_RATE, "+0%", "未锁定的项仍应被设置覆盖")

    def test_cli_locked_accepts_option_with_or_without_dashes(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"engine": "edgetts"}})
        for token in ("--tts", "tts", "-tts"):
            opt = self.Opt()
            store.apply_to_opt(opt, cli_locked={token})
            self.assertEqual(opt.tts, "sapi", "cli_locked 应容忍不同写法: %s" % token)

    def test_environment_counted_as_explicit(self):
        os.environ["TTS_ENGINE"] = "qwentts"
        store = SettingsStore(self.path)
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "qwentts", "环境变量属于显式配置，应覆盖 opt")


class TtsFallbackSettingTestCase(unittest.TestCase):
    """备用引擎（TTS_FALLBACK）必须能在设置页配置并对新建会话生效。

    背景：微软 readaloud 端点持续返回 403，在线引擎整体不可用。
    此时"主引擎失败后自动回退到离线引擎"是唯一不中断课堂的路径，
    而它此前只能通过命令行 `--TTS_FALLBACK` 设置，界面上完全没有入口。
    """

    class Opt(object):
        def __init__(self):
            self.tts = "edgetts"
            self.TTS_RATE = "+0%"
            self.REF_FILE = "zh-CN-YunjianNeural"
            self.REF_TEXT = ""
            self.TTS_FALLBACK = "cli-fallback"
            self.avatar_id = ""
            self.mouth_alpha = 0.96
            self.mouth_offset_x = 0.15
            self.mouth_offset_y = -0.15

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="knowtalk-fallback-")
        self.path = os.path.join(self.tmpdir, "settings.json")
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        for spec in FIELD_SPECS.values():
            name = spec.get("env")
            if name:
                os.environ.pop(name, None)

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_field_is_exposed_to_the_settings_page(self):
        store = SettingsStore(self.path)
        self.assertIn("fallback", store.read_all()["tts"],
                      "设置页需要拿到 tts.fallback，否则界面上无从配置")

    def test_default_empty_does_not_clear_cli_value(self):
        store = SettingsStore(self.path)
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "cli-fallback",
                         "未配置备用引擎时不得清空命令行取值")

    def test_explicit_setting_is_applied(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": "sapi"}})
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "sapi")

    def test_empty_is_valid_and_means_no_fallback(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": ""}})
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "",
                         "显式留空代表关闭回退，必须真的写回空串")

    def test_turning_fallback_off_clears_previous_value(self):
        """先开回退再关掉，opt 上不得残留上一次的引擎。

        这是"空值跳过"通用分支会踩到的坑：value 为空就 continue，
        opt 会一直留着上一次写入的 sapi，界面上关了却仍在回退。
        """
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": "sapi"}})
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "sapi")

        store.update({"tts": {"fallback": ""}})
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "", "关掉回退后不得残留 sapi")

    def test_cli_locked_survives_empty_setting(self):
        """命令行显式给的值，不能因为设置里留空而被清掉。"""
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": ""}})
        opt = self.Opt()
        store.apply_to_opt(opt, cli_locked={"--TTS_FALLBACK"})
        self.assertEqual(opt.TTS_FALLBACK, "cli-fallback")

    def test_unknown_engine_is_rejected(self):
        store = SettingsStore(self.path)
        with self.assertRaises(SettingsError):
            store.update({"tts": {"fallback": "no-such-engine"}})

    def test_sapi_is_selectable_both_as_main_and_fallback(self):
        """sapi 在前后端都必须是合法候选，否则界面上选不到离线兜底。"""
        store = SettingsStore(self.path)
        store.update({"tts": {"engine": "sapi", "fallback": "sapi"}})
        opt = self.Opt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.tts, "sapi")
        self.assertEqual(opt.TTS_FALLBACK, "sapi")

    def test_cli_locked_is_respected(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": "sapi"}})
        opt = self.Opt()
        store.apply_to_opt(opt, cli_locked={"--TTS_FALLBACK"})
        self.assertEqual(opt.TTS_FALLBACK, "cli-fallback",
                         "命令行显式给出时必须优先于设置文件")


class TtsFallbackDefaultTestCase(unittest.TestCase):
    """开箱默认兜底：未做任何配置时，离线兜底必须已经生效。

    背景：默认主引擎 `edgetts` 已被服务端持续拒绝，若备用引擎默认仍为空，
    则"默认配置"恰好等于"必然失败 + 无兜底"，整堂课无声。
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="knowtalk-fallback-default-")
        self.path = os.path.join(self.tmpdir, "settings.json")
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        for spec in FIELD_SPECS.values():
            name = spec.get("env")
            if name:
                os.environ.pop(name, None)

    def tearDown(self):
        self.env_patch.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_default_follows_platform_offline_capability(self):
        expected = "sapi" if IS_WINDOWS else ""
        store = SettingsStore(self.path)
        self.assertEqual(DEFAULT_TTS_FALLBACK, expected)
        self.assertEqual(store.resolve("tts.fallback"), expected)
        self.assertEqual(store.get_str("tts.fallback"), expected)

    def test_default_is_reported_as_default_source(self):
        store = SettingsStore(self.path)
        field = store.read_all()["tts"]["fallback"]
        self.assertEqual(field["value"], DEFAULT_TTS_FALLBACK)
        self.assertEqual(field["source"], "default",
                         "默认值必须如实标注来源，否则界面上看不出它来自默认")

    def test_explicit_setting_still_wins_over_default(self):
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": "cosyvoice"}})
        self.assertEqual(store.resolve("tts.fallback"), "cosyvoice")

    def test_explicit_empty_still_disables_fallback(self):
        """选了「（不回退）」必须真的不回退，不能被新默认值吞掉。"""
        store = SettingsStore(self.path)
        store.update({"tts": {"fallback": ""}})
        if IS_WINDOWS:
            self.assertEqual(store.resolve("tts.fallback"), "")
        opt = SettingsStoreOpt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "")

    def test_unset_does_not_override_cli_value(self):
        """默认值不得覆盖命令行显式给出的值（默认值不是"显式配置"）。"""
        store = SettingsStore(self.path)
        opt = SettingsStoreOpt()
        store.apply_to_opt(opt)
        self.assertEqual(opt.TTS_FALLBACK, "cli-fallback",
                         "apply_to_opt 只应写显式设置过的字段；"
                         "若新默认值被当作显式值写入，命令行参数会被悄悄改掉")


class SettingsStoreOpt(object):
    """apply_to_opt 的目标对象，TTS_FALLBACK 预置为命令行取值。"""

    def __init__(self):
        self.tts = "edgetts"
        self.TTS_RATE = "+0%"
        self.REF_FILE = "zh-CN-YunjianNeural"
        self.REF_TEXT = ""
        self.TTS_FALLBACK = "cli-fallback"
        self.avatar_id = ""
        self.mouth_alpha = 0.96
        self.mouth_offset_x = 0.15
        self.mouth_offset_y = -0.15


if __name__ == "__main__":
    unittest.main()
