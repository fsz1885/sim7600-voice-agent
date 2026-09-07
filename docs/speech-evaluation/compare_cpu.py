"""CPU-only, sequential Chinese speech comparison; synthetic cross-ASR evaluation."""

import argparse
import gc
import hashlib
import json
import platform
import re
import statistics
import unicodedata
from math import gcd
from pathlib import Path
from time import perf_counter

import numpy as np
import sherpa_onnx as so
import soundfile as sf
from evaluate import transcribe
from scipy.signal import resample_poly

ROOT = Path(__file__).resolve().parent
MODELS = ROOT / "models"
TTS_NAMES = ["melo", "matcha", "kokoro_zh"]
ASR_NAMES = ["zipformer_small", "zipformer_zh", "paraformer_streaming", "sensevoice"]


def load_tts(name, threads):
    dirs = {
        "melo": "vits-melo-tts-zh_en",
        "matcha": "matcha-icefall-zh-baker",
        "kokoro_zh": "kokoro-multi-lang-v1_1",
    }
    folder = MODELS / dirs[name]

    def p(f):
        return str(folder / f)

    if name == "melo":
        model = so.OfflineTtsModelConfig(
            vits=so.OfflineTtsVitsModelConfig(
                model=p("model.onnx"), tokens=p("tokens.txt"), lexicon=p("lexicon.txt")
            ),
            num_threads=threads,
            provider="cpu",
        )
    elif name == "matcha":
        model = so.OfflineTtsModelConfig(
            matcha=so.OfflineTtsMatchaModelConfig(
                acoustic_model=p("model-steps-3.onnx"),
                vocoder=str(MODELS / "vocos-22khz-univ.onnx"),
                tokens=p("tokens.txt"),
                lexicon=p("lexicon.txt"),
            ),
            num_threads=threads,
            provider="cpu",
        )
    else:
        model = so.OfflineTtsModelConfig(
            kokoro=so.OfflineTtsKokoroModelConfig(
                model=p("model.onnx"),
                voices=p("voices.bin"),
                tokens=p("tokens.txt"),
                data_dir=p("espeak-ng-data"),
                lexicon=p("lexicon-us-en.txt") + "," + p("lexicon-zh.txt"),
            ),
            num_threads=threads,
            provider="cpu",
        )
    # Chinese telephone/date/number frontend, only where the distribution provides it.
    suffix = "-zh" if name == "kokoro_zh" else ""
    rules = [folder / f"{n}{suffix}.fst" for n in ["phone", "date", "number"]]
    cfg = so.OfflineTtsConfig(model=model, rule_fsts=",".join(str(f) for f in rules if f.exists()))
    if not cfg.validate():
        raise ValueError(f"Invalid {name} configuration")
    return so.OfflineTts(cfg), (3 if name == "kokoro_zh" else 0)


def load_asr(name, threads):
    names = {
        "zipformer_small": "sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16",
        "zipformer_zh": "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
        "paraformer_streaming": "sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        "sensevoice": "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
    }
    folder = MODELS / names[name]
    common = dict(tokens=str(folder / "tokens.txt"), num_threads=threads, provider="cpu")
    if name == "sensevoice":
        return so.OfflineRecognizer.from_sense_voice(
            model=str(folder / "model.int8.onnx"), language="zh", use_itn=False, **common
        )
    if name == "paraformer_streaming":
        return so.OnlineRecognizer.from_paraformer(
            encoder=str(folder / "encoder.int8.onnx"),
            decoder=str(folder / "decoder.int8.onnx"),
            **common,
        )

    def pick(prefix):
        files = sorted(folder.glob(prefix + "*.onnx"))
        preferred = [f for f in files if ("int8" in f.name) == (name != "zipformer_small")]
        return str((preferred or files)[0])

    return so.OnlineRecognizer.from_transducer(
        encoder=pick("encoder"), decoder=pick("decoder"), joiner=pick("joiner"), **common
    )


def normalized(text):
    text = re.sub(r"<\|.*?\|>", "", text)
    return "".join(
        c for c in unicodedata.normalize("NFKC", text).lower() if unicodedata.category(c)[0] in "LN"
    )


def edits(a, b):
    row = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        next_row = [i]
        for j, y in enumerate(b, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (x != y)))
        row = next_row
    return row[-1]


def recognize(model, name, samples, rate):
    if name != "sensevoice":
        return transcribe(model, samples, rate)
    start = perf_counter()
    stream = model.create_stream()
    stream.accept_waveform(rate, samples)
    model.decode_stream(stream)
    elapsed = perf_counter() - start
    return dict(
        text=stream.result.text,
        compute_s=elapsed,
        audio_s=len(samples) / rate,
        rtf=elapsed / (len(samples) / rate),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["tts", "asr", "all"], default="all")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--run", default="cpu-zh-2026-09-07")
    args = parser.parse_args()
    if Path(args.run).name != args.run or args.run in {".", ".."}:
        raise ValueError("run must be a simple directory name")
    output = ROOT / "runs" / args.run
    output.mkdir(parents=True, exist_ok=True)
    cases = json.loads((ROOT / "chinese-cases.json").read_text(encoding="utf-8"))
    meta = dict(
        provider="cpu",
        threads=args.threads,
        runtime=so.__version__,
        platform=platform.platform(),
        cpu=platform.processor(),
        method="Models loaded sequentially. Warmed once. No paced playback or LLM. "
        "CER measures synthetic TTS->ASR consistency, not human recording accuracy. "
        "All TTS timings are full-sentence generation, not streaming first audio.",
    )
    if args.stage in ["tts", "all"]:
        if (output / "tts.json").exists():
            raise FileExistsError("Use a new --run to preserve existing audio and timing evidence")
        rows, models = [], []
        for name in TTS_NAMES:
            start = perf_counter()
            model, sid = load_tts(name, args.threads)
            models.append(dict(name=name, load_s=perf_counter() - start, speaker_id=sid))
            model.generate("你好，欢迎咨询。", sid=sid)
            folder = output / name
            folder.mkdir(exist_ok=True)
            for case in cases:
                start = perf_counter()
                audio = model.generate(case["text"], sid=sid, speed=1.0)
                elapsed = perf_counter() - start
                samples = np.asarray(audio.samples)
                if not len(samples) or not np.isfinite(samples).all():
                    raise ValueError(f"Invalid generated audio: {name}/{case['id']}")
                wav = folder / (case["id"] + ".wav")
                sf.write(wav, samples, audio.sample_rate, subtype="PCM_16")
                rows.append(
                    dict(
                        tts=name,
                        case_id=case["id"],
                        text=case["text"],
                        wav=str(wav.relative_to(output)).replace("\\", "/"),
                        sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
                        sample_rate=audio.sample_rate,
                        audio_s=len(samples) / audio.sample_rate,
                        full_sentence_s=elapsed,
                        rtf=elapsed / (len(samples) / audio.sample_rate),
                    )
                )
            print(
                f"TTS {name}: {len(cases)} clips, mean "
                f"{statistics.mean(r['full_sentence_s'] for r in rows if r['tts'] == name):.3f}s",
                flush=True,
            )
            del model
            gc.collect()
        (output / "tts.json").write_text(
            json.dumps(dict(meta=meta, models=models, rows=rows), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.stage in ["asr", "all"]:
        tts = json.loads((output / "tts.json").read_text(encoding="utf-8"))
        clips = []
        for row in tts["rows"]:
            wav = output / row["wav"]
            assert hashlib.sha256(wav.read_bytes()).hexdigest() == row["sha256"]
            samples, rate = sf.read(wav, dtype="float32")
            clips.append(({**row, "condition": "native"}, samples, rate))
            divisor = gcd(rate, 8000)
            narrow = resample_poly(samples, 8000 // divisor, rate // divisor).astype(np.float32)
            clips.append(({**row, "condition": "resampled_8khz"}, narrow, 8000))
        rows, models = [], []
        for name in ASR_NAMES:
            start = perf_counter()
            model = load_asr(name, args.threads)
            models.append(
                dict(
                    name=name,
                    load_s=perf_counter() - start,
                    mode="offline" if name == "sensevoice" else "streaming",
                )
            )
            recognize(model, name, clips[0][1], clips[0][2])
            for clip, samples, rate in clips:
                result = recognize(model, name, samples, rate)
                reference, hypothesis = normalized(clip["text"]), normalized(result["text"])
                case = next(c for c in cases if c["id"] == clip["case_id"])
                checks = [
                    any(normalized(v) in hypothesis for v in group) for group in case["keywords"]
                ]
                rows.append(
                    dict(
                        asr=name,
                        tts=clip["tts"],
                        condition=clip["condition"],
                        case_id=clip["case_id"],
                        reference=clip["text"],
                        **result,
                        edits=edits(reference, hypothesis),
                        reference_chars=len(reference),
                        keyphrase_checks=checks,
                        all_keyphrases=all(checks),
                    )
                )
            print(f"ASR {name}: {len(clips)} clips completed", flush=True)
            del model
            gc.collect()
            (output / "asr.json").write_text(
                json.dumps(dict(meta=meta, models=models, rows=rows), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    print(output, flush=True)


if __name__ == "__main__":
    main()
