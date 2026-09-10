import queue
import threading
import time
import wave

import pytest

from voice_agent.sim7600 import ATResponse, ATTransport, Call, ModemError, Sim7600, select_port
from voice_agent.sim7600_audio import PCMAudio


class Serial:
    def __init__(self, replies=()):
        self.incoming = queue.Queue()
        self.replies = iter(replies)
        self.writes = []
        self.closed = False

    def read(self, size):
        try:
            value = self.incoming.get(timeout=0.01)
            if isinstance(value, Exception):
                raise value
            return value
        except queue.Empty:
            return b""

    def write(self, data):
        self.writes.append(data)
        for chunk in next(self.replies, ()):
            self.incoming.put(chunk)
        return len(data)

    def reset_output_buffer(self):
        pass

    def close(self):
        self.closed = True


def transport(serial):
    return ATTransport("test", serial_factory=lambda *args, **kwargs: serial)


def test_discovery_requires_unique_role():
    ports = [{"device": "COM77", "description": "Simcom HS-USB AT PORT 9001"}]
    assert select_port("at", ports) == "COM77"
    with pytest.raises(ModemError):
        select_port("audio", ports)
    with pytest.raises(ModemError):
        select_port("at", ports * 2)


def test_split_replies_and_interleaved_urcs_are_preserved():
    serial = Serial([[b"AT+CSQ\r\nRI", b"NG\r\n+CS", b"Q: 11,99\r\nOK\r\n"]])
    with transport(serial) as at:
        response = at.command("AT+CSQ", prefixes=("+CSQ:",))
        assert response.lines == ("+CSQ: 11,99",)
        assert at.events.get(timeout=0.1)["line"] == "RING"
    assert serial.closed


def test_timeout_prevents_late_ok_from_completing_next_command():
    serial = Serial()
    with transport(serial) as at:
        with pytest.raises(ModemError, match="timed out"):
            at.command("AT", timeout=0.03)
        serial.incoming.put(b"OK\r\n")
        with pytest.raises(ModemError, match="unavailable"):
            at.command("AT+CSQ")
        assert len(serial.writes) == 1


def test_disconnect_wakes_waiting_command():
    serial = Serial([[OSError("unplugged")]])
    with transport(serial) as at, pytest.raises(ModemError, match="reader stopped"):
        at.command("AT", timeout=1)


def test_unsolicited_hangup_is_visible_and_fails_pending_command():
    serial = Serial([[b"NO CARRIER\r\n"]])
    with transport(serial) as at:
        with pytest.raises(ModemError, match="NO CARRIER"):
            at.command("ATD12345;")
        assert at.events.get()["line"] == "NO CARRIER"


def test_command_injection_rejected_before_write():
    serial = Serial()
    with transport(serial) as at:
        with pytest.raises(ValueError):
            at.command("AT\r\nATD12345;")
    assert not serial.writes


class Commands:
    def __init__(self, registration="+CEREG: 0,1"):
        self.sent = []
        self.registration = registration

    def command(self, command, **kwargs):
        self.sent.append(command)
        lines = {
            "AT+CLCC": (),
            "AT+CPIN?": ("+CPIN: READY",),
            "AT+CEREG?": (self.registration,),
            "AT+CSQ": ("+CSQ: 11,99",),
            "AT+CPSI?": ("+CPSI: LTE,Online",),
        }.get(command, ())
        return ATResponse(command, lines, "OK")


@pytest.mark.parametrize(
    "registration,allowed",
    [("+CEREG: 0,1", True), ('+CEREG: 2,5,"A"', True), ("+CEREG: 0,3", False)],
)
def test_dial_registration_guard(registration, allowed):
    at = Commands(registration)
    modem = Sim7600(at)
    if allowed:
        modem.dial("12345")
        assert at.sent[-1] == "ATD12345;"
    else:
        with pytest.raises(ModemError):
            modem.dial("12345")
        assert not any(x.startswith("ATD") for x in at.sent)


def test_call_parsing_and_active_audio_guard():
    serial = Serial([[b'+CLCC: 1,0,3,0,0,"12345",129\r\nOK\r\n'], [b"OK\r\n"]])
    with transport(serial) as at:
        modem = Sim7600(at)
        assert modem.calls() == [Call(1, 0, 3, 0, 0)]
        with pytest.raises(ModemError, match="active"):
            modem.usb_audio(True)
        assert b"AT+CPCMREG=1\r\n" not in serial.writes


def test_pcm_record_preserves_odd_chunk_boundaries(tmp_path):
    serial = Serial()
    serial.incoming.put(b"\x01")
    serial.incoming.put(b"\x02\x03\x04")
    target = tmp_path / "capture.wav"
    with PCMAudio("test", serial_factory=lambda *a, **kw: serial) as audio:
        result = audio.record(target, 0.03)
    assert result["bytes"] == 4
    with wave.open(str(target), "rb") as wav:
        assert wav.getframerate() == 8000
        assert wav.readframes(2) == b"\x01\x02\x03\x04"


def test_playback_paced_and_cancelled(tmp_path):
    target = tmp_path / "play.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 8000)
    serial = Serial()
    with PCMAudio("test", serial_factory=lambda *a, **kw: serial) as audio:
        job = threading.Thread(target=audio.play, args=(target,))
        job.start()
        time.sleep(0.07)
        audio.cancel()
        job.join(timeout=1)
        assert not job.is_alive()
        assert 0 < len(serial.writes) < 20
        assert all(len(chunk) <= 320 for chunk in serial.writes)


def test_audio_rejects_overwrite_and_wrong_format(tmp_path):
    target = tmp_path / "wrong.wav"
    with wave.open(str(target), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 160)
    with pytest.raises(ValueError):
        PCMAudio.validate_wav(target)
    with PCMAudio("test", serial_factory=lambda *a, **kw: Serial()) as audio:
        with pytest.raises(FileExistsError):
            audio.record(target, 1)
