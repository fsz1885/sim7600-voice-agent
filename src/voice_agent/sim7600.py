"""SIM7600 AT control. Independent of speech engines, the web console and LLMs."""

import queue
import re
import threading
import time
from dataclasses import dataclass


class ModemError(RuntimeError):
    pass


def discover_ports():
    from serial.tools.list_ports import comports

    return [
        {"device": p.device, "description": p.description, "hwid": p.hwid}
        for p in comports()
        if p.vid == 0x1E0E or "simcom" in (p.description or "").lower()
    ]


def select_port(kind, ports=None):
    marker = {"at": "at port", "audio": "audio"}[kind]
    matches = [
        p["device"]
        for p in (discover_ports() if ports is None else ports)
        if marker in p["description"].lower()
    ]
    if len(matches) != 1:
        raise ModemError(f"Expected one {kind} port, found {matches}; specify the port explicitly")
    return matches[0]


@dataclass(frozen=True)
class ATResponse:
    command: str
    lines: tuple[str, ...]
    result: str


@dataclass(frozen=True)
class Call:
    id: int
    direction: int
    status: int
    mode: int
    multiparty: int


class ATTransport:
    """One serial reader, serialized commands, separate bounded unsolicited-event queue.

    A timeout poisons the connection: a late OK must never complete the next command.
    Reopen after a timeout; do not retry non-idempotent commands automatically.
    """

    def __init__(self, port=None, serial_factory=None):
        if serial_factory is None:
            import serial

            serial_factory = serial.Serial
        self.serial = serial_factory(
            port or select_port("at"), 115200, timeout=0.1, write_timeout=2
        )
        self.events = queue.Queue(maxsize=256)
        self.dropped_events = 0
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending = None
        self._prefixes = ()
        self._closed = threading.Event()
        self._fault = None
        self._thread = threading.Thread(target=self._read, name="sim7600-at", daemon=True)
        self._thread.start()

    def _event(self, line):
        try:
            self.events.put_nowait({"time": time.time(), "line": line})
        except queue.Full:
            self.dropped_events += 1

    def _read(self):
        buffer = bytearray()
        try:
            while not self._closed.is_set():
                chunk = self.serial.read(1024)
                if not chunk:
                    continue
                buffer.extend(chunk)
                if len(buffer) > 65536:
                    raise ModemError("AT input exceeded line buffer limit")
                while b"\n" in buffer:
                    raw, _, remainder = buffer.partition(b"\n")
                    buffer = bytearray(remainder)
                    line = raw.decode("ascii", errors="replace").strip()
                    if not line:
                        continue
                    with self._state_lock:
                        pending, prefixes = self._pending, self._prefixes
                        terminal = line in {"OK", "ERROR", "NO CARRIER", "BUSY", "NO ANSWER"}
                        terminal |= line.startswith(("+CME ERROR", "+CMS ERROR"))
                        urc = line.startswith(
                            (
                                "RING",
                                "+CRING:",
                                "+CLIP:",
                                "VOICE CALL:",
                                "NO CARRIER",
                                "BUSY",
                                "NO ANSWER",
                            )
                        )
                        if urc or (line.startswith("+") and not line.startswith(prefixes)):
                            self._event(line)
                        if pending is not None and (terminal or line.startswith(prefixes)):
                            pending.put((line, terminal))
                        elif not urc and not line.startswith(("AT", "+")):
                            self._event(line)
        except Exception as exc:
            self._fault = ModemError(f"AT reader stopped: {type(exc).__name__}")
            with self._state_lock:
                if self._pending is not None:
                    self._pending.put((self._fault, True))

    def command(self, command, *, prefixes=(), timeout=3):
        if not command.startswith("AT") or any(c in command for c in "\r\n\x00"):
            raise ValueError("Expected a single AT command")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._lock:
            if self._closed.is_set() or self._fault:
                raise ModemError("AT transport unavailable; close and reopen")
            replies = queue.Queue()
            with self._state_lock:
                self._pending, self._prefixes = replies, tuple(prefixes)
            try:
                self.serial.write((command + "\r\n").encode("ascii"))
                deadline, lines = time.monotonic() + timeout, []
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    line, terminal = replies.get(timeout=remaining)
                    if isinstance(line, Exception):
                        raise line
                    if terminal:
                        if line != "OK":
                            raise ModemError(f"AT command failed: {line}")
                        return ATResponse(command, tuple(lines), line)
                    lines.append(line)
            except queue.Empty as exc:
                self._fault = ModemError("AT command timed out; reopen before issuing commands")
                raise self._fault from exc
            except OSError as exc:
                self._fault = ModemError("AT write failed; reopen transport")
                raise self._fault from exc
            finally:
                with self._state_lock:
                    self._pending, self._prefixes = None, ()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        with self._state_lock:
            if self._pending is not None:
                self._pending.put((ModemError("AT transport closed"), True))
        self.serial.close()
        self._thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Sim7600:
    def __init__(self, transport):
        self.at = transport

    def health(self):
        self.at.command("AT")
        pin = self.at.command("AT+CPIN?", prefixes=("+CPIN:",)).lines
        signal = self.at.command("AT+CSQ", prefixes=("+CSQ:",)).lines
        registration = self.at.command("AT+CEREG?", prefixes=("+CEREG:",)).lines
        network = self.at.command("AT+CPSI?", prefixes=("+CPSI:",)).lines
        registered = any(
            re.match(r"\+CEREG:\s*\d+\s*,\s*[15](?:\s*,|\s*$)", x) for x in registration
        )
        return {
            "sim_ready": any(x == "+CPIN: READY" for x in pin),
            "registered": registered,
            "signal": signal,
            "registration": registration,
            "network": network,
        }

    def calls(self):
        response = self.at.command("AT+CLCC", prefixes=("+CLCC:",))
        result = []
        for line in response.lines:
            match = re.match(r"\+CLCC:\s*(\d+),(\d+),(\d+),(\d+),(\d+)", line)
            if not match:
                raise ModemError("Malformed CLCC response")
            result.append(Call(*(int(x) for x in match.groups())))
        return result

    def dial(self, number):
        if not re.fullmatch(r"\+?[0-9]{3,20}", number):
            raise ValueError("Phone number must contain 3–20 digits with optional leading +")
        if self.calls():
            raise ModemError("A call already exists")
        status = self.health()
        if not status["sim_ready"] or not status["registered"]:
            raise ModemError("SIM or LTE registration not ready")
        self.at.command(f"ATD{number};", timeout=15)

    def answer(self):
        if not any(c.status in {4, 5} for c in self.calls()):
            raise ModemError("No incoming call to answer")
        self.at.command("ATA", timeout=15)

    def hangup(self):
        self.at.command("AT+CHUP", timeout=5)

    def wait_active(self, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            calls = self.calls()
            if any(c.status == 0 and c.mode == 0 for c in calls):
                return
            if not calls:
                raise ModemError("Call ended before connection")
            time.sleep(0.3)
        raise ModemError("Timed out waiting for call connection")

    def usb_audio(self, enabled):
        if enabled and not any(c.status == 0 and c.mode == 0 for c in self.calls()):
            raise ModemError("USB audio requires an active voice call")
        self.at.command(f"AT+CPCMREG={int(bool(enabled))}")
