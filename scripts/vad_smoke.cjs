const fs = require("node:fs");
const ort = require("onnxruntime-web/wasm");
const {
  SileroV5,
} = require("../node_modules/@ricky0123/vad-web/dist/models/v5.js");
const {
  FrameProcessor,
} = require("../node_modules/@ricky0123/vad-web/dist/frame-processor.js");
(async () => {
  ort.env.wasm.numThreads = 1;
  const bytes = fs.readFileSync(
    "src/voice_agent/static/vendor/vad/silero_vad_v5.onnx",
  );
  const model = await SileroV5.new(ort, async () =>
    bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  );
  const p = new FrameProcessor(
    model.process,
    model.reset_state,
    {
      positiveSpeechThreshold: 0.6,
      negativeSpeechThreshold: 0.35,
      redemptionMs: 900,
      preSpeechPadMs: 300,
      minSpeechMs: 250,
      submitUserSpeechOnPause: true,
    },
    32,
  );
  p.resume();
  const raw = fs.readFileSync(
    process.argv[2] || "local-data/vad-synthetic.f32",
  );
  const samples = new Float32Array(
    raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength),
  );
  let starts = 0,
    ends = 0;
  for (let i = 0; i + 512 <= samples.length; i += 512)
    await p.process(samples.slice(i, i + 512), (e) => {
      if (e.msg === "SPEECH_REAL_START") starts++;
      if (e.msg === "SPEECH_END") {
        ends++;
        console.log("Segment", ends, e.audio.length / 16000);
      }
    });
  console.log({ starts, ends });
  if (starts !== 2 || ends !== 2)
    throw new Error("Expected two automatically segmented utterances");
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});
