# Agent Automation Toolbox

Мозаика небольших самодостаточных инструментов автоматизации для воркспейса
ИИ-агента: демон фоновых задач, мониторинг здоровья с каталогом проблем,
мосты MCP, веб-сборщики и конвертеры документов. Чистый Python (по
возможности stdlib); инструменты независимы и комбинируемы.

## Структура

| Каталог | Содержимое |
|---|---|
| `daemon/` | `periodic.py` — исполнитель периодических задач: триггер Планировщика Windows + цикл демона со случайным интервалом |
| `monitoring/` | `problems.py` — каталог записок механизмов о проблемах (JSON); `health.py` — диагностика воркспейса (планировщик, логи прогонов, индексы, Bright Data, система, git) |
| `bridges/` | `brightdata/` — stdio-MCP-мост к HTTP-MCP Bright Data + тест-клиент `mcp_call.py`; `pencil/` — мост к MCP-серверу Pencil (канвас pen.dev в VS Code) |
| `collectors/` | `huggingface/` — новые модели / тренды / статьи дня через публичный Hub API; `tg-channels/` — посты из публичных превью Telegram-каналов (`t.me/s/`) |
| `converters/` | `md_to_docx.py` (python-docx), `md_to_pdf.py` (Chrome headless print-to-pdf) |
| `docs/notes/` | полевые заметки: патчи kimi-cli под локальные LLM-серверы; безопасная запись больших файлов в инструментах агента |

## Быстрый старт

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows; в остальных ОС: `source .venv/bin/activate`
pip install -r requirements.txt
```

**Демон периодических задач** (install/uninstall требуют Планировщика Windows):

```bash
python daemon/periodic.py install --name demo --min-hours 4 --max-hours 5 --command "python -c \"print('ok')\""
python daemon/periodic.py status --name demo     # состояние + хвост лога
python daemon/periodic.py once --name demo       # разовый проход сейчас
python daemon/periodic.py stop --name demo       # мягкая остановка (в шаге сна 10 мин)
python daemon/periodic.py uninstall --name demo
```

**Каталог проблем + health** — при сбое механизмы зовут `problems.add(...)`
(см. `monitoring/README.md`):

```bash
python monitoring/problems.py add --source demo --severity error --title "pass failed" --detail "trace"
python monitoring/health.py problems
python monitoring/health.py check                # корень: env WORKSPACE, по умолчанию — корень репозитория
```

**Мост Bright Data** — токен: env `BRIGHTDATA_TOKEN` или
`bridges/brightdata/token.local.txt` (в gitignore); SOCKS5-маршрутизация: env
`MCP_PROXY_URL` (например, `socks5://host:1080`); переопределение эндпоинта:
`MCP_UPSTREAM_URL`. Укажите в MCP-конфиге агента
`python bridges/brightdata/brightdata_bridge.py`; проверка —
`python bridges/brightdata/mcp_call.py smoke`.

**Мост Pencil** — `bridges/pencil/pencil-bridge.py` транслирует между
stdio-MCP-фреймингом агента и локальным бинарём Pencil
(`~/.pencil/mcp/visual_studio_code/out/mcp-server-*.exe`, стартует
расширение VS Code) и перезапускает его при падении.

**Сборщики** — выгрузки в gitignored `data/`; SOCKS5-фолбэк через env
`PROXY_URL` (пусто = напрямую):

```bash
cp collectors/huggingface/criteria.example.json collectors/huggingface/criteria.json  # настройте (в gitignore)
python collectors/huggingface/collect.py               # -> data/huggingface/ (env HF_OUT_DIR)
python collectors/tg-channels/subscribe.py --add examplechannel
python collectors/tg-channels/collect.py --hours 24    # -> data/telegram/ (env TG_OUT_DIR)
```

**Конвертеры**:

```bash
python converters/docx/md_to_docx.py report.md report.docx
python converters/pdf/md_to_pdf.py report.md report.pdf --chrome "C:\Program Files\Google\Chrome\Application\chrome.exe"
```
