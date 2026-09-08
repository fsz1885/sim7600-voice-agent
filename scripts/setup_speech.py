"""Download only the selected CPU models and verify recorded archive hashes."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = {
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
    "matcha-icefall-zh-baker.tar.bz2",
    "vocos-22khz-univ.onnx",
}


def install(folder):
    folder.mkdir(parents=True, exist_ok=True)
    rows = json.loads(
        (ROOT / "docs/speech-evaluation/comparison-manifest.json").read_text(encoding="utf-8")
    )
    for row in rows:
        if row["name"] not in NAMES:
            continue
        target = folder / row["name"]
        if not target.exists():
            partial = target.with_name(target.name + ".part")
            subprocess.run(
                [
                    shutil.which("curl") or "curl",
                    "--fail",
                    "--location",
                    "--retry",
                    "3",
                    "--connect-timeout",
                    "20",
                    "--output",
                    str(partial),
                    row["url"],
                ],
                check=True,
            )
            partial.replace(target)
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != row["sha256"] or target.stat().st_size != row["bytes"]:
            raise ValueError(f"Hash/size mismatch: {target.name}; file left for inspection")
        if target.name.endswith(".tar.bz2"):
            with tarfile.open(target) as archive:
                archive.extractall(folder, filter="data")
        print(f"Verified: {target.name}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", type=Path, default=ROOT / "models")
    install(parser.parse_args().models)
