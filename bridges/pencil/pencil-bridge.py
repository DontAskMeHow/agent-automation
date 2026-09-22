"""Мост Kimi Code (MCP stdio, Content-Length-framing) <-> Pencil MCP-сервер (JSON-lines на stdin/stdout).

Сервер Pencil (mcp-server-windows-x64.exe --app visual_studio_code) принимает по одной
JSON-строке на сообщение без стандартного MCP-фрейминга, а Kimi отправляет сообщения
в стандартном фрейминге Content-Length. Мост переводит форматы в обе стороны и
перезапускает сервер, если тот умер (например, закрыли VS Code).

Протокол с kimi по stdout — только framed JSON; все логи — в stderr и лог-файл.
"""
import json
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "pencil-bridge.log")
SERVER = os.path.expanduser(r"~\.pencil\mcp\visual_studio_code\out\mcp-server-windows-x64.exe")

RESPAWN_DELAY = 3.0


def log(msg):
    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
        try:
            os.remove(LOG_PATH)
        except OSError:
            pass
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


class Bridge:
    def __init__(self):
        self.proc = None
        self.proc_lock = threading.Lock()
        self.proc_ready = threading.Event()
        self.shutdown = False

    # ---------- сервер ----------
    def spawn(self):
        if not os.path.exists(SERVER):
            raise RuntimeError(f"нет {SERVER}")
        # CREATE_NO_WINDOW (0x08000000) — без консольного окна
        proc = subprocess.Popen(
            [SERVER, "--app", "visual_studio_code"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=0x08000000,
        )
        with self.proc_lock:
            self.proc = proc
            self.proc_ready.set()
        log("сервер запущен")

    def server_loop(self):
        while not self.shutdown:
            try:
                self.spawn()
            except Exception as e:
                log(f"ошибка запуска: {e}")
                time.sleep(RESPAWN_DELAY)
                continue
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                self.frame_to_kimi(line)
            if self.shutdown:
                break
            log("сервер завершился — перезапуск")
            with self.proc_lock:
                self.proc = None
                self.proc_ready.clear()
            time.sleep(RESPAWN_DELAY)

    def forward(self, message):
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self.proc_lock:
            proc = self.proc
        if proc is None:
            # сервер ещё поднимается или на перезапуске — ждём до 10 с
            self.proc_ready.wait(10)
            with self.proc_lock:
                proc = self.proc
        if proc is None:
            raise RuntimeError("сервер не поднялся")
        proc.stdin.write(line + "\n")
        proc.stdin.flush()
        try:
            msg_id = str(message.get("id", ""))
        except Exception:
            msg_id = ""
        log(f"-> [{msg_id}] {message.get('method', '')} {line[:120]}")

    # ---------- kimi (stdin/stdout) ----------
    @staticmethod
    def frame_to_kimi(text):
        # Kimi Code на Windows говорит со stdio-MCP серверами JSON-lines
        # (проверено на живом ccc.exe mcp), а не Content-Length-фреймингом.
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
        log(f"<- {text[:120]}")

    def read_framed_message(self, stream):
        """Читает одно сообщение в Content-Length-фрейминге; терпимо и к JSON-lines."""
        line = stream.readline()
        if not line:
            return None
        line_str = line.decode("utf-8", "replace").strip()
        log(f">> {line_str[:100]}")
        if not line_str:
            return self.read_framed_message(stream)
        if line_str.lower().startswith("content-length:"):
            try:
                length = int(line_str.split(":", 1)[1].strip())
            except (IndexError, ValueError):
                return None
            while True:
                h = stream.readline()
                if not h or h.strip() == b"":
                    break
            chunks = []
            got = 0
            while got < length:
                b = stream.read(length - got)
                if not b:
                    break
                chunks.append(b)
                got += len(b)
            try:
                return json.loads(b"".join(chunks).decode("utf-8"))
            except json.JSONDecodeError:
                return None
        # JSON-lines (на случай клиента без фрейминга)
        try:
            return json.loads(line_str)
        except json.JSONDecodeError:
            return None

    # ---------- главный цикл ----------
    def run(self):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace", newline="")
        except Exception:
            pass
        server_thread = threading.Thread(target=self.server_loop, daemon=True)
        server_thread.start()
        try:
            while True:
                msg = self.read_framed_message(sys.stdin.buffer)
                if msg is None:
                    break  # kimi закрыл stdin
                if isinstance(msg, dict):
                    try:
                        self.forward(msg)
                    except Exception as e:
                        log(f"forward err: {e}")
                        # сервер мёртв — ответ-ошибка, чтобы kimi не завис
                        req_id = msg.get("id")
                        if req_id is not None:
                            err = {
                                "jsonrpc": "2.0",
                                "id": req_id,
                                "error": {"code": -32000, "message": f"pencil server unavailable: {e}"},
                            }
                            self.frame_to_kimi(json.dumps(err, ensure_ascii=False, separators=(",", ":")))
        finally:
            self.shutdown = True
            with self.proc_lock:
                proc = self.proc
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
            log("мост остановлен")


if __name__ == "__main__":
    Bridge().run()
