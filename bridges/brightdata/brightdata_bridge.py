#!/usr/bin/env python3
"""Мост stdio MCP (JSON-lines) <-> Bright Data MCP (Streamable HTTP) через SOCKS5.

Аналог mcp_ssl_proxy.py коллег под конвенции воркспейса. Kimi Code на Windows
говорит со stdio-MCP серверами JSON-lines (строка = сообщение, без
Content-Length). Мост пробрасывает запросы в удалённый MCP Bright Data,
принудительно маршрутизируя трафик через MCP_PROXY_URL (socks5://…), чтобы
сервис работал и без VPN.

env:
  BRIGHTDATA_TOKEN  — токен (или файл token.local.txt рядом с мостом, gitignored)
  MCP_UPSTREAM_URL  — https://mcp.brightdata.com/mcp?token=…[&tools=…]
  MCP_PROXY_URL     — SOCKS5-прокси (напр. socks5://host:1080; пусто = прямое)
  MCP_VERIFY        — true/false, проверка TLS-сертификата апстрима
  PYTHONIOENCODING  — utf-8 (обязательно для кириллицы в pipe на Windows)

Диагностика — brightdata-bridge.log рядом с мостом (методы, ошибки апстрима).
initialize при кривом/отсутствующем ответе апстрима синтезируется локально,
чтобы kimi не отключал сервер на этапе хендшейка.
"""
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "brightdata-bridge.log")

PROXY = os.environ.get("MCP_PROXY_URL", "")
VERIFY = os.environ.get("MCP_VERIFY", "true").strip().lower() not in ("false", "0", "no", "off")
DEFAULT_TOOLS = "search_engine,scrape_as_markdown,ask_brightdata_assistant,session_stats"


def _log(msg):
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 500_000:
            os.remove(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _read_token():
    env = os.environ.get("BRIGHTDATA_TOKEN", "").strip()
    if env:
        return env
    p = os.path.join(HERE, "token.local.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _upstream_url():
    url = os.environ.get("MCP_UPSTREAM_URL", "").strip()
    if url:
        return url
    token = _read_token()
    if not token:
        print("нет токена: BRIGHTDATA_TOKEN или token.local.txt рядом с мостом",
              file=sys.stderr, flush=True)
        return ""
    tools = os.environ.get("BRIGHTDATA_TOOLS", DEFAULT_TOOLS).strip()
    return f"https://mcp.brightdata.com/mcp?token={token}&tools={tools}"

_session_headers = {}
_proxies = {"http": PROXY, "https": PROXY} if PROXY else None

# методы, которые обслуживаются локально (апстрим их не знает)
LOCAL_METHODS = {"ping"}


def _parse_sse(text: str):
    """SSE -> JSON-объект из data:-строк.

    Событие Bright Data: заголовки event:/id: + data: строки, блоки разделены
    пустой строкой. JSON в data: бывает сломан — в поле text реальные переносы
    строк без экранирования. Поэтому: корректно собираем data:-строки блока,
    пробуем json; при неудаче вытаскиваем содержимое между маркерами UNTRUSTED
    (все ответы scrape/search обёрнуты в SECURITY NOTICE), иначе — сырой текст."""
    text = text.strip("\r\n")
    payload = None
    for block in re.split(r"\r?\n\r?\n", text):
        data_parts = []
        for ln in block.splitlines():
            if ln.startswith("data:"):
                data_parts.append(ln[5:].lstrip())
        if data_parts:
            payload = "\n".join(data_parts)
            break
    if payload is None:
        payload = text
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass
    m = re.search(r"=====UNTRUSTED_([0-9a-f]+)_BEGIN=====", payload)
    if m:
        marker = m.group(1)
        end = payload.rfind(f"=====UNTRUSTED_{marker}_END=====")
        start = m.end()
        content = payload[start:end].strip()
        return {"result": {"content": [{"type": "text", "text": content}]},
                "_reconstructed": True}
    m = re.match(r'^\{?"error"', payload)
    if m:
        return {"result": {"content": [{"type": "text", "text": payload}]},
                "_reconstructed": True}
    return {"result": {"content": [{"type": "text", "text": payload}]},
            "_reconstructed": True}


def upstream(url: str, method: str, params, rid, timeout=120):
    payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if rid is not None:
        payload["id"] = rid
    r = requests.post(
        url,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **_session_headers,
        },
        proxies=_proxies,
        verify=VERIFY,
        timeout=timeout,
    )
    r.raise_for_status()
    sid = r.headers.get("Mcp-Session-Id")
    if sid and sid != _session_headers.get("Mcp-Session-Id"):
        _session_headers["Mcp-Session-Id"] = sid
    items = _parse_sse(r.content.decode("utf-8", "replace"))
    # восстановленная структура не несёт id/протокол — достраиваем
    if isinstance(items, dict) and "_reconstructed" in items:
        items = {"jsonrpc": "2.0", "id": rid, "result": items["result"]}
    return items


def _ok_initialize(req):
    proto = "2025-11-25"
    try:
        proto = (req.get("params") or {}).get("protocolVersion") or proto
    except AttributeError:
        pass
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": {
        "protocolVersion": proto,
        "capabilities": {"tools": {"listChanged": False}, "logging": {}},
        "serverInfo": {"name": "brightdata-mcp", "version": "1.0.0"},
    }}


def main():
    upstream_url = _upstream_url()
    if not upstream_url:
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method", "")
        rid = req.get("id")
        req_id = rid if "id" in req else None
        if method in LOCAL_METHODS:
            if rid is not None:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}) + "\n")
                sys.stdout.flush()
            continue
        _log(f">> [{req_id}] {method}")
        resp = None
        try:
            resp = upstream(
                upstream_url, method, req.get("params"), rid,
                timeout=15 if method in ("initialize", "tools/list") else 120,
            )
            _log(f"<< [{req_id}] {method} ok "
                 f"{len(json.dumps(resp, ensure_ascii=False))}B")
        except Exception as e:
            _log(f"<< [{req_id}] {method} ERR {type(e).__name__}: {e}")
        # initialize обязан нести валидный result — иначе kimi режет сервер.
        # При неудаче/кривом ответе синтезируем (сессия апстрима уже захвачена).
        if method == "initialize":
            res = (resp or {}).get("result")
            ok = isinstance(res, dict) and "protocolVersion" in res
            if not ok:
                resp = _ok_initialize(req)
                _log(f"<< [{req_id}] initialize синтезирован")
        if req_id is None:  # notification — ответа нет
            continue
        if not resp:
            resp = {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32000, "message":
                              "brightdata upstream unreachable (см. brightdata-bridge.log)"}}
        try:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except (OSError, BrokenPipeError):
            return 0  # клиент закрыл пайп (head/kill) — штатный выход


if __name__ == "__main__":
    sys.exit(main())
