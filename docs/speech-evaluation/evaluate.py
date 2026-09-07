"""Local CPU speech baseline. No LLM, microphone access, or network calls."""

import argparse
import json
import platform
from pathlib import Path
from time import perf_counter

import numpy as np
import sherpa_onnx as so
import soundfile as sf

ROOT = Path(__file__).resolve().parent


def load_models(threads):
    a = ROOT / "models/sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16"
    t = ROOT / "models/vits-melo-tts-zh_en"

    def onnx(prefix):
        files = sorted(a.glob(prefix + "*.onnx"))
        return str(next((p for p in files if "int8" not in p.name), files[0]))

    start = perf_counter()
    asr = so.OnlineRecognizer.from_transducer(
        tokens=str(a / "tokens.txt"),
        encoder=onnx("encoder"),
        decoder=onnx("decoder"),
        joiner=onnx("joiner"),
        num_threads=threads,
        provider="cpu",
        sample_rate=16000,
        decoding_method="greedy_search",
        enable_endpoint_detection=False,
    )
    asr_load = perf_counter() - start
    start = perf_counter()
    cfg = so.OfflineTtsConfig(
        model=so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(
                model=str(t / "model.onnx"),
                tokens=str(t / "tokens.txt"),
                lexicon=str(t / "lexicon.txt"),
                dict_dir=str(t / "dict"),
            ),
            num_threads=threads,
            provider="cpu",
        ),
        rule_fsts=",".join(str(t / f) for f in ["date.fst", "number.fst", "phone.fst"]),
    )
    if not cfg.validate():
        raise ValueError("Invalid TTS configuration")
    tts = so.OfflineTts(cfg)
    return asr, tts, dict(asr=asr_load, tts=perf_counter() - start)


def transcribe(asr, samples, rate):
    stream = asr.create_stream()
    start = perf_counter()
    partials = []
    chunk = int(rate * 0.02)
    previous = ""
    # Feed 20 ms blocks as fast as possible: compute RTF, not wall-clock live latency.
    for pos in range(0, len(samples), chunk):
        stream.accept_waveform(rate, samples[pos : pos + chunk])
        while asr.is_ready(stream):
            asr.decode_stream(stream)
        text = asr.get_result(stream)
        if text and text != previous:
            partials.append(dict(audio_position_s=min(pos + chunk, len(samples)) / rate, text=text))
            previous = text
    stream.accept_waveform(rate, np.zeros(int(rate * 0.66), dtype=np.float32))
    stream.input_finished()
    while asr.is_ready(stream):
        asr.decode_stream(stream)
    elapsed = perf_counter() - start
    return dict(
        text=asr.get_result(stream),
        compute_s=elapsed,
        audio_s=len(samples) / rate,
        rtf=elapsed / (len(samples) / rate),
        partials=partials,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", type=Path, help="Optional local recording to transcribe")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    result_dir = ROOT / "results"
    result_dir.mkdir(exist_ok=True)
    asr, tts, loads = load_models(args.threads)
    report = dict(
        runtime=so.__version__,
        platform=platform.platform(),
        provider="cpu",
        threads_per_model=args.threads,
        load_s=loads,
        note=(
            "Synthetic loopback smoke test; no LLM, VAD, microphone, playback or real-time pacing. "
            "Not an ASR accuracy benchmark or end-to-end voice latency."
        ),
        cases=[],
    )
    warm = tts.generate("你好。", sid=0, speed=1.0)
    transcribe(asr, np.asarray(warm.samples), warm.sample_rate)
    texts = [
        "您好，请问注册一家公司的费用是多少？",
        "大概两千多元，具体要看公司的情况。",
        "不是三千元，是两千五百元，不包含刻章费用。",
        "需要身份证和经营地址证明。",
        "材料齐全以后，大约五个工作日可以办理完成。",
    ]
    for i, text in enumerate(texts):
        start = perf_counter()
        audio = tts.generate(text, sid=0, speed=1.0)
        elapsed = perf_counter() - start
        samples = np.asarray(audio.samples)
        sf.write(result_dir / f"sample-{i + 1}.wav", samples, audio.sample_rate, subtype="PCM_16")
        row = dict(
            input_text=text,
            tts_full_sentence_s=elapsed,
            audio_s=len(samples) / audio.sample_rate,
            tts_rtf=elapsed / (len(samples) / audio.sample_rate),
            asr=transcribe(asr, samples, audio.sample_rate),
        )
        report["cases"].append(row)
        print(json.dumps(row, ensure_ascii=True), flush=True)
    if args.wav:
        samples, rate = sf.read(args.wav, dtype="float32", always_2d=True)
        report["user_recording"] = transcribe(asr, samples.mean(axis=1), rate)
    (result_dir / "speech-evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("Saved results/speech-evaluation.json")


if __name__ == "__main__":
    main()
