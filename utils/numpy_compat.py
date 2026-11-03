# -*- coding: utf-8 -*-
"""numpy 2.x → 1.x 的 pickle 兼容别名。

形象素材（`coords.pkl` / `mask_coords.pkl`）是别的机器用 numpy 2.x 序列化的，
pickle 里写的是 `numpy._core.multiarray._reconstruct`；本项目运行环境是
numpy 1.22，只有 `numpy.core`。不注册别名时反序列化直接抛

    ModuleNotFoundError: No module named 'numpy._core.multiarray'

看起来像"素材坏了"，其实是解释器版本差异。

幂等判断只看 `numpy._core.multiarray` 是否已正确指向 `numpy.core.multiarray`：
**不能**用 `hasattr(np, '_core')` 判断——torch / diffusers 之类的库会创建
`numpy._core` 的占位命名空间模块，但里面没有 multiarray，据此 return 就会漏注册。
"""

import sys

_ALIAS_NAMES = (
    'multiarray', 'umath', 'numeric', 'overrides', 'fromnumeric', 'arrayprint',
    '_multiarray_umath', 'getlimits', 'einsumfunc', 'shape_base', 'function_base',
    'records', 'defchararray', 'memmap',
)


def ensure_numpy_core_alias():
    """注册 `numpy._core.*` → `numpy.core.*` 别名。重复调用无副作用。"""
    module = sys.modules.get('numpy._core.multiarray')
    if module is not None and getattr(module, '__name__', '') == 'numpy.core.multiarray':
        return True
    try:
        import types

        import numpy as np
        import numpy.core as core

        fake = types.ModuleType('numpy._core')
        for name in _ALIAS_NAMES:
            sub = getattr(core, name, None)
            if sub is not None:
                setattr(fake, name, sub)
                sys.modules['numpy._core.%s' % name] = sub
        sys.modules['numpy._core'] = fake
        return True
    except Exception:  # noqa: BLE001 - 兼容层失败不该拦住主流程
        return False
