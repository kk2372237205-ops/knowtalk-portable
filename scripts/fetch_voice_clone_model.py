# -*- coding: utf-8 -*-
"""下载声音克隆模型（XTTS-v2，约 1.9GB）到 data/models/xtts_v2。

为什么单独一个脚本：模型太大，不适合在启动服务时隐式下载（会卡住开播流程），
单独跑可以看见进度，失败也方便重试。

用法：
    python scripts/fetch_voice_clone_model.py
    python scripts/fetch_voice_clone_model.py --endpoint https://hf-mirror.com

国内网络直连 huggingface.co 经常失败（代理 502），脚本会先试官方源，
失败自动改走 hf-mirror.com 镜像。
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "data", "models", "xtts_v2")
REPO = "coqui/XTTS-v2"
# 只要推理必需的文件；samples/ 里的示例音频用不上（约 20MB，省掉）
FILES = ["config.json", "model.pth", "dvae.pth", "mel_stats.pth",
         "speakers_xtts.pth", "vocab.json"]


def _have_all():
    return all(os.path.getsize(os.path.join(TARGET, f)) > 1024
               for f in FILES) if os.path.isdir(TARGET) else False


def _try(endpoint):
    os.environ["HF_ENDPOINT"] = endpoint
    # 环境里可能配了全局代理，而代理到 HF 常常 502；直连镜像时绕开它
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        os.environ.pop(key, None)
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    from huggingface_hub import hf_hub_download, list_repo_files
    # 先探一下：能列出文件才说明这个源通
    list_repo_files(REPO, repo_type="model")
    os.makedirs(TARGET, exist_ok=True)
    for name in FILES:
        t0 = time.time()
        path = hf_hub_download(REPO, name, repo_type="model", local_dir=TARGET)
        size = os.path.getsize(path) / 1048576.0
        print("  %-20s %7.1f MB  (%.0fs)" % (name, size, time.time() - t0), flush=True)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--endpoint", default="https://huggingface.co")
    ap.add_argument("--force", action="store_true", help="已存在也重新下载")
    args = ap.parse_args()

    if _have_all() and not args.force:
        print("模型已就绪：%s" % TARGET)
        for name in FILES:
            print("  %-20s %7.1f MB" % (name,
                                        os.path.getsize(os.path.join(TARGET, name)) / 1048576.0))
        return 0

    sources = [args.endpoint]
    if args.endpoint == "https://huggingface.co":
        sources.append("https://hf-mirror.com")

    for endpoint in sources:
        print("尝试从 %s 下载 %s …" % (endpoint, REPO), flush=True)
        try:
            _try(endpoint)
        except Exception as exc:  # noqa: BLE001
            print("  失败：%r" % (exc,), flush=True)
            continue
        print("完成：%s" % TARGET, flush=True)
        return 0

    print("所有源都失败了。可以手动下载后放进 %s：" % TARGET)
    print("  需要的文件：%s" % ", ".join(FILES))
    print("  仓库地址：https://huggingface.co/%s" % REPO)
    return 1


if __name__ == "__main__":
    sys.exit(main())
