"""管理登录账号（添加、改密码、停用 / 启用、列出）。系统没有注册入口，账号只能在服务器上建。

用法（在 backend/ 目录下；Docker 部署时在命令前加 docker compose exec backend）：
    python scripts/manage_users.py add 账号        # 新建账号，交互输入密码
    python scripts/manage_users.py passwd 账号     # 重设密码（律师忘记密码时用），该账号所有登录失效
    python scripts/manage_users.py disable 账号    # 停用（立即退出所有已登录的电脑）
    python scripts/manage_users.py enable 账号     # 重新启用
    python scripts/manage_users.py list            # 列出账号
账号库路径取 AUTH_DB_PATH，默认 data/auth.sqlite。密码输入时不回显，也不会进 shell 历史。
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import auth_store  # noqa: E402

_CST = timezone(timedelta(hours=8))


def _ask_password() -> str:
    first = getpass.getpass("输入密码（至少 10 位，输入时不显示）：")
    second = getpass.getpass("再输入一次：")
    if first != second:
        raise auth_store.AuthError("两次输入的密码不一致")
    return first


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts, _CST).strftime("%Y-%m-%d %H:%M")


def main() -> int:
    parser = argparse.ArgumentParser(description="管理登录账号")
    parser.add_argument("action", choices=["add", "passwd", "disable", "enable", "list"])
    parser.add_argument("username", nargs="?")
    args = parser.parse_args()
    if args.action != "list" and not args.username:
        parser.error("请指定账号")

    try:
        if args.action == "add":
            auth_store.create_user(args.username, _ask_password())
            print(f"已创建账号「{args.username}」")
        elif args.action == "passwd":
            auth_store.set_password(args.username, _ask_password())
            print(f"已重设「{args.username}」的密码，该账号需要重新登录")
        elif args.action in ("disable", "enable"):
            auth_store.set_active(args.username, args.action == "enable")
            print(f"已{'启用' if args.action == 'enable' else '停用'}账号「{args.username}」")
        else:
            users = auth_store.list_users()
            if not users:
                print("还没有账号")
            for u in users:
                state = "" if u.active else "  [已停用]"
                print(
                    f"{u.username:<20} 创建于 {_fmt(u.created_at)}  "
                    f"密码更新于 {_fmt(u.password_changed_at)}{state}"
                )
    except auth_store.AuthError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
