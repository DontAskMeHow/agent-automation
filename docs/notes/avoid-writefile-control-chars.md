# Безопасная запись больших файлов через kimi-cli

## Проблема

При записи файлов размером более ~8–10 KB с помощью инструмента `WriteFile`
периодически возникает ошибка:

```
LLM provider error: Error code: 400 - {
  'error': {
    'message': 'Invalid control character at: line 1 column 8150 (char 8149)',
    'type': 'BadRequestError',
    'param': None,
    'code': 400
  }
}
```

Иногда текст ошибки — `Expecting ',' delimiter` или `Unterminated string`.

## Корневая причина

LLM генерирует JSON-аргументы для `WriteFile` внутри `tool_calls`.  
Если содержимое поля `content` слишком велико, модель **обрывает (truncates)**
выход на лимите `max_tokens` или при backpressure.

При этом в JSON-строке `content` появляются **незакрытые кавычки** или
**неэкранированные управляющие символы** (например, raw `\n` вместо `\\n`).
Сервер OpenAI-совместимого провайдера отвергает такой JSON как содержащий
недопустимый control character (Unicode U+0000–U+001F внутри неэкранированной
JSON-строки).

> Проверено в `wire.jsonl`: `args_str` содержал 2 неэкранированных `\n` на
> позициях 8149 и 8192, что и привело к `Invalid control character`.

## Диагностика

Если ошибка возникла — перед повторной попыткой убедиться:

1. Файлы `*.py` не содержат реальных control characters:
   ```python
   import os
   for fn in os.listdir('.'):
       with open(fn, 'rb') as f:
           data = f.read()
       bad = [i for i, b in enumerate(data)
              if b < 32 and b not in (0x09, 0x0A, 0x0D)]
       if bad:
           print(fn, 'bad offsets:', bad[:5])
   ```

2. В `wire.jsonl` аргументы `tool_calls` действительно содержат
   unescaped newlines (raw `\n` внутри JSON-строки).

## Решения

### 1. Основной способ: шаблон + StrReplaceFile (рекомендуется)

Не писать большой файл за один `WriteFile`.  
Вместо этого:

1. Создать файл-заглушку минимального размера через `WriteFile`
   (или `Shell` с `touch`).
2. Последовательно добавлять секции через `StrReplaceFile`.

Пример:

```python
# Step 1 — WriteFile: пустая заготовка
with open('myfile.py', 'w') as f:
    f.write('# PLACEHOLDER\n')

# Step 2+ — StrReplaceFile: добавлять блоки по частям
# (каждый блок < 3000 символов)
```

### 2. Ограничение размера одного WriteFile

Если `WriteFile` используется — размер `content` должен быть **не более 6–7 KB**
(примерно 500–600 строк простого Python).  Для большего — обязательно переходить
к шагу 1 (placeholder + StrReplaceFile).

### 3. Fallback: Shell с heredoc

Если всё равно нужно записать большой файл с кириллицей за один шаг —
использовать `Shell` с heredoc вместо `WriteFile`:

```bash
cd /path/to/dir && cat > myfile.py << 'PYEOF'
# Здесь кириллица, юникод, любые символы —
# heredoc передаёт raw bytes, минуя JSON-serialization
PYEOF
```

## Правила на практике

- **Python-файлы с русскими docstrings > 300 строк** — разбивать на ≥2 части:
  заглушка, затем 2–3 `StrReplaceFile`.
- **Никогда не пытаться** записать `> 10 KB` через один `WriteFile`.
- При длинных файлах **сначала написать план** (placeholder-структуру),
  потом заполнять секции.
- Если `WriteFile` вернул `Invalid arguments` — не повторять вызов с тем же
  большим content; переключиться на `StrReplaceFile`.
