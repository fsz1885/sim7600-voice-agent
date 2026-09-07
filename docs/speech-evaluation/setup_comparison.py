"""Download published CPU candidates; preserve baseline assets."""

import hashlib
import json
import subprocess
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = [
    ("asr-models", "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30.tar.bz2"),
    ("asr-models", "sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"),
    ("asr-models", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2"),
    ("tts-models", "matcha-icefall-zh-baker.tar.bz2"),
    ("vocoder-models", "vocos-22khz-univ.onnx"),
    ("tts-models", "kokoro-multi-lang-v1_1.tar.bz2"),
]


def fetch(asset):
    tag, name = asset
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    path = dest / name
    url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{tag}/{name}"
    if not path.exists():
        print(f"Download: {name}", flush=True)
        partial = path.with_suffix(".part")
        subprocess.run(
            [
                "curl.exe",
                "-fL",
                "--silent",
                "--show-error",
                "--connect-timeout",
                "20",
                "--max-time",
                "900",
                "--retry",
                "2",
                "-o",
                str(partial),
                url,
            ],
            check=True,
        )
        partial.replace(path)
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    marker = dest / (name + ".extracted")
    if name.endswith(".tar.bz2") and not marker.exists():
        with tarfile.open(path) as tf:
            tf.extractall(dest, filter="data")
        marker.write_text(digest, encoding="ascii")
    print(f"Ready: {name}", flush=True)
    return dict(name=name, url=url, sha256=digest, bytes=path.stat().st_size)


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(fetch, ASSETS))
    (ROOT / "comparison-manifest.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
