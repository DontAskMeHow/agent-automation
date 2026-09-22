# Клиент Bright Data MCP (Streamable HTTP) для объёмного тестирования.
# Использование:
#   python mcp_call.py smoke
#   python mcp_call.py search "запрос" [--engine google|bing|yandex] [--geo ru] [--runs N]
#   python mcp_call.py scrape "https://..." [--runs N]
#   python mcp_call.py assistant "вопрос"
#   python mcp_call.py session-stats
#   python mcp_call.py bench <файл_запросов> [--engines google bing] [--geo ru]
# Токен: BRIGHTDATA_TOKEN или token.local.txt рядом (gitignored). Прокси:
# env MCP_PROXY_URL (напр. socks5://host:1080; пусто = прямое соединение).
# Сервер держит сессию (тёплые вызовы).
import argparse
import json
import os
import re
import sys
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://mcp.brightdata.com/mcp"
# набор инструментов как в дефолте моста brightdata_bridge.py (batch выключен)
DEFAULT_TOOLS = "search_engine,scrape_as_markdown,ask_brightdata_assistant,session_stats"

for _s in (sys.stdout, sys.stderr):
    try:
        # консоль Windows cp1251 — без этого падаем на кириллице в выводе
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_token():
    env = os.environ.get("BRIGHTDATA_TOKEN", "").strip()
    if env:
        return env
    p = os.path.join(HERE, "token.local.txt")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    sys.exit("нет токена: BRIGHTDATA_TOKEN или token.local.txt")


def build_url(tools=None):
    url = f"{BASE}?token={get_token()}"
    if tools:
        url += "&tools=" + tools
    return url


def parse_sse(text):
    """SSE -> JSON-объект. Обходит ломаный JSON сервера (реальные переносы
    строк внутри data:) через маркеры UNTRUSTED / перекладку сырого текста."""
    payload = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        ls = line.strip()
        if ls.startswith("data:"):
            rest = [ls[5:].strip()] + lines[i + 1:]
            payload = "\n".join(rest).strip()
            break
    if payload is None:
        payload = text.strip()
    try:
        return json.loads(payload), False
    except json.JSONDecodeError:
        pass
    m = re.search(r"=====UNTRUSTED_([0-9a-f]+)_BEGIN=====", payload)
    if m:
        marker = m.group(1)
        end = payload.rfind(f"=====UNTRUSTED_{marker}_END=====")
        content = payload[m.end():end].strip()
        return {"result": {"content": [{"type": "text", "text": content}]}}, True
    return {"result": {"content": [{"type": "text", "text": payload}]}}, True


class Session:
    def __init__(self, url, timeout=90):
        self.url = url
        self.timeout = timeout
        self.sid = None
        self.proxies = None
        proxy = os.environ.get("MCP_PROXY_URL", "").strip()
        if proxy:
            self.proxies = {"http": proxy, "https": proxy}

    def post(self, payload):
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        t0 = time.perf_counter()
        r = requests.post(self.url, json=payload, headers=headers,
                          proxies=self.proxies, timeout=self.timeout)
        dt = time.perf_counter() - t0
        new_sid = r.headers.get("Mcp-Session-Id")
        if new_sid:
            self.sid = new_sid
        out, _ = parse_sse(r.content.decode("utf-8", "replace"))
        if payload.get("id"):
            out.setdefault("jsonrpc", "2.0")
            out.setdefault("id", payload["id"])
        return out, dt

    def init(self):
        out, dt = self.post({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                       "clientInfo": {"name": "kimi-brightdata-test", "version": "0.1"}}})
        self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return out, dt

    def call_tool(self, name, arguments, rid=2):
        out, dt = self.post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                             "params": {"name": name, "arguments": arguments}})
        return out, dt

    def list_tools(self):
        out, _ = self.post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        return out


def open_session(tools=None):
    s = Session(build_url(tools))
    out, dt = s.init()
    if "error" in out:
        print("initialize error:", out["error"])
        sys.exit(1)
    return s, dt


def result_text(out, limit=6000):
    try:
        for item in out["result"]["content"]:
            if item.get("type") == "text":
                return item["text"][:limit]
    except Exception:
        pass
    return json.dumps(out, ensure_ascii=False)[:limit]


def cmd_smoke(_):
    s, dt = open_session(DEFAULT_TOOLS)
    print("initialize:", round(dt, 2), "s")
    out = s.list_tools()
    tools = out.get("result", {}).get("tools", [])
    print("tools:", ", ".join(t["name"] for t in tools))
    for t in tools:
        print(" -", t["name"], "|", (t.get("description") or "")[:100].replace("\n", " "))


def cmd_search(args):
    s, _ = open_session("search_engine")
    lats = []
    out = None
    for _ in range(max(1, args.runs)):
        arguments = {"query": args.query}
        if args.engine:
            arguments["engine"] = args.engine
        if args.geo:
            arguments["geo_location"] = args.geo
        out, dt = s.call_tool("search_engine", arguments)
        lats.append(round(dt, 3))
    print("latencies:", lats, "| mean:", round(sum(lats) / len(lats), 3))
    print(result_text(out))


def cmd_scrape(args):
    s, _ = open_session("scrape_as_markdown")
    lats = []
    out = None
    for _ in range(max(1, args.runs)):
        out, dt = s.call_tool("scrape_as_markdown", {"url": args.url})
        lats.append(round(dt, 3))
    print("latencies:", lats, "| mean:", round(sum(lats) / len(lats), 3))
    print(result_text(out, 8000))


def cmd_assistant(args):
    s, _ = open_session("ask_brightdata_assistant")
    out, dt = s.call_tool("ask_brightdata_assistant", {"question": args.question})
    print("latency:", round(dt, 2), "s")
    print(result_text(out, 8000))


def cmd_session_stats(_):
    s, _ = open_session("session_stats")
    out, dt = s.call_tool("session_stats", {})
    print("latency:", round(dt, 2), "s")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4000])


def cmd_bench(args):
    with open(args.file, encoding="utf-8") as f:
        queries = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    engines = args.engines or ["google", "bing"]
    geo = args.geo or "ru"
    s, _ = open_session("search_engine")
    total, n, lats = 0.0, 0, []
    print(f"queries={len(queries)} engines={engines} geo={geo}")
    for i, q in enumerate(queries):
        for engine in engines:
            out, dt = s.call_tool("search_engine", {"query": q, "engine": engine,
                                                    "geo_location": geo})
            n += 1
            total += dt
            lats.append(dt)
            text = result_text(out, 50).replace("\n", " | ")
            has_err = "error" in out
            print(f"{i + 1:02d} {engine:6s} {dt:6.2f}s {'ERR' if has_err else 'OK '} "
                  f"{q[:44]!r} -> {text[:60]!r}")
    lats.sort()
    print(f"сумма {total:.1f}s; медиана {lats[len(lats)//2]:.2f}s; мин {lats[0]:.2f}s; "
          f"макс {lats[-1]:.2f}s; вызовов {n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["smoke", "search", "scrape", "assistant",
                                    "session-stats", "bench"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--engine")
    ap.add_argument("--geo")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--engines", nargs="*")
    args = ap.parse_args()
    args.query = args.arg
    args.url = args.arg
    args.question = args.arg
    args.file = args.arg
    {"smoke": cmd_smoke, "search": cmd_search, "scrape": cmd_scrape,
     "assistant": cmd_assistant, "session-stats": cmd_session_stats,
     "bench": cmd_bench}[args.cmd](args)


if __name__ == "__main__":
    main()
