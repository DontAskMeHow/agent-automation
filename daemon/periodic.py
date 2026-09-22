"""Периодические задачи со случайным интервалом (демон + Планировщик задач Windows).

  python periodic.py install --name demo --min-hours 4 --max-hours 5 --command "<команда>"
  python periodic.py status --name demo
  python periodic.py once --name demo
  python periodic.py stop --name demo
  python periodic.py uninstall --name demo
  python periodic.py daemon --name demo             # вызывается задачей Планировщика

Демон: сразу первый проход, затем цикл «случайный интервал из [min; max]
часов → проход», сон шагами по 10 минут (проверка файла остановки).
Параметры задачи (команда, интервалы) хранятся в periodic-<name>.task.json,
поэтому в Планировщик уходит простая команда без вложенных кавычек.
"""

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHECK_STEP = 600  # шаг сна демона, с (10 минут)
LOG_LIMIT = 512 * 1024
NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _files(name):
    return {
        "task": ROOT / f"periodic-{name}.task.json",
        "log": ROOT / f"periodic-{name}.log",
        "status": ROOT / f"periodic-{name}.status.json",
        "stop": ROOT / f"periodic-{name}.stop",
    }


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fix_console():
    import tempfile

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8", errors="replace"))
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def log(name, msg):
    line = f"[{_now()}] {msg}"
    f = _files(name)["log"]
    with f.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    try:
        if f.stat().st_size > LOG_LIMIT:
            text = f.read_text(encoding="utf-8")
            f.write_text(text[-LOG_LIMIT // 2 :], encoding="utf-8")
    except Exception:
        pass
    if sys.stdout is not None:
        print(line, flush=True)


def read_task(name):
    f = _files(name)["task"]
    if not f.exists():
        raise SystemExit(f"Задача «{name}» не установлена (нет {f.name}). Сначала: install")
    return json.loads(f.read_text(encoding="utf-8"))


def write_status(name, **kw):
    f = _files(name)["status"]
    data = {}
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    data.update(kw)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_alive(pid):
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, creationflags=NO_WINDOW,
        ).stdout
        return str(pid) in out
    except Exception:
        return False


def _split_command(command):
    """Разбить командную строку как Windows CommandLineToArgvW (без cmd).

    В отличие от shlex: кавычки снимаются, бэкслеши в путях остаются
    буквальными (`D:\\tools` не превращается в `D:<tab>ools`).
    """
    if sys.platform != "win32":
        return shlex.split(command)
    args, cur, quoted, bs = [], [], False, 0
    for ch in command:
        if ch == "\\":
            bs += 1
            continue
        if ch == '"':
            cur.append("\\" * (bs // 2))
            if bs % 2 == 0:
                quoted = not quoted
            else:
                cur.append('"')
            bs = 0
            continue
        cur.append("\\" * bs)
        bs = 0
        if ch in " \t\n" and not quoted:
            s = "".join(cur)
            if s:
                args.append(s)
            cur = []
        else:
            cur.append(ch)
    cur.append("\\" * bs)
    s = "".join(cur)
    if s:
        args.append(s)
    return args


def run_pass(name, command, timeout_min):
    log(name, f"запуск: {command}")
    t0 = time.time()
    try:
        # Без shell: по таймауту убиваем дерево прохода (taskkill /T), а не
        # только cmd-оболочку — иначе python прохода осиротел бы и продолжал
        # работать мимо демона.
        proc = subprocess.Popen(
            _split_command(command),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=NO_WINDOW,
        )
    except Exception as e:
        log(name, f"проход завершён: ошибка запуска: {e}")
        return f"ошибка запуска: {e}"
    try:
        out, _ = proc.communicate(timeout=timeout_min * 60)
        lines = [l for l in out.strip().splitlines() if l.strip()]
        joined = " | ".join(lines)
        tail = joined if len(joined) <= 1500 else " | ".join(lines[:2]) + " … " + " | ".join(lines[-2:])
        result = f"exit={proc.returncode}" + (f"; {tail}" if tail else "")
    except subprocess.TimeoutExpired:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, creationflags=NO_WINDOW,
        )
        out, _ = proc.communicate()
        result = f"таймаут после {timeout_min} мин"
    log(name, f"проход завершён за {time.time() - t0:.0f} с: {result}")
    return result


def cmd_daemon(name, task):
    write_status(name, pid=os.getpid(), started_at=_now(), command=task["command"])
    log(name, f"демон запущен (pid {os.getpid()})")
    stopf = _files(name)["stop"]
    passes = 0
    while True:
        result = run_pass(name, task["command"], task.get("timeout_min", 30))
        passes += 1
        interval = random.uniform(task["min_hours"] * 3600, task["max_hours"] * 3600)
        next_at = datetime.now() + timedelta(seconds=interval)
        write_status(
            name,
            last_run_at=_now(), last_result=result, passes=passes,
            next_run_at=next_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        log(name, f"следующий проход: {next_at:%Y-%m-%d %H:%M} (интервал {interval / 3600:.2f} ч)")
        while True:
            if stopf.exists():
                stopf.unlink()
                log(name, "остановлен командой stop")
                write_status(name, stopped_at=_now(), next_run_at=None)
                return
            remaining = (next_at - datetime.now()).total_seconds()
            if remaining <= 0:
                break
            time.sleep(min(CHECK_STEP, max(1, remaining)))


def cmd_install(name, command, min_hours, max_hours, timeout_min):
    task = {
        "name": name, "command": command,
        "min_hours": min_hours, "max_hours": max_hours, "timeout_min": timeout_min,
    }
    _files(name)["task"].write_text(
        json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pythonw = Path(sys.executable)
    script = Path(__file__).resolve()
    ps = f"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$action = New-ScheduledTaskAction -Execute '{pythonw}' -Argument '\"{script}\" daemon --name {name}' -WorkingDirectory '{script.parent}'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User \"$env:USERNAME\"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan)
Register-ScheduledTask -TaskName 'periodic-{name}' -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'periodic-{name}'
"""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=NO_WINDOW,
    )
    if proc.returncode != 0:
        raise SystemExit(f"Не удалось зарегистрировать задачу: {proc.stderr.strip()}")
    log(name, f"задача periodic-{name} зарегистрирована (при входе в систему) и запущена")
    log(name, f"демон: {pythonw} \"{script}\" daemon --name {name}; команда прохода: {command}")


def cmd_uninstall(name):
    for args in (["schtasks", "/End", "/TN", f"periodic-{name}"],
                 ["schtasks", "/Delete", "/TN", f"periodic-{name}", "/F"]):
        subprocess.run(args, capture_output=True, creationflags=NO_WINDOW)
    f = _files(name)
    for key in ("stop", "task"):
        try:
            f[key].unlink()
        except FileNotFoundError:
            pass
    log(name, f"задача periodic-{name} удалена (лог оставлен: {f['log'].name})")


def cmd_status(name):
    task = read_task(name)
    f = _files(name)
    status = {}
    if f["status"].exists():
        try:
            status = json.loads(f["status"].read_text(encoding="utf-8"))
        except Exception:
            status = {}
    alive = _pid_alive(status.get("pid"))
    print(f"Задача periodic-{name}: {'демон работает' if alive else 'демон не запущен'}")
    print(f"Команда: {task['command']}")
    print(f"Интервал: {task['min_hours']}–{task['max_hours']} ч (случайный), шаг сна {CHECK_STEP // 60} мин")
    if status.get("last_run_at"):
        print(f"Последний проход: {status['last_run_at']} — {status.get('last_result')}")
    if status.get("next_run_at"):
        print(f"Следующий проход: {status['next_run_at']}")
    if f["log"].exists():
        lines = f["log"].read_text(encoding="utf-8").strip().splitlines()
        print("\nПоследние записи лога:")
        for line in lines[-15:]:
            print("  " + line)


def main():
    _fix_console()
    ap = argparse.ArgumentParser(description="Периодические задачи со случайным интервалом")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("install", help="установить задачу (Планировщик + файл задачи)")
    p.add_argument("--name", required=True)
    p.add_argument("--command", required=True, help='команда одного прохода, например "python demo_pass.py"')
    p.add_argument("--min-hours", type=float, default=4)
    p.add_argument("--max-hours", type=float, default=5)
    p.add_argument("--timeout-min", type=int, default=30, help="лимит одного прохода, мин")

    p = sub.add_parser("uninstall", help="остановить и удалить задачу")
    p.add_argument("--name", required=True)

    p = sub.add_parser("daemon", help="цикл демона (вызывается Планировщиком)")
    p.add_argument("--name", required=True)

    p = sub.add_parser("once", help="одиночный проход сейчас")
    p.add_argument("--name", required=True)

    p = sub.add_parser("status", help="состояние и хвост лога")
    p.add_argument("--name", required=True)

    p = sub.add_parser("stop", help="мягкая остановка демона (в пределах шага сна)")
    p.add_argument("--name", required=True)

    args = ap.parse_args()

    if args.cmd == "install":
        if args.max_hours < args.min_hours:
            raise SystemExit("--max-hours не может быть меньше --min-hours")
        cmd_install(args.name, args.command, args.min_hours, args.max_hours, args.timeout_min)
    elif args.cmd == "uninstall":
        cmd_uninstall(args.name)
    elif args.cmd == "daemon":
        cmd_daemon(args.name, read_task(args.name))
    elif args.cmd == "once":
        task = read_task(args.name)
        result = run_pass(args.name, task["command"], task.get("timeout_min", 30))
        write_status(args.name, last_run_at=_now(), last_result=result)
    elif args.cmd == "status":
        cmd_status(args.name)
    elif args.cmd == "stop":
        _files(args.name)["stop"].touch()
        log(args.name, "получен запрос на остановку (демон завершится в пределах 10 минут)")


if __name__ == "__main__":
    main()
