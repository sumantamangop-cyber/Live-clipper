"""Anonymous read-only Twitch chat over IRC: we only count messages per second, never store text."""
import asyncio
import random
import time

IRC_HOST = "irc.chat.twitch.tv"
IRC_PORT = 6667


def handle_line(line: str):
    """Classify one IRC line: ('pong', reply) for keepalives, ('msg', None) for chat, else (None, None)."""
    if line.startswith("PING"):
        return "pong", line.replace("PING", "PONG", 1)
    if " PRIVMSG #" in line:
        return "msg", None
    return None, None


class ChatCounter:
    """Keeps only the time (seconds since ``t0``) of each chat message."""

    def __init__(self, login: str, t0: float = None):
        self.login = login.lower()
        self.t0 = time.monotonic() if t0 is None else t0
        self.stamps = []
        self._task = None

    def on_line(self, line: str, now: float = None):
        kind, reply = handle_line(line)
        if kind == "msg":
            self.stamps.append((time.monotonic() if now is None else now) - self.t0)
        return reply

    async def run(self):
        nick = f"justinfan{random.randint(10000, 99999)}"  # anonymous, read-only login
        reader, writer = await asyncio.open_connection(IRC_HOST, IRC_PORT)
        writer.write(f"NICK {nick}\r\nJOIN #{self.login}\r\n".encode())
        await writer.drain()
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                reply = self.on_line(raw.decode("utf-8", "replace").strip())
                if reply:
                    writer.write((reply + "\r\n").encode())
                    await writer.drain()
        finally:
            writer.close()

    def start(self):
        self._task = asyncio.create_task(self.run())
        return self._task

    def stop(self):
        if self._task:
            self._task.cancel()
