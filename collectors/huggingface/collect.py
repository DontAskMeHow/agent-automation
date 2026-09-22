"""Сбор новинок huggingface.co через публичный Hub API (без токена).

Запуск из папки scripts:  python collect.py

Разделы прогона (все параметры — criteria.json):
  1. новые модели (sort=createdAt, пороги likes/downloads + org-авторы);
  2. тренды недели (sort=trendingScore);
  3. статьи дня /api/daily_papers (отбор по papers_keywords);
  4. тематический поиск по терминам (search_terms).
Дедуп по id против latest.json; выгрузка — файлы дня в data/huggingface/
(env HF_OUT_DIR). При сетевой ошибке — повтор через SOCKS5-прокси (env
PROXY_URL, пусто = только прямое соединение).
"""

import argparse
import json
import re
import time

import requests

import common


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

API = "https://huggingface.co/api"


def api_get(path, params):
    """GET Hub API; при сетевой ошибке — один повтор через SOCKS5-прокси (env PROXY_URL)."""
    attempts = ((None, 40),)
    if common.PROXY_URL:
        attempts += (({"http": common.PROXY_URL, "https": common.PROXY_URL}, 60),)
    last = None
    for proxies, timeout in attempts:
        try:
            r = requests.get(API + path, params=params, headers=HEADERS,
                             proxies=proxies, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
    raise RuntimeError("%s: %s" % (path, last))


def stats_of(it):
    dl = it.get("downloads")
    lk = it.get("likes")
    parts = []
    if isinstance(dl, int):
        parts.append("\u2b07%d" % dl)
    if isinstance(lk, int):
        parts.append("\U0001f44d%d" % lk)
    tag = it.get("pipeline_tag")
    if tag:
        parts.append(tag)
    return " ".join(parts)


def row(kind, mid, it, note, date=None):
    author = str(it.get("author") or (mid.split("/")[0] if mid and "/" in mid else ""))
    url = "https://huggingface.co/%s" % mid if mid else ""
    return {
        "id": "%s:%s" % (kind, mid),
        "kind": kind,
        "title": mid or "",
        "author": author,
        "url": url,
        "date": (date or it.get("createdAt") or "")[:10],
        "stats": stats_of(it) if isinstance(it, dict) else str(it or ""),
        "note": note,
    }


def collect_new_models(cfg):
    n = int(cfg.get("new_limit", 100))
    items = api_get("/models", {"sort": "createdAt", "direction": -1, "limit": n, "full": "true"})
    authors = {a.lower() for a in cfg.get("authors", [])}
    likes_min = int(cfg.get("likes_min_new", 1))
    dl_min = int(cfg.get("downloads_min_new", 1))
    rows, skipped = [], 0
    for it in items:
        author = str(it.get("author") or "")
        likes = it.get("likes") or 0
        dl = it.get("downloads") or 0
        if author.lower() in authors or likes >= likes_min or dl >= dl_min:
            rows.append(row("model", it.get("id") or it.get("modelId"), it, "новинка"))
        else:
            skipped += 1
    print("новые модели: всего %d, отобрано %d (отсеяно %d)" % (len(items), len(rows), skipped))
    return rows


def collect_trending(cfg):
    n = int(cfg.get("trending_limit", 30))
    items = api_get("/models", {"sort": "trendingScore", "direction": -1, "limit": n})
    rows = [row("model", it.get("id") or it.get("modelId"), it, "тренд #%d" % (i + 1))
            for i, it in enumerate(items)]
    print("тренды: %d" % len(rows))
    return rows


def collect_papers(cfg):
    keywords = [str(k).lower() for k in cfg.get("papers_keywords", [])]
    fallback = int(cfg.get("papers_fallback", 20))
    items = api_get("/daily_papers", {})
    papers = [p.get("paper") for p in items if isinstance(p, dict) and p.get("paper")]
    picked = []
    if keywords:
        for pp in papers:
            title = str(pp.get("title") or "")
            if any(k in title.lower() for k in keywords):
                picked.append(pp)
    if not picked and papers:
        picked = papers[:fallback]
    rows = []
    for pp in picked:
        mid = str(pp.get("id") or "")
        rows.append({
            "id": "paper:%s" % mid,
            "kind": "paper",
            "title": str(pp.get("title") or ""),
            "author": ", ".join(str(a.get("name") or "") for a in (pp.get("authors") or [])[:3]),
            "url": "https://huggingface.co/papers/%s" % mid,
            "date": (str(pp.get("publishedAt") or ""))[:10],
            "stats": "",
            "note": "статья дня",
        })
    print("статьи дня: всего %d, отобрано %d" % (len(papers), len(rows)))
    return rows


def collect_search(cfg):
    terms = [str(t) for t in cfg.get("search_terms", [])]
    limit = int(cfg.get("search_limit", 8))
    rows = []
    for term in terms:
        items = api_get("/models", {"search": term, "sort": "trendingScore",
                                    "direction": -1, "limit": limit})
        for it in items:
            rows.append(row("model", it.get("id") or it.get("modelId"), it, "поиск: %s" % term))
        print("поиск '%s': %d" % (term, len(items)))
        time.sleep(common.FETCH_PAUSE_SEC)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Сбор новинок huggingface.co (Hub API)")
    ap.add_argument("--skip", default="", help="разделы через запятую: papers,search,new,trending")
    args = ap.parse_args()
    cfg = common.load_criteria()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    rows = []
    if "new" not in skip:
        rows += collect_new_models(cfg)
    if "trending" not in skip:
        rows += collect_trending(cfg)
    if "papers" not in skip:
        rows += collect_papers(cfg)
    if "search" not in skip:
        rows += collect_search(cfg)

    # дедуп внутри прогона
    uniq = {}
    for r in rows:
        uniq.setdefault(r["id"], r)
    rows = list(uniq.values())

    paths, new = common.write_results(rows, "авторы %s; термины %s" % (
        ",".join(cfg.get("authors", [])), ",".join(cfg.get("search_terms", []))))
    print("Собрано позиций: %d, новых: %d" % (len(rows), new))
    if new == 0:
        print("новых нет — файлы дня не менялись")
        return 0
    print("Сохранено:")
    for p in (paths["json"], paths["md"], paths["links"], paths["latest"]):
        print("  %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
