"""Управление подписками на Telegram-каналы (channels.json).

Запуск:
  python subscribe.py --list
  python subscribe.py --add mychannel otherchannel
  python subscribe.py --remove mychannel
Имена нормализуются: срезаются @ и пробелы; валидность ^[A-Za-z0-9_]{5,32}$.
"""

import argparse
import json
import re
from pathlib import Path

import common

RE_CHANNEL = re.compile(r"^[A-Za-z0-9_]{5,32}$")


def normalize(name):
    return name.strip().lstrip("@")


def main():
    ap = argparse.ArgumentParser(description="Подписки на Telegram-каналы")
    ap.add_argument("--list", action="store_true", help="показать подписки")
    ap.add_argument("--add", nargs="+", default=[], help="канал(ы) для подписки")
    ap.add_argument("--remove", nargs="+", default=[], help="канал(ы) для отписки")
    args = ap.parse_args()

    channels = common.load_channels()

    for raw in args.add:
        c = normalize(raw)
        if not RE_CHANNEL.match(c):
            print(f"пропущен некорректный: {raw!r}")
            continue
        if c in channels:
            print(f"{c}: уже в подписках")
        else:
            channels.append(c)
            print(f"+ {c}")

    for raw in args.remove:
        c = normalize(raw)
        if c in channels:
            channels.remove(c)
            print(f"- {c}")
        else:
            print(f"{c}: не было в подписках")

    common.save_channels(channels)
    print(f"Подписки ({len(channels)}): {', '.join(channels) if channels else 'нет'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
