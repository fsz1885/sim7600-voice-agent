// Offline runtime assets, reproduced from package-lock.json; never fetched by the browser.
const fs = require("node:fs");
const path = require("node:path");
const target = path.resolve(__dirname, "../src/voice_agent/static/vendor");
fs.mkdirSync(target, { recursive: true });
fs.cpSync(path.join(__dirname, "licenses"), path.join(target, "licenses"), {
  recursive: true,
});
for (const [name, source, files] of [
  [
    "vad",
    "@ricky0123/vad-web",
    [
      "dist/bundle.min.js",
      "dist/vad.worklet.bundle.min.js",
      "dist/silero_vad_v5.onnx",
      "dist/bundle.min.js.LICENSE.txt",
    ],
  ],
  [
    "ort",
    "onnxruntime-web",
    [
      "dist/ort.wasm.min.js",
      "dist/ort-wasm-simd-threaded.mjs",
      "dist/ort-wasm-simd-threaded.wasm",
    ],
  ],
]) {
  fs.mkdirSync(path.join(target, name), { recursive: true });
  for (const file of files)
    fs.copyFileSync(
      path.resolve(__dirname, "../node_modules", source, file),
      path.join(target, name, path.basename(file)),
    );
}

// vad-web 0.0.29 accepts audioContext but fails to assign it in MicVAD.start().
// Patch only the pinned bundle, fail closed if a dependency update changes this code.
const bundlePath = path.join(target, "vad/bundle.min.js");
const bundle = fs.readFileSync(bundlePath, "utf8");
const before = "this.options.audioContext||(this._audioContext=new AudioContext,this.ownsAudioContext=!0)";
const after = "this.options.audioContext?(this._audioContext=this.options.audioContext):(this._audioContext=new AudioContext,this.ownsAudioContext=!0)";
if (bundle.split(before).length !== 2) {
  throw new Error("Pinned VAD AudioContext patch no longer matches; review the upstream implementation");
}
fs.writeFileSync(bundlePath, bundle.replace(before, after));
