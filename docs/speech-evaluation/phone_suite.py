"""Expanded synthetic telephone stress suite. No real calls or cloud inference."""

import argparse
import gc
import hashlib
import io
import json
import platform
from math import gcd
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf
from compare_cpu import ASR_NAMES, ROOT, TTS_NAMES, edits, load_asr, load_tts, normalized, recognize
from scipy.signal import butter, resample_poly, sosfiltfilt

CONDITIONS = ["clean", "narrow_ulaw", "narrow_noise12db_ulaw"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_digest(cases):
    payload = json.dumps(cases, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def ulaw_roundtrip(samples):
    buffer = io.BytesIO()
    sf.write(buffer, samples, 8000, format="WAV", subtype="ULAW")
    buffer.seek(0)
    return sf.read(buffer, dtype="float32")[0]


def phone_audio(samples, rate, noisy, seed):
    divisor = gcd(rate, 8000)
    y = resample_poly(samples, 8000 // divisor, rate // divisor)
    sos = butter(4, [300, 3400], btype="bandpass", fs=8000, output="sos")
    y = sosfiltfilt(sos, y)
    if noisy:
        rng = np.random.default_rng(seed)
        noise = sosfiltfilt(sos, rng.normal(size=len(y)))
        # SNR measured over the full utterance, not a speech-active mask.
        noise *= np.sqrt(np.mean(y * y) / (10**1.2 * np.mean(noise * noise)))
        y += noise
    peak = np.max(np.abs(y))
    if peak > 0.98:
        y *= 0.98 / peak
    return ulaw_roundtrip(y.astype(np.float32))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="phone-cpu-2026-09-08")
    p.add_argument("--stage", choices=["tts", "asr", "all"], default="all")
    args = p.parse_args()
    if Path(args.run).name != args.run or args.run in {".", ".."}:
        raise ValueError("Invalid run name")
    out = ROOT / "runs" / args.run
    out.mkdir(exist_ok=True, parents=True)
    corpus = ROOT / "phone-cases-v2.json"
    cases = json.loads(corpus.read_text(encoding="utf-8"))
    meta = dict(
        cpu=platform.processor(),
        platform=platform.platform(),
        threads=2,
        provider="cpu",
        corpus_sha256=corpus_digest(cases),
        corpus_hash_format="canonical-json-utf8-sort-keys",
        corpus_file_sha256_at_run=digest(corpus),
        conditions=CONDITIONS,
        noise_seed_base=20260908,
        noise_snr_db=12,
        source="synthetic TTS only",
        caveat="Not real telephone recordings, no VAD/LLM/AEC or live pacing. "
        "Full-sentence TTS timing; ASR RTF is compute only.",
    )
    if args.stage in ["tts", "all"]:
        if (out / "tts.json").exists():
            raise FileExistsError("Preserve prior evidence; use a new run name")
        save(out / "cases.json", cases)
        rows = []
        for name in TTS_NAMES:
            model, sid = load_tts(name, 2)
            model.generate("你好，欢迎咨询。", sid=sid)
            folder = out / name
            folder.mkdir(exist_ok=True)
            for c in cases:
                start = perf_counter()
                a = model.generate(c["text"], sid=sid)
                elapsed = perf_counter() - start
                samples = np.asarray(a.samples)
                if not len(samples) or not np.isfinite(samples).all():
                    raise ValueError("Invalid audio")
                wav = folder / (c["id"] + ".wav")
                sf.write(wav, samples, a.sample_rate, subtype="PCM_16")
                rows.append(
                    dict(
                        tts=name,
                        case_id=c["id"],
                        category=c["category"],
                        wav=wav.relative_to(out).as_posix(),
                        sha256=digest(wav),
                        sample_rate=a.sample_rate,
                        audio_s=len(samples) / a.sample_rate,
                        full_sentence_s=elapsed,
                        speaker_id=sid,
                    )
                )
            print(f"TTS {name}: {len(cases)} complete", flush=True)
            del model
            gc.collect()
        save(out / "tts.json", dict(meta=meta, rows=rows))
    if args.stage in ["asr", "all"]:
        tts = json.loads((out / "tts.json").read_text(encoding="utf-8"))
        if tts["meta"]["corpus_sha256"] != corpus_digest(cases):
            raise ValueError("Corpus changed since audio generation")
        clips = []
        for row in tts["rows"]:
            path = out / row["wav"]
            if digest(path) != row["sha256"]:
                raise ValueError("Audio hash mismatch")
            samples, rate = sf.read(path, dtype="float32")
            clips.append((row, "clean", samples, rate))
            for cond in CONDITIONS[1:]:
                transformed = phone_audio(
                    samples, rate, cond == CONDITIONS[2], 20260908 + int(row["case_id"])
                )
                # Derivatives generated once and shared across all ASR models.
                clips.append((row, cond, transformed, 8000))
                # Save diagnostic case 03 only; all derivatives are reproducible from seeds.
                if row["case_id"] == "03":
                    sf.write(
                        out / row["tts"] / f"03-{cond}.wav", transformed, 8000, subtype="PCM_16"
                    )
        rng = np.random.default_rng(20260908)
        controls = {
            "silence": np.zeros(24000, dtype=np.float32),
            "noise": rng.normal(0, 0.01, 24000).astype(np.float32),
            "hum": (0.01 * np.sin(2 * np.pi * 50 * np.arange(24000) / 8000)).astype(np.float32),
        }
        rows, control_rows = [], []
        for name in ASR_NAMES:
            model = load_asr(name, 2)
            recognize(model, name, clips[0][2], clips[0][3])
            for i, (clip, cond, samples, rate) in enumerate(clips):
                r = recognize(model, name, samples, rate)
                r.pop("partials", None)
                c = next(c for c in cases if c["id"] == clip["case_id"])
                ref, hyp = normalized(c["text"]), normalized(r["text"])
                checks = [any(normalized(v) in hyp for v in group) for group in c["keywords"]]
                rows.append(
                    dict(
                        asr=name,
                        tts=clip["tts"],
                        condition=cond,
                        case_id=c["id"],
                        category=c["category"],
                        reference=c["text"],
                        **r,
                        edits=edits(ref, hyp),
                        reference_chars=len(ref),
                        all_keyphrases=all(checks),
                        checks=checks,
                    )
                )
                if (i + 1) % 90 == 0:
                    print(f"ASR {name}: {i + 1}/{len(clips)}", flush=True)
            for kind, samples in controls.items():
                r = recognize(model, name, samples, 8000)
                r.pop("partials", None)
                control_rows.append(
                    dict(asr=name, kind=kind, **r, false_transcript=bool(normalized(r["text"])))
                )
            save(out / "asr.json", dict(meta=meta, rows=rows, controls=control_rows))
            del model
            gc.collect()
        print(f"Done: {len(rows)} speech decodes, {len(control_rows)} controls", flush=True)


if __name__ == "__main__":
    main()
