"""Explicit hardware operations; no LLM, ASR, TTS or automatic outbound calls."""

import argparse
import json
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from .sim7600 import ATTransport, ModemError, Sim7600, discover_ports
from .sim7600_audio import PCMAudio


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="SIM7600 standalone hardware test")
    parser.add_argument("--at-port")
    parser.add_argument("--audio-port")
    sub = parser.add_subparsers(dest="operation", required=True)
    for name in ("ports", "status", "calls", "hangup"):
        sub.add_parser(name)
    watch = sub.add_parser("watch")
    watch.add_argument("--seconds", type=float, default=30)
    for name in ("dial", "answer"):
        command = sub.add_parser(name)
        if name == "dial":
            command.add_argument("number")
        command.add_argument("--seconds", type=float, default=30)
        command.add_argument("--connect-timeout", type=float, default=60)
        command.add_argument("--record", type=Path)
        command.add_argument("--play", type=Path)
    args = parser.parse_args()
    if hasattr(args, "seconds") and not 0 < args.seconds <= 3600:
        parser.error("--seconds must be between 0 and 3600")
    if hasattr(args, "connect_timeout") and not 0 < args.connect_timeout <= 180:
        parser.error("--connect-timeout must be between 0 and 180")
    try:
        if args.operation == "ports":
            emit(discover_ports())
            return 0
        if getattr(args, "play", None):
            PCMAudio.validate_wav(args.play)
        if getattr(args, "record", None):
            if args.record.exists() or not args.record.parent.is_dir():
                raise ValueError("Recording needs an existing directory and a new filename")
        with ATTransport(args.at_port) as at:
            modem = Sim7600(at)
            if args.operation == "status":
                emit(modem.health())
            elif args.operation == "calls":
                emit([asdict(c) for c in modem.calls()])
            elif args.operation == "hangup":
                modem.hangup()
                modem.usb_audio(False)
                emit({"hangup": True})
            elif args.operation == "watch":
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline:
                    try:
                        emit(at.events.get(timeout=0.2))
                    except queue.Empty:
                        pass
            else:
                # Do not touch an existing call unless explicitly answering a ringing call.
                calls = modem.calls()
                if args.operation == "dial" and calls:
                    raise ModemError("A call already exists")
                if args.operation == "answer" and not any(c.status == 4 for c in calls):
                    raise ModemError("No ringing incoming call")
                audio = None
                try:
                    if args.operation == "dial":
                        modem.dial(args.number)
                    else:
                        modem.answer()
                    modem.wait_active(args.connect_timeout)
                    emit({"call": "active"})
                    if args.record or args.play:
                        audio = PCMAudio(args.audio_port)
                        modem.usb_audio(True)
                    deadline = time.monotonic() + args.seconds
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        jobs = []
                        if args.record:
                            jobs.append(pool.submit(audio.record, args.record, args.seconds))
                        if args.play:
                            jobs.append(pool.submit(audio.play, args.play))
                        try:
                            while time.monotonic() < deadline:
                                for job in jobs:
                                    if job.done() and job.exception():
                                        raise job.exception()
                                if not any(c.status == 0 for c in modem.calls()):
                                    break
                                time.sleep(0.3)
                        finally:
                            if audio:
                                audio.cancel()
                        for job in jobs:
                            emit(job.result(timeout=3))
                finally:
                    if audio:
                        audio.close()
                    for cleanup in (modem.hangup, lambda: modem.usb_audio(False)):
                        try:
                            cleanup()
                        except (ModemError, OSError) as exc:
                            print(
                                f"Cleanup failed: {exc}; check call state manually", file=sys.stderr
                            )
        return 0
    except (ModemError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
