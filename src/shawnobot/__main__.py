from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

from .bot.feishu import FeishuBot
from .models import RequestContext
from .runtime import build_runtime
from .settings import ConfigurationError, load_settings, validate_runtime


def _default_config() -> str:
    return os.environ.get("SHAWNOBOT_CONFIG", str(Path.cwd() / "config.yaml"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="shawnobot",
        description="通过飞书安全地查询本地主机、项目并调用 Codex CLI",
    )
    parser.add_argument(
        "--config",
        default=_default_config(),
        help="YAML 配置文件路径（默认：SHAWNOBOT_CONFIG 或 ./config.yaml）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出调试日志",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("run", help="连接飞书并持续运行")
    subparsers.add_parser("check", help="检查配置与项目路径")
    local = subparsers.add_parser("local", help="不连接飞书，本地执行一条指令")
    local.add_argument("command", help='例如 "/projects"')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = load_settings(args.config)
        warnings = validate_runtime(
            settings,
            require_credentials=args.action == "run",
        )
    except ConfigurationError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    for warning in warnings:
        print(f"警告：{warning}", file=sys.stderr)
    if args.action == "check":
        print(f"配置有效：{settings.config_path}")
        print(f"项目数：{len(settings.projects)}")
        print(f"Codex：{'启用' if settings.codex.enabled else '禁用'}")
        print(
            "DeepSeek NLU："
            + (
                f"启用（{settings.nlu.model}，密钥"
                f"{'已读取但未验证' if settings.nlu.api_key else '未配置'}）"
                if settings.nlu.enabled
                else "禁用"
            )
        )
        return 0

    runtime = build_runtime(settings)
    if args.action == "local":
        context = RequestContext(
            sender_id="local",
            chat_id="local",
            chat_type="p2p",
            message_id="local",
        )
        print(runtime.router.handle(args.command, context))
        return 0

    bot = FeishuBot(
        settings,
        runtime.router,
        runtime.authorizer,
        runtime.audit,
        runtime.watches,
    )
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("ShawnoBot 已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
