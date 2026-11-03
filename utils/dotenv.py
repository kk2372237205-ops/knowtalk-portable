# -*- coding: utf-8 -*-
"""轻量 .env 加载器：环境变量优先，.env 文件兜底，避免引入额外依赖。"""

import os


def load_dotenv(path: str = ".env") -> bool:
    """
    读取 .env 文件中的 KEY=VALUE 并写入 os.environ（已存在的环境变量优先，不覆盖）。

    Args:
        path: .env 文件路径，默认当前目录 .env

    Returns:
        是否成功读取到文件
    """
    if not os.path.isfile(path):
        return False

    loaded = 0
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            # 跳过空行与注释
            if not line or line.startswith("#"):
                continue
            # 支持 export KEY=VALUE 写法
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去除可选引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            # 环境变量优先：只有未设置时才写入
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1

    if loaded:
        print(f"[config] 已从 {path} 加载 {loaded} 个配置项")
    return True
