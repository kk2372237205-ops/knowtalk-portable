# -*- coding: utf-8 -*-
"""命令行重置本地账号密码。

忘记密码时用这个，不用改文件、也不用删账号：

    python scripts/reset_password.py admin                    # 自动生成并打印新密码
    python scripts/reset_password.py admin --password 新的密码  # 指定新密码
    python scripts/reset_password.py --list                    # 看看有哪些账号

重置后签名密钥会轮换，此前签发的登录态全部失效（所有浏览器需要重新登录）。
"""

import argparse
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.auth_store import DEFAULT_DIR, AuthStore, AuthError  # noqa: E402


def _store(data_dir):
    if data_dir:
        base = Path(data_dir)
        return AuthStore(path=base / "users.json", secret_path=base / "secret.key")
    return AuthStore()


def _generate_password() -> str:
    # 去掉容易看错的 0/O/1/l/I，方便口头转述
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="重置本地账号密码")
    parser.add_argument("username", nargs="?", help="要重置的账号名")
    parser.add_argument("--password", help="新密码；不给则自动生成")
    parser.add_argument("--data-dir", help=f"账号文件所在目录，默认 {DEFAULT_DIR}")
    parser.add_argument("--list", action="store_true", help="只列出账号")
    args = parser.parse_args(argv)

    store = _store(args.data_dir)

    if args.list:
        names = store.usernames()
        if not names:
            print("还没有任何账号。启动服务后在登录页创建管理员。")
        else:
            for name in names:
                print(f"{name}\t{store.display_name(name)}")
        return 0

    if not args.username:
        parser.print_help()
        return 2

    if not store.has_user(args.username):
        print(f"账号不存在：{args.username}", file=sys.stderr)
        names = store.usernames()
        if names:
            print("现有账号：" + "、".join(names), file=sys.stderr)
        return 1

    password = args.password or _generate_password()
    try:
        store.reset_password(args.username, password)
    except AuthError as exc:
        print(f"重置失败：{exc}", file=sys.stderr)
        return 1

    print(f"已重置 {args.username} 的密码：{password}")
    print("此前签发的登录态已全部失效，请用新密码重新登录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
