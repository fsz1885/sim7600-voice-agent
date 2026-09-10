"""20ms PCM frame bridge with bounded queues and generation-based playback cancellation."""

import base64
import threading
import time
from collections import deque


class PCMBridge:
    def __init__(self, port=None, serial_factory=None):
        from .sim7600 import select_port

        if serial_factory is None:
            import serial

            serial_factory = serial.Serial
        self.serial = serial_factory(
            port or select_port("audio"), 115200, timeout=0.1, write_timeout=1
        )
        self.lock = threading.Lock()
        self.tx_lock = threading.Lock()
        self.rx = deque(maxlen=250)
        self.tx = deque()
        self.sequence = 0
        self.generation = 0
        self.sent_bytes = 0
        self.received_bytes = 0
        self.error = None
        self.closed = threading.Event()
        self.last_access = time.monotonic()
        self.threads = [
            threading.Thread(target=fn, daemon=True) for fn in (self._receive, self._transmit)
        ]
        for thread in self.threads:
            thread.start()

    def _receive(self):
        pending = b""
        try:
            while not self.closed.is_set():
                pending += self.serial.read(320)
                while len(pending) >= 320:
                    frame, pending = pending[:320], pending[320:]
                    with self.lock:
                        self.sequence += 1
                        self.received_bytes += len(frame)
                        self.rx.append((self.sequence, frame))
        except Exception as exc:
            if not self.closed.is_set():
                self.error = type(exc).__name__

    def _transmit(self):
        try:
            while not self.closed.is_set():
                with self.lock:
                    item = self.tx.popleft() if self.tx else None
                if item is None:
                    self.closed.wait(0.005)
                    continue
                generation, frame = item
                started = time.monotonic()
                with self.tx_lock:
                    if generation != self.generation:
                        continue
                    offset = 0
                    while offset < len(frame) and not self.closed.is_set():
                        count = self.serial.write(frame[offset:])
                        if not count:
                            raise OSError("No PCM write progress")
                        offset += count
                    self.sent_bytes += offset
                self.closed.wait(max(0, len(frame) / 16000 - (time.monotonic() - started)))
        except Exception as exc:
            if not self.closed.is_set():
                self.error = type(exc).__name__

    def read(self, after):
        self.last_access = time.monotonic()
        with self.lock:
            frames = [(seq, data) for seq, data in self.rx if seq > after]
            return {
                "frames": [
                    {"sequence": seq, "pcm": base64.b64encode(data).decode()}
                    for seq, data in frames
                ],
                "sequence": self.sequence,
                "generation": self.generation,
                "queued_frames": len(self.tx),
                "sent_bytes": self.sent_bytes,
                "received_bytes": self.received_bytes,
                "error": self.error,
            }

    def write(self, data, generation):
        if not data or len(data) % 2 or len(data) > 16000:
            raise ValueError("PCM payload must contain 1–8000 PCM16 samples")
        with self.lock:
            if generation != self.generation:
                raise ValueError("Stale playback generation")
            frames = [data[i : i + 320] for i in range(0, len(data), 320)]
            if len(self.tx) + len(frames) > 100:
                raise BufferError("Playback queue full")
            self.tx.extend((generation, frame) for frame in frames)

    def interrupt(self):
        with self.tx_lock, self.lock:
            self.generation += 1
            self.tx.clear()
            self.serial.reset_output_buffer()
            return self.generation

    def close(self):
        self.closed.set()
        try:
            self.interrupt()
        finally:
            self.serial.close()
            for thread in self.threads:
                thread.join(timeout=2)
