import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODELS = [
    ("asr-models", "sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16"),
    ("tts-models", "vits-melo-tts-zh_en"),
]


def main():
    target = ROOT / "models"
    target.mkdir(exist_ok=True)
    manifest = []
    for tag, name in MODELS:
        url = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/{tag}/{name}.tar.bz2"
        archive = target / (name + ".tar.bz2")
        if not archive.exists():
            print(f"Downloading {name}", flush=True)
            partial = archive.with_suffix(".part")
            subprocess.run(
                [
                    "curl.exe",
                    "-fL",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    "20",
                    "--max-time",
                    "600",
                    "--retry",
                    "2",
                    "-o",
                    str(partial),
                    url,
                ],
                check=True,
            )
            partial.replace(archive)
        digest = hashlib.file_digest(archive.open("rb"), "sha256").hexdigest()
        with tarfile.open(archive) as tf:
            tf.extractall(target, filter="data")
        manifest.append(
            dict(name=name, url=url, sha256=digest, archive_bytes=archive.stat().st_size)
        )
        print(f"Ready: {name}", flush=True)
    (ROOT / "model-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
