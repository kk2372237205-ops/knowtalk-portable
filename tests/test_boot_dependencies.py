# -*- coding: utf-8 -*-
"""启动依赖的锁：入口脚本只能依赖运行时真正用到的库。

来由：`app.py` 从 Flask 迁到 aiohttp 后，头部的 flask 导入与 `app = Flask(...)`
一直留着没人删。它们在运行时从未被读取，却让没装 flask 的解释器在第一屏就
`ModuleNotFoundError`，把「解释器选错了」伪装成「缺 flask 依赖」。
"""

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / 'app.py'
REQUIREMENTS = ROOT / 'requirements.txt'


def imported_modules(path):
    """从 AST 里取所有被导入的顶层模块名——注释与字符串不参与，避免误判。"""
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split('.')[0])
    return names


class NoLegacyFlaskTestCase(unittest.TestCase):
    def test_entry_script_does_not_import_flask(self):
        names = imported_modules(APP)
        for legacy in ('flask', 'flask_sockets'):
            self.assertNotIn(legacy, names, 'app.py 不应再导入 %s（运行时是 aiohttp）' % legacy)

    def test_entry_script_does_not_build_flask_app(self):
        tree = ast.parse(APP.read_text(encoding='utf-8'), filename=str(APP))
        calls = [n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        self.assertNotIn('Flask', calls, 'app.py 不应再构造 Flask 实例')

    def test_requirements_do_not_declare_flask(self):
        lines = requirements_lines()
        offenders = [line for line in lines if line.lower().startswith('flask')]
        self.assertEqual(offenders, [], 'requirements.txt 不应再声明 flask：%s' % offenders)

    def test_core_dependencies_kept(self):
        # 删 flask 时别顺手删掉别的东西：运行期真正用到的几个包必须还在
        joined = '\n'.join(requirements_lines()).lower()
        for pkg in ('aiohttp', 'aiortc', 'torch', 'edge_tts', 'opencv-python-headless'):
            self.assertIn(pkg, joined, 'requirements.txt 里不该少了 %s' % pkg)


class BootAssemblyTestCase(unittest.TestCase):
    """删死导入不许顺手改装配顺序。"""

    ORDER = (
        'setup_settings_routes',
        'install_auth',
        'setup_quiz_routes',
        'setup_stt_routes',
        'setup_routes',
        'setup_material_routes',
        'install_cors',
    )

    def test_route_assembly_order_unchanged(self):
        source = APP.read_text(encoding='utf-8')
        positions = []
        for name in self.ORDER:
            idx = source.find(name + '(')
            self.assertGreater(idx, -1, 'app.py 里找不到装配调用 %s' % name)
            positions.append((name, idx))
        ordered = [name for name, _ in sorted(positions, key=lambda p: p[1])]
        self.assertEqual(ordered, list(self.ORDER))

    def test_aiohttp_entry_intact(self):
        source = APP.read_text(encoding='utf-8')
        self.assertIn('from aiohttp import web', source)
        self.assertIn('web.run_app', source)


def requirements_lines(keep_comments=False):
    out = []
    for raw in REQUIREMENTS.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not keep_comments and (not line or line.startswith('#')):
            continue
        out.append(line)
    return out


class StartScriptTestCase(unittest.TestCase):
    """start_app.bat（桌面版启动器）必须指向项目解释器与桌面入口。

    网页版已移除，start.bat / desktop.py 不再存在；启动器只剩 start_app.bat，
    锁住它的编码与启动链路，避免以后有人改回裸 python 或弄坏桌面版启动。
    """

    def setUp(self):
        self.raw = (ROOT / 'start_app.bat').read_bytes()
        self.text = self.raw.decode('gbk')

    def test_saved_as_crlf_without_bom(self):
        self.assertNotIn(b'\r\n', self.raw.replace(b'\r\n', b''),
                         'start_app.bat 必须是 CRLF')
        self.assertFalse(self.raw.startswith(b'\xef\xbb\xbf'),
                         'start_app.bat 不要带 BOM')

    def test_launches_desktop_app(self):
        self.assertIn('desktop_app.py', self.text)
        self.assertNotIn('python app.py', self.text,
                         '桌面版启动器不应再用裸 python 启动后端')

    def test_uses_project_interpreter(self):
        self.assertIn('pythonw', self.text)
        self.assertIn('desktop_app.py', self.text)


if __name__ == '__main__':
    unittest.main()
