# Патчи для работы kimi-cli с локальными LLM-серверами (vLLM и др.)

> Все патчи из этого skill уже **применены в текущей установке**. После
> `pip install --upgrade kimi-cli` их нужно будет повторить — см. секцию
> [«Повторное применение после обновления»](#после-обновления).

---

## Патч 1: JSON Repair для Tool Call Arguments

### Симптом

```text
LLM provider error: Error code: 400 - {'error': {'message': "Expecting ',' delimiter: line 1 column N (char N-1)", 'type': 'BadRequestError', 'param': None, 'code': 400}}
```

В логах `~/.kimi/logs/kimi.log`:

```text
WARNING | kimi_cli.soul.toolset:handle:257 | ... - Tool call JSON parse error: WriteFile (call_id=...): Expecting ',' delimiter: line 1 column N (char N-1)
```

**Почему так происходит:** локальная модель иногда генерирует невалидный JSON
в аргументах вызова инструмента (tool call). Типичный пример — неэкранированные
двойные кавычки внутри строки:

```json
{"content": "Он сказал "привет" и ушел"}
```

### Где править

Нужно пропатчить **два** файла:

1. **Файл:** `<venv>/Lib/site-packages/kimi_cli/soul/toolset.py`
   - **Функция:** `handle()`
   - **Строка:** ~254 (вокруг `json.loads(tool_call.function.arguments or "{}", strict=False)`)

2. **Файл:** `<venv>/Lib/site-packages/kosong/contrib/chat_provider/openai_legacy.py`
   - **Функция:** `_convert_message()`
   - **Строка:** ~215 (вокруг `dumped_message = message.model_dump(exclude_none=True)`)

### Установить `json-repair`

```bash
<venv>/Scripts/pip install json-repair
```

### Патч `toolset.py` (ремонт при выполнении)

Оригинал:

```python
            try:
                arguments: JsonType = json.loads(tool_call.function.arguments or "{}", strict=False)
            except json.JSONDecodeError as e:
                logger.warning(
                    "Tool call JSON parse error: {tool_name} (call_id={call_id}): {error}",
                    tool_name=tool_call.function.name,
                    call_id=tool_call.id,
                    error=e,
                )
                return ToolResult(tool_call_id=tool_call.id, return_value=ToolParseError(str(e)))
```

Заменить на:

```python
            raw_args = tool_call.function.arguments or "{}"
            try:
                arguments: JsonType = json.loads(raw_args, strict=False)
            except json.JSONDecodeError:
                try:
                    import json_repair
                    repaired = json_repair.repair_json(raw_args, return_objects=False)
                    arguments = json.loads(repaired, strict=False)
                except Exception as e:
                    logger.warning(
                        "Tool call JSON parse error: {tool_name} (call_id={call_id}): {error}",
                        tool_name=tool_call.function.name,
                        call_id=tool_call.id,
                        error=e,
                    )
                    return ToolResult(tool_call_id=tool_call.id, return_value=ToolParseError(str(e)))
            except Exception as e:
                logger.warning(
                    "Tool call JSON parse error: {tool_name} (call_id={call_id}): {error}",
                    tool_name=tool_call.function.name,
                    call_id=tool_call.id,
                    error=e,
                )
                return ToolResult(tool_call_id=tool_call.id, return_value=ToolParseError(str(e)))
```

### Патч `openai_legacy.py` (ремонт перед отправкой к API)

Добавить `import json` в начало файла (если ещё нет), затем в `_convert_message`,
**перед** строкой `dumped_message = message.model_dump(exclude_none=True)`:

```python
        # --- Repair invalid JSON in tool_call arguments before sending to API ---
        if message.tool_calls:
            for tc in message.tool_calls:
                if tc.function.arguments:
                    try:
                        json.loads(tc.function.arguments, strict=False)
                    except json.JSONDecodeError:
                        try:
                            import json_repair
                            repaired = json_repair.repair_json(
                                tc.function.arguments, return_objects=False
                            )
                            json.loads(repaired, strict=False)  # verify
                            tc.function.arguments = repaired
                        except Exception:
                            pass  # leave as-is if repair fails
```

---

## Патч 2: Пустой массив `tools` при compaction (context сжатие)

### Симптом

```text
LLM provider error: Error code: 400 - {'error': {'message': '1 validation error:
  Value error, `tools` must not be an empty array. Either provide at least one tool or omit the field entirely.'}}
```

**Почему так происходит:** когда контекст становится большим, kimi-cli вызывает
`kosong.step()` с `EmptyToolset()` (нет инструментов) для compaction (сжатия)
истории. OpenAI SDK отправляет `tools=[]` в API, а локальный vLLM-сервер
отвергает пустой массив.

### Где править

Нужно пропатчить **два** файла:

1. **Файл:** `<venv>/Lib/site-packages/kosong/contrib/chat_provider/openai_legacy.py`
   - **Строка:** ~146 (внутри `generate()`)

2. **Файл:** `<venv>/Lib/site-packages/kosong/chat_provider/kimi.py`
   - **Строка:** ~173 (внутри `generate()`)

### Патч `openai_legacy.py`

Оригинал:

```python
                tools=(tool_to_openai(tool) for tool in tools),
```

Заменить на:

```python
                tools=(tool_to_openai(tool) for tool in tools) if tools else omit,
```

(`omit` уже импортирован в этом файле.)

### Патч `kimi.py`

Оригинал:

```python
                tools=(_convert_tool(tool) for tool in tools),
```

Заменить на:

```python
                tools=(_convert_tool(tool) for tool in tools) if tools else omit,
```

(`omit` уже импортирован в этом файле.)

---

## Патч 3: Сброс шрифта Consolas в Windows после Shell tool

### Симптом

После выполнения любого subprocess через `Shell tool` в Windows (cmd/PowerShell)
консольный шрифт Consolas сбрасывается в растровый шрифт. Все не-ASCII символы
(кириллица, emoji) становятся **невидимыми** (пустые клетки). Изменение
`chcp 65001` не помогает, потому что проблема в шрифте, а не в кодировке.

Описано в upstream issue [#2197](https://github.com/MoonshotAI/kimi-cli/issues/2197).

### Причина и решение

Windows при создании subprocess с pipe'ами на stdin/stdout/stderr изменяет
настройки родительской консоли. Фикс: передать `creationflags=subprocess.CREATE_NO_WINDOW`,
чтобы открепить дочерний процесс от родительской консоли.

### Где править

**Файл:** `<venv>/Lib/site-packages/kaos/local.py`
- **Функция:** `LocalKaos.exec()`
- **Строка:** ~165

Вставить перед `asyncio.create_subprocess_exec(...)`:

```python
        import subprocess

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW

        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            creationflags=creationflags,
        )
```

**Статус:** Этот патч **уже применён** в текущей установке (`kaos/local.py` строка 173 содержит `CREATE_NO_WINDOW`).

---

## Патч 4: OpenAI Legacy — `reasoning_key` (reasoning content)

### Симптом

При использовании vLLM/sglang модель иногда возвращает ответ, состоящий только
из reasoning/thinking content, без обычного текста. `openai_legacy` провайдер
его отбрасывает → `APIEmptyResponseError("The API returned an empty response.")`.

Описано в upstream issue [#1155](https://github.com/MoonshotAI/kimi-cli/issues/1155).

### Решение

В `kimi-cli ≥ 1.41+` поле `reasoning_key` уже поддерживается в провайдере
`openai_legacy`. В `~/.kimi/config.toml`:

```toml
[providers.vllm]
type = "openai_legacy"
base_url = "http://localhost:8000/v1"
api_key = "dummy"
reasoning_key = "reasoning_content"
```

По умолчанию `reasoning_key = "reasoning_content"`. Пустая строка `""` отключает
round-tripping reasoning.

**Статус:** Этот функционал **уже присутствует** в текущей установке
(`llm.py` строки 158–167 + `config.py` строка 48).

---

## Патч 5: Лимит размера AGENTS.md (32 KiB → 150 KiB)

### Симптом

Большие файлы `AGENTS.md` (выше 32 KiB) автоматически обрезаются при загрузке в системный промпт. Это приводит к потере критических инструкций в нижней части файла (например, правил безопасности, каталога дашбордов, чек-листов агента).

В логах `~/.kimi/logs/kimi.log`:

```text
WARNING | kimi_cli.soul.agent:load_agents_md:158 | AGENTS.md truncated due to size limit: ...
```

### Где править

**Файл:** `<venv>/Lib/site-packages/kimi_cli/soul/agent.py`
- **Строка:** 68

### Патч

Оригинал:

```python
_AGENTS_MD_MAX_BYTES = 32 * 1024  # 32 KiB
```

Заменить на:

```python
_AGENTS_MD_MAX_BYTES = 150 * 1024  # 150 KiB
```

**Статус:** Этот патч **уже применён** в текущей установке (`agent.py` строка 68 содержит `150 * 1024`).

---

## Патч 6: reasoning_effort "high" → "xhigh" (совместимость с vLLM/Gateway)

### Симптом

```text
LLM provider error: Error code: 400 - {'error': {'message': 'Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low.', 'type': 'BadRequestError', 'param': None, 'code': 400}}
```

**Почему так происходит:** kimi-cli хардкодит `reasoning_effort="high"` при
включённом thinking (`llm.py:247`). Кастомный vLLM/LLM Gateway не принимает
`"high"`, но принимает `"xhigh"` (дефолт), `"medium"`, `"low"`.

### Где править

**Файл:** `<venv>/Lib/site-packages/kosong/chat_provider/openai_common.py`
- **Функция:** `thinking_effort_to_reasoning_effort()`
- **Строка:** ~126

### Патч

Оригинал:

```python
        case "high":
            return "high"
```

Заменить на:

```python
        case "high":
            return "xhigh"
```

**Затрагивает:** только провайдеры `openai_legacy` и `openai_responses`.
Anthropic и Kimi провайдеры имеют собственный маппинг и не затронуты.

**Статус:** Этот патч **уже применён** в текущей установке (локальная + сервер).

### Пути к файлу на разных машинах

| Машина | Путь к `openai_common.py` |
|--------|--------------------------|
| Локальная (Windows) | `<venv>/Lib/site-packages/kosong/chat_provider/openai_common.py` |
| Сервер (Linux, pipx) | `~/.local/share/pipx/venvs/kimi-cli/lib/python3.13/site-packages/kosong/chat_provider/openai_common.py` |

> ⚠️ На сервере kimi-cli установлен через **pipx**, а не pip — путь к venv отличается.

---

## Патч 7 (workaround): Повреждённая сессия из-за потери tool response

### Симптом

При memory pressure (высокая нагрузка на сервер/систему) CLI может потерять
tool response. После перезапуска и попытки восстановить сессию:

```text
LLM provider error: Error code: 400 - {'error': {
  'message': "an assistant message with 'tool_calls' must be followed by tool messages
  responding to each 'tool_call_id'. The following tool_call_ids did not have
  response messages: Shell:206",
  'type': 'invalid_request_error'
}}
```

Описано в upstream issue [#2336](https://github.com/MoonshotAI/kimi-cli/issues/2336).

### Workaround (ручное восстановление)

На данный момент в upstream нет автоматического фикса. Единственный способ
восстановить сессию — удалить poisoned assistant message из `context.jsonl`.

```bash
# 1. Найти сессию (ID виден в логе kimi.log или в заголовке окна)
ls ~/.kimi/sessions/<workdir_hash>/

# 2. Открыть context.jsonl и найти последний assistant message с tool_calls,
#    у которого НЕТ следующих tool messages с соответствующими tool_call_id.
#    Удалить этот assistant message целиком (строку JSON).

# 3. Сохранить и перезапустить kimi.
```

Если сессия не критично важна, проще удалить её целиком:

```bash
rm -rf ~/.kimi/sessions/<workdir_hash>/<session_id>
```

---

## Удаление повреждённой сессии (общий случай) {#delete-session}

Если сессия уже "заболела" (все запросы падают с 400 по любой из причин выше):

```bash
rm -rf ~/.kimi/sessions/<workdir_hash>/<session_id>
```

ID сессии виден в логе `kimi.log` или в заголовке окна.

---

## Перезапуск

После всех патчей перезапустить `kimi`, чтобы Python перезагрузил модули.

---

## Проверка: всё ли на месте?

Быстрая проверка всех патчей в текущей установке:

```bash
# 1. json_repair в toolset.py
grep -n "json_repair" <venv>/Lib/site-packages/kimi_cli/soul/toolset.py

# 2. json_repair в openai_legacy.py
grep -n "json_repair" <venv>/Lib/site-packages/kosong/contrib/chat_provider/openai_legacy.py

# 3. empty tools fix
grep -n "else omit" <venv>/Lib/site-packages/kosong/contrib/chat_provider/openai_legacy.py
grep -n "else omit" <venv>/Lib/site-packages/kosong/chat_provider/kimi.py

# 4. CREATE_NO_WINDOW
grep -n "CREATE_NO_WINDOW" <venv>/Lib/site-packages/kaos/local.py

# 5. reasoning_key
grep -n "reasoning_key" <venv>/Lib/site-packages/kimi_cli/llm.py

# 6. AGENTS.md size limit
grep -n "150 \* 1024" <venv>/Lib/site-packages/kimi_cli/soul/agent.py

# 7. reasoning_effort high → xhigh
grep -n "return \"xhigh\"" <venv>/Lib/site-packages/kosong/chat_provider/openai_common.py
```

Если какая-то строка не найдена — значит, после обновления `kimi-cli` патч
сбросился и нужно применить его заново по инструкции выше.

---

## Ссылки на upstream issue

| Проблема | Issue | Статус | Патч в коде? |
|----------|-------|--------|--------------|
| Invalid tool call corrupt the whole session (malformed JSON в tool_call arguments) | [#2165](https://github.com/MoonshotAI/kimi-cli/issues/2165) | 🔴 open | ✅ Применён вручную (toolset.py + openai_legacy.py) |
| Empty `tools` array during `/compact` с vLLM | [#2233](https://github.com/MoonshotAI/kimi-cli/issues/2233) | 🔴 open | ✅ Применён вручную (openai_legacy.py + kimi.py) |
| Windows console TrueType font reset после subprocess | [#2197](https://github.com/MoonshotAI/kimi-cli/issues/2197) | 🔴 open | ✅ Уже встроен в `kaos/local.py` |
| OpenAI Legacy — `reasoning_key` для reasoning content | [#1155](https://github.com/MoonshotAI/kimi-cli/issues/1155) | 🔴 open | ✅ Уже встроен в `llm.py` + `config.py` |
| AGENTS.md truncated to 32 KiB (теряется нижняя часть файла) | — | 🔴 open | ✅ Применён вручную (agent.py: `_AGENTS_MD_MAX_BYTES`) |
| reasoning_effort "high" не принимается vLLM/Gateway | — | 🔴 open | ✅ Применён вручную (openai_common.py: high→xhigh) |
| Session corruption under memory pressure (потеря tool response) | [#2336](https://github.com/MoonshotAI/kimi-cli/issues/2336) | 🔴 open | ❌ Нет автоматического фикса, workaround выше |

> 💡 **Ставь 👍 на эти issue**, чтобы разработчики увидели актуальность.
> В #2165 уже есть предложенные патчи от сообщества.

---

## Важные замечания

- `json_repair.repair_json(..., return_objects=False)` возвращает **строку**.
- **После каждого `pip install --upgrade kimi-cli` ВСЕ файлы**
  (`toolset.py`, `openai_legacy.py`, `kimi.py`) перезаписываются на оригинальные —
  **патчи 1 и 2** нужно накладывать заново.
- **Патчи 3 и 4** (CREATE_NO_WINDOW, reasoning_key) входят в upstream
  `kaos`/`kimi_cli`, поэтому обычно сохраняются при обновлении, но лучше
  перепроверить командой из секции «Проверка».
- **Патч 6** (reasoning_effort high→xhigh) — одна строка в `openai_common.py`,
  перезаписывается при обновлении `kosong`.
