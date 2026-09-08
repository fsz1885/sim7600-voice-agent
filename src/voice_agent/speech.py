"""Lazy CPU speech engines and bounded PCM WAV decoding."""

import io
import threading
import wave
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

ASR_FOLDER = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
TTS_FOLDER = "matcha-icefall-zh-baker"
MAX_AUDIO_BYTES = 6_000_000


def decode_wav(data: bytes):
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError("录音过大，请将每段控制在 30 秒以内")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate, channels, width, frames = (
                wav.getframerate(),
                wav.getnchannels(),
                wav.getsampwidth(),
                wav.getnframes(),
            )
            if channels != 1 or width != 2 or not 8000 <= rate <= 48000:
                raise ValueError("需要 8–48 kHz、单声道、16-bit PCM WAV")
            if not 0.15 <= frames / rate <= 30:
                raise ValueError("每段录音需要在 0.15–30 秒之间")
            raw = wav.readframes(frames)
            if len(raw) != frames * 2:
                raise ValueError("录音文件不完整")
    except (wave.Error, EOFError) as exc:
        raise ValueError("无法读取 PCM WAV 录音") from exc
    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768
    # Energy gate only, not a learned VAD or a guarantee against noise hallucination.
    window = max(1, rate // 50)
    usable = samples[: len(samples) // window * window].reshape(-1, window)
    voiced = np.sqrt(np.mean(usable**2, axis=1)) > 0.008
    if voiced.sum() * 0.02 < 0.12:
        raise ValueError("未检测到足够语音，请靠近麦克风重录")
    if rate != 16000:
        factor = gcd(rate, 16000)
        samples = resample_poly(samples, 16000 // factor, rate // factor).astype(np.float32)
    return samples, 16000


class LocalSpeech:
    def __init__(self, folder: Path, threads=2):
        self.folder, self.threads = folder, threads
        self.asr = self.tts = None
        self.lock = threading.Lock()

    def availability(self):
        return {
            "asr": all(
                (self.folder / ASR_FOLDER / p).is_file() for p in ("model.int8.onnx", "tokens.txt")
            ),
            "tts": all(
                (self.folder / TTS_FOLDER / p).is_file()
                for p in ("model-steps-3.onnx", "tokens.txt", "lexicon.txt")
            )
            and (self.folder / "vocos-22khz-univ.onnx").is_file(),
        }

    def transcribe(self, data: bytes) -> str:
        samples, rate = decode_wav(data)
        with self.lock:
            import sherpa_onnx as so

            if not self.availability()["asr"]:
                raise ValueError("ASR 模型尚未安装，请运行语音模型安装脚本")
            if self.asr is None:
                folder = self.folder / ASR_FOLDER
                self.asr = so.OfflineRecognizer.from_sense_voice(
                    model=str(folder / "model.int8.onnx"),
                    tokens=str(folder / "tokens.txt"),
                    language="zh",
                    use_itn=True,
                    num_threads=self.threads,
                    provider="cpu",
                )
            stream = self.asr.create_stream()
            stream.accept_waveform(rate, samples)
            self.asr.decode_stream(stream)
            text = stream.result.text.strip()
            if not text:
                raise ValueError("没有识别到文字，请重录或输入文字")
            return text

    def synthesize(self, text: str) -> bytes:
        if not text.strip() or len(text) > 400:
            raise ValueError("回复长度超出语音合成限制")
        with self.lock:
            import sherpa_onnx as so

            if not self.availability()["tts"]:
                raise ValueError("TTS 模型尚未安装，文字回复仍可使用")
            if self.tts is None:
                folder = self.folder / TTS_FOLDER
                cfg = so.OfflineTtsConfig(
                    model=so.OfflineTtsModelConfig(
                        matcha=so.OfflineTtsMatchaModelConfig(
                            acoustic_model=str(folder / "model-steps-3.onnx"),
                            vocoder=str(self.folder / "vocos-22khz-univ.onnx"),
                            tokens=str(folder / "tokens.txt"),
                            lexicon=str(folder / "lexicon.txt"),
                        ),
                        num_threads=self.threads,
                        provider="cpu",
                    ),
                    rule_fsts=",".join(
                        str(folder / f"{n}.fst")
                        for n in ("phone", "date", "number")
                        if (folder / f"{n}.fst").is_file()
                    ),
                )
                if not cfg.validate():
                    raise ValueError("TTS 模型配置不完整")
                self.tts = so.OfflineTts(cfg)
            audio = self.tts.generate(text, sid=0, speed=1.0)
            samples = np.asarray(audio.samples)
            if not len(samples) or not np.isfinite(samples).all():
                raise ValueError("语音合成未返回有效音频")
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(audio.sample_rate)
                wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
            return output.getvalue()
