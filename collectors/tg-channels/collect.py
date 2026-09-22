"""Сбор постов подписанных Telegram-каналов через публичные превью t.me/s/<канал>.

Запуск:
  python collect.py [--hours 24] [--channels mychannel,otherchannel]

GET https://t.me/s/<канал> (превью серверные, ~9–20 постов с датами/текстом/
просмотрами); при сетевой ошибке — один повтор через SOCKS5-прокси (env
PROXY_URL). Окно постов — последние N часов; дедуп по id против latest.json.
Выгрузка — 4 файла контракта (см. common.py).
"""

import argparse
import html
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import common

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

RE_MESSAGE = re.compile(r'<div class="tgme_widget_message_wrap[^"]*">')
RE_DATETIME = re.compile(r'<time datetime="([^"]+)"')
RE_MSG_ID = re.compile(r'href="https://t\.me/[^"/]+/(\d+)"')
RE_TEXT = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S
)
RE_VIEWS = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')


def strip_html(text):
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def to_utc(dt_str):
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def fetch_page(channel):
    """GET страницы превью; при сетевой ошибке — один повтор через SOCKS5 (env PROXY_URL)."""
    url = f"https://t.me/s/{channel}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.text
    except requests.RequestException:
        if not common.PROXY_URL:
            raise
        proxies = {"http": common.PROXY_URL, "https": common.PROXY_URL}
        r = requests.get(url, headers=HEADERS, proxies=proxies, timeout=40)
        r.raise_for_status()
        return r.text


def parse_posts(channel, page):
    """Посты страницы превью: id/title/channel/datetime/url/views/text + parsed utc."""
    posts = []
    for part in RE_MESSAGE.split(page)[1:]:
        m_dt = RE_DATETIME.search(part)
        dt_str = m_dt.group(1) if m_dt else ""
        m_id = RE_MSG_ID.search(part)
        msg_id = m_id.group(1) if m_id else ""
        m_text = RE_TEXT.search(part)
        text = strip_html(m_text.group(1)) if m_text else ""
        m_views = RE_VIEWS.search(part)
        views = m_views.group(1) if m_views else ""

        url = f"https://t.me/{channel}/{msg_id}" if msg_id else f"https://t.me/s/{channel}"
        title = text.splitlines()[0][:120] if text else ""
        posts.append(
            {
                "id": f"{channel}:{msg_id or dt_str[:16]}",
                "title": title,
                "channel": channel,
                "datetime": dt_str,
                "url": url,
                "views": views,
                "text": text,
                "_dt": to_utc(dt_str),
            }
        )
    return posts


def main():
    ap = argparse.ArgumentParser(description="Сбор постов Telegram-каналов (t.me/s)")
    ap.add_argument("--hours", type=int, default=24, help="окно постов, часов (дефолт 24)")
    ap.add_argument("--channels", default="", help="каналы через запятую (дефолт — все подписки)")
    args = ap.parse_args()

    channels = [c for c in args.channels.split(",") if c] or common.load_channels()
    if not channels:
        print("Нет подписанных каналов — добавьте: python subscribe.py --add <канал>")
        return 1

    hours = max(1, args.hours)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = []
    print(f"Каналов: {len(channels)}, окно: {hours} ч")
    for ch in channels:
        try:
            page = fetch_page(ch)
            posts = parse_posts(ch, page)
        except Exception as e:
            print(f"{ch}: ошибка получения — {e.__class__.__name__}: {e}")
            time.sleep(common.FETCH_PAUSE_SEC)
            continue
        if not posts:
            print(f"{ch}: постов не найдено (канала нет или пуст)")
            time.sleep(common.FETCH_PAUSE_SEC)
            continue
        in_window = [
            p for p in posts
            if p["_dt"] is None or p["_dt"] >= cutoff
        ]
        for p in in_window:
            p.pop("_dt", None)
        rows.extend(in_window)
        print(f"{ch}: постов на странице {len(posts)}, в окне {len(in_window)}")
        time.sleep(common.FETCH_PAUSE_SEC)

    paths, new = common.write_results(
        rows, f"каналы: {len(channels)}, окно: {hours} ч"
    )
    print(f"Постов в окне: {len(rows)}, новых: {new}")
    if new == 0:
        print("новых постов нет — файлы дня не менялись")
        return 0
    print("Сохранено:")
    for p in (paths["json"], paths["md"], paths["links"], paths["latest"]):
        print(f"  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
