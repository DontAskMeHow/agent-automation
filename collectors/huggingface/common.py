"""Общие утилиты коллектора huggingface: критерии, схема строк, выгрузка файлов дня.

Выгрузка — data/huggingface/ (переопределяется env HF_OUT_DIR),
по одному комплекту файлов на день:
  <YYYY-MM-DD>.json      — новые позиции дня (накапливаются, дедуп по id),
  <YYYY-MM-DD>.md        — таблица-дайджест,
  <YYYY-MM-DD>.links.txt — по URL на строку,
  latest.json            — накопительная база (∪ новых).
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

# корень репозитория (collectors/huggingface/ -> на два уровня вверх)
REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(os.environ.get("HF_OUT_DIR", "").strip()
               or REPO_ROOT / "data" / "huggingface")
LATEST_PATH = OUT_DIR / "latest.json"
CRITERIA_PATH = Path(__file__).resolve().parent / "criteria.json"

# Строгий набор ключей строки результата (порядок в файле)
ROW_KEYS = ["id", "kind", "title", "author", "url", "date", "stats", "note"]

FETCH_PAUSE_SEC = 1.0
# SOCKS5-фолбэк при сетевой ошибке; пусто = только прямое соединение
PROXY_URL = os.environ.get("PROXY_URL", "").strip()

# нейтральные дефолты; свои критерии -- в criteria.json
# (копируется из criteria.example.json, gitignored)
DEFAULT_CRITERIA = {
    "authors": [],
    "likes_min_new": 20,
    "downloads_min_new": 1000,
    "trending_limit": 30,
    "new_limit": 100,
    "papers_keywords": ["llm", "agent", "multimodal"],
    "papers_fallback": 20,
    "search_terms": [],
    "search_limit": 8,
    "model_configs": False,
}


def load_criteria():
    cfg = json.loads(json.dumps(DEFAULT_CRITERIA))
    if CRITERIA_PATH.exists():
        try:
            cfg.update(json.loads(CRITERIA_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def out_dir():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


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
    """Записать файлы дня + latest.json; вернуть (пути, число новых)."""
    clean = [{k: str(r.get(k, "") or "") for k in ROW_KEYS} for r in rows]
    day = time.strftime("%Y-%m-%d")
    d = out_dir()
    p_json = d / f"{day}.json"
    p_md = d / f"{day}.md"
    p_links = d / f"{day}.links.txt"

    base, seen = load_latest_rows()
    new = [r for r in clean if r["id"] and r["id"] not in seen]
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

    kinds = {"model": "модель", "dataset": "датасет", "space": "space", "paper": "статья"}
    md = [
        f"# Hugging Face — новинки ({day})",
        "",
        f"_Последний прогон: {criteria_text} ({now}); за день: {len(day_rows)}._",
        "",
        "| № | Тип | Автор | Название | Загрузки/лайки | Откуда |",
        "|---|-----|-------|----------|----------------|--------|",
    ]
    for i, r in enumerate(day_rows, 1):
        name = f"[{_md_cell(r['title'])}]({r['url']})" if r["url"] else _md_cell(r["title"])
        md.append(
            f"| {i} | {kinds.get(r['kind'], r['kind'])} | {_md_cell(r['author'])} "
            f"| {name} | {_md_cell(r['stats'])} | {_md_cell(r['note'])} |"
        )
    p_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    urls = []
    for r in day_rows:
        if r["url"] and r["url"] not in urls:
            urls.append(r["url"])
    p_links.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")

    LATEST_PATH.write_text(
        json.dumps(base + new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    paths = {"json": p_json, "md": p_md, "links": p_links, "latest": LATEST_PATH}
    return paths, len(new)
