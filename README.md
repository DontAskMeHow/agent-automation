# Agent Automation Toolbox

A mosaic of small, self-contained automation tools for an AI-agent workspace:
a background task daemon, health monitoring with a problem catalog, MCP
bridges, web collectors and document converters. Plain Python (stdlib where
possible); pieces are independent and combinable.

## Layout

| Directory | Contents |
|---|---|
| `daemon/` | `periodic.py` — periodic task runner: Task Scheduler trigger + daemon loop with a random interval |
| `monitoring/` | `problems.py` — JSON problem-note catalog for mechanisms; `health.py` — workspace diagnostics (scheduler, run logs, indices, Bright Data, system, git) |
| `bridges/` | `brightdata/` — stdio-MCP bridge to Bright Data's HTTP MCP + `mcp_call.py` test client; `pencil/` — bridge to the Pencil MCP server (pen.dev canvas in VS Code) |
| `collectors/` | `huggingface/` — new models / trends / daily papers via the public Hub API; `tg-channels/` — posts from public Telegram channel previews (`t.me/s/`) |
| `converters/` | `md_to_docx.py` (python-docx), `md_to_pdf.py` (Chrome headless print-to-pdf) |
| `docs/notes/` | field notes: kimi-cli patches for local LLM servers; safe large-file writes in agent tool calls |

## Quickstart

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows; `source .venv/bin/activate` elsewhere
pip install -r requirements.txt
```

**Periodic daemon** (install/uninstall need the Windows Task Scheduler):

```bash
python daemon/periodic.py install --name demo --min-hours 4 --max-hours 5 --command "python -c \"print('ok')\""
python daemon/periodic.py status --name demo     # state + log tail
python daemon/periodic.py once --name demo       # single pass now
python daemon/periodic.py stop --name demo       # soft stop (within the 10-min sleep step)
python daemon/periodic.py uninstall --name demo
```

**Problem catalog + health** — mechanisms call `problems.add(...)` on failure
(see `monitoring/README.md`):

```bash
python monitoring/problems.py add --source demo --severity error --title "pass failed" --detail "trace"
python monitoring/health.py problems
python monitoring/health.py check                # root: env WORKSPACE, default = repo root
```

**Bright Data bridge** — token: env `BRIGHTDATA_TOKEN` or
`bridges/brightdata/token.local.txt` (gitignored); SOCKS5 routing: env
`MCP_PROXY_URL` (e.g. `socks5://host:1080`); endpoint override:
`MCP_UPSTREAM_URL`. Point your agent's MCP config at
`python bridges/brightdata/brightdata_bridge.py`; test with
`python bridges/brightdata/mcp_call.py smoke`.

**Pencil bridge** — `bridges/pencil/pencil-bridge.py` translates between the
agent's stdio MCP framing and the local Pencil server binary
(`~/.pencil/mcp/visual_studio_code/out/mcp-server-*.exe`, started by the
VS Code extension) and respawns it if it dies.

**Collectors** — outputs land in gitignored `data/`; SOCKS5 fallback via env
`PROXY_URL` (empty = direct):

```bash
cp collectors/huggingface/criteria.example.json collectors/huggingface/criteria.json  # tune it (gitignored)
python collectors/huggingface/collect.py               # -> data/huggingface/ (env HF_OUT_DIR)
python collectors/tg-channels/subscribe.py --add examplechannel
python collectors/tg-channels/collect.py --hours 24    # -> data/telegram/ (env TG_OUT_DIR)
```

**Converters**:

```bash
python converters/docx/md_to_docx.py report.md report.docx
python converters/pdf/md_to_pdf.py report.md report.pdf --chrome "C:\Program Files\Google\Chrome\Application\chrome.exe"
```
