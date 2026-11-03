# 启动失败要能自己说清「用哪个 python」

## 为什么

同一个问题一天内被撞了两次：先 `No module named 'flask'`，清掉死依赖后又变成
`No module named 'numpy'`。两次都不是代码问题，而是**用错了解释器**——
PATH 里的 `python` 落在一个没装项目依赖的 Python 上。

裸 traceback 只说「缺某个模块」，不说「你应该用哪个解释器」，于是用户只能逐个补安装，
或者反过来把 requirements 装到错误的 Python 里，越修越乱。

## 做什么

1. **`app.py` 加一段启动前自检**：在导入 numpy / torch 之前先探测依赖。
   缺了就打一段可以直接照做的话——当前解释器是谁、这台机器上哪个 python 装了全部依赖、
   应该怎么运行、以及「不要在当前解释器里装 requirements」，然后以非零码退出。
   探测用 `importlib.util.find_spec`，不真的导入（torch 导入要好几秒）。
2. **`start.bat` 自动找环境**：激活的 conda 环境 → 项目内 `.venv` → 扫
   `%USERPROFILE%\.conda\envs\*` 与 `%USERPROFILE%\anaconda3\envs\*`，只选真的能
   `import aiohttp, aiortc, numpy, torch` 的那个 → 最后才是 PATH 里的 python。
   这样双击启动不必先 `conda activate`。

## 不做什么

- 不自动 pip 安装依赖（装错环境比报错更难排查）。
- 不改任何接口、页面或运行期行为；依赖齐全时这段自检直接放行，零输出。

## 影响

- 依赖齐全：行为与现在完全一致。
- 依赖不全：从一句 traceback 变成一段带路径的可操作提示。
