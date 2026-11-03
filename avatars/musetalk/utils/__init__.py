import sys
from os.path import abspath, dirname
current_dir = dirname(abspath(__file__))
parent_dir = dirname(current_dir)
# 原第 5 行是 `sys.path.append(parent_dir+'/utils')`：它把本目录塞进顶层搜索路径，
# 于是项目自己的 `utils` 包被本目录同名的 utils.py 遮蔽，
# `from utils.logger import logger` 会报 "utils is not a package"。
# 本包内没有任何裸导入依赖它（preprocessing.py 对 face_detection 的兜底已改为临时插入），
# 因此移除，避免污染全局 sys.path。
