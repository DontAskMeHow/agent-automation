"""Общие утилиты коллектора tg-channels: схема строк, выгрузка 4 файлов, дедуп latest.json.

Сбор постов каналов пишет в data/telegram/ (переопределяется env TG_OUT_DIR)
четыре файла:
  <YYYY-MM-DD>.json       — новые посты дня (накапливаются, дедуп по id),
  <YYYY-MM-DD>.md         — markdown-таблица,
  <YYYY-MM-DD>.links.txt  — по URL на строку,
  latest.json             — накопительная база (∪ новых), дедуп по id.
"""

import json
import os
import sys
import time
from pathlib import Path


def _fix_console():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


_fix_console()

# корень репозитория (collectors/tg-channels/ -> на два уровня вверх)
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(os.environ.get("TG_OUT_DIR", "").strip()
               or REPO_ROOT / "data" / "telegram")
LATEST_PATH = OUT_DIR / "latest.json"
# подписки: channels.json рядом со скриптами (копируется из
# channels.example.json, gitignored)
CHANNELS_PATH = Path(__file__).resolve().parent / "channels.json"

# Строгий набор ключей строки результата (порядок в файле)
ROW_KEYS = ["id", "title", "channel", "datetime", "url", "views", "text"]

FETCH_PAUSE_SEC = 2.5
# SOCKS5-фолбэк при сетевой ошибке; пусто = только прямое соединение
PROXY_URL = os.environ.get("PROXY_URL", "").strip()


def out_dir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


def load_channels():
    if not CHANNELS_PATH.exists():
        return []
    try:
        data = json.loads(CHANNELS_PATH.read_text(encoding="utf-8"))
        return [str(c) for c in data.get("channels", [])]
    except Exception:
        return []


def save_channels(names):
    CHANNELS_PATH.write_text(
        json.dumps({"channels": names}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_latest_rows():
    """База latest.json целиком и набор id для дедупа."""
    if not LATEST_PATH.exists():
        return [], set()
    try:
        rows = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [], set()
    clean = [{k: str(r.get(k, "") or "") for k in ROW_KEYS} for r in rows]
    return clean, {r["id"] for r in clean if r["id"]}


def _md_cell(text):
    return str(text if text is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def write_results(rows, criteria_text):
    """Записать файлы дня + latest.json; вернуть (пути, число новых).

    По файлу каждого типа в СУТКИ — <день>.json/.md/.links.txt накапливают
    новые посты дня (дедуп по id), latest.json — накопительная база (∪ новых).
    При нуле новых файлы не пишутся.
    """
    clean = [{k: str(r.get(k, "") or "") for k in ROW_KEYS} for r in rows]
    day = time.strftime("%Y-%m-%d")
    d = out_dir()
    p_json = d / f"{day}.json"
    p_md = d / f"{day}.md"
    p_links = d / f"{day}.links.txt"

    base, seen = load_latest_rows()
    new = [r for r in clean if r["id"] and r["id"] not in seen]
    latest_set = base + new
    if not new:
        return {"json": p_json, "md": p_md, "links": p_links, "latest": LATEST_PATH}, 0

    day_rows = []
    if p_json.exists():
        try:
            day_rows = [
                {k: str(r.get(k, "") or "") for k in ROW_KEYS}
                for r in json.loads(p_json.read_text(encoding="utf-8"))
                if isinstance(r, dict)
            ]
        except Exception:
            day_rows = []
    day_seen = {r["id"] for r in day_rows if r["id"]}
    day_rows.extend(r for r in new if r["id"] not in day_seen)

    now = time.strftime("%H:%M")
    p_json.write_text(
        json.dumps(day_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    md = [
        f"# Telegram — посты каналов ({day})",
        "",
        f"_Последний прогон: {criteria_text} ({now}); за день: {len(day_rows)}._",
        "",
        "| № | Канал | Дата | Пост | Просмотры |",
        "|---|-------|------|------|-----------|",
    ]
    for i, r in enumerate(day_rows, 1):
        title = _md_cell(r["title"]) or _md_cell(r["text"])[:80]
        name = f"[{title}]({r['url']})" if r["url"] else title
        md.append(
            f"| {i} | {_md_cell(r['channel'])} | {_md_cell(r['datetime'])} "
            f"| {name} | {_md_cell(r['views'])} |"
        )
    p_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    urls = []
    for r in day_rows:
        if r["url"] and r["url"] not in urls:
            urls.append(r["url"])
    p_links.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")

    LATEST_PATH.write_text(
        json.dumps(latest_set, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths = {"json": p_json, "md": p_md, "links": p_links, "latest": LATEST_PATH}
    return paths, len(new)
