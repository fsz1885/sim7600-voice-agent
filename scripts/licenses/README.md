# Browser voice dependencies

The vendor step copies these notices alongside locally served runtime assets.

- `@ricky0123/vad-web` 0.0.29: ISC, https://github.com/ricky0123/vad/blob/master/LICENSE
- ONNX Runtime Web 1.22.0: MIT, https://github.com/microsoft/onnxruntime/blob/v1.22.0/LICENSE
- Silero VAD v5 model (distributed by vad-web): MIT, https://github.com/snakers4/silero-vad/blob/v5.1/LICENSE

The npm integrity hashes are recorded in package-lock.json. Runtime assets are generated, not checked into Git.

The vendor script applies a narrowly matched patch to vad-web 0.0.29's bundle:
MicVAD.start assigns a supplied audioContext to its internal context without taking
ownership. Upstream omits this assignment and throws "Audio context is null".
The build fails if the pinned code changes; tests execute the patched bundle's
actual initialization and verify that destruction leaves the shared context open.
