"""Bounded full-duplex PCM serial capture and paced playback, no sound-card dependency."""

import threading
import time
import wave
from pathlib import Path

from .sim7600 import ModemError, select_port


class PCMAudio:
    sample_rate = 8000
    frame_bytes = 320  # 20 ms, mono signed little-endian PCM16

    def __init__(self, port=None, serial_factory=None):
        if serial_factory is None:
            import serial

            serial_factory = serial.Serial
        self.serial = serial_factory(
            port or select_port("audio"), 115200, timeout=0.1, write_timeout=1
        )
        self.stop = threading.Event()
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()

    def record(self, path, seconds):
        if not 0 < seconds <= 3600:
            raise ValueError("Recording duration must be 0–3600 seconds")
        path = Path(path)
        total, remainder = 0, b""
        deadline = time.monotonic() + seconds
        with self._read_lock, path.open("xb") as file, wave.open(file, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            while not self.stop.is_set() and time.monotonic() < deadline:
                chunk = self.serial.read(3200)
                if not chunk:
                    continue
                chunk = remainder + chunk
                end = len(chunk) // 2 * 2
                wav.writeframesraw(chunk[:end])
                total += end
                remainder = chunk[end:]
                if total >= int(seconds * 16000):
                    break
        if not total:
            raise ModemError("No downlink PCM received; WAV contains no audio")
        return {
            "bytes": total,
            "audio_seconds": total / 16000,
            "discarded_trailing_bytes": len(remainder),
        }

    @staticmethod
    def validate_wav(path):
        with wave.open(str(path), "rb") as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getcomptype()) != (
                1,
                2,
                8000,
                "NONE",
            ):
                raise ValueError("Playback requires 8 kHz mono PCM16 WAV; convert explicitly first")
            if not 0 < wav.getnframes() <= 8000 * 3600:
                raise ValueError("Playback WAV must contain 0–3600 seconds of audio")

    def play(self, path):
        self.validate_wav(path)
        total = 0
        with self._write_lock, wave.open(str(path), "rb") as wav:
            while not self.stop.is_set():
                chunk = wav.readframes(160)
                if not chunk:
                    break
                start, offset = time.monotonic(), 0
                while offset < len(chunk) and not self.stop.is_set():
                    written = self.serial.write(chunk[offset:])
                    if not written:
                        raise ModemError("PCM serial write made no progress")
                    offset += written
                total += offset
                # Never burst to catch up after slow writes.
                self.stop.wait(max(0, len(chunk) / 16000 - (time.monotonic() - start)))
        return {"bytes": total, "audio_seconds": total / 16000}

    def cancel(self):
        self.stop.set()
        try:
            self.serial.reset_output_buffer()
        except OSError:
            pass

    def close(self):
        self.cancel()
        self.serial.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
