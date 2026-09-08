const { test } = require("node:test");
const assert = require("node:assert/strict");
const vm = require("node:vm");
const fs = require("node:fs");

function harness() {
  const elements = new Map(),
    calls = [],
    tracks = {
      stopped: false,
      stop() {
        this.stopped = true;
      },
    };
  const element = () => ({
    value: "",
    checked: true,
    style: {},
    dataset: {},
    classList: { toggle() {}, add() {}, remove() {} },
    append() {},
    replaceChildren() {},
  });
  let options;
  const state = { status: "active", fields: {}, history: [], turns: 0 };
  const data = { id: "session", state, events: [], generation: 1, busy: false };
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element());
        return elements.get(id);
      },
      createElement: element,
      querySelectorAll: () => [],
    },
    sessionStorage: {
      getItem() {
        return null;
      },
      setItem() {},
      removeItem() {},
    },
    window: { addEventListener() {} },
    navigator: {
      mediaDevices: {
        getUserMedia: async () => ({ getTracks: () => [tracks] }),
      },
    },
    AudioContext: class {
      state = "running";
      async resume() {}
    },
    ort: { env: { wasm: {} } },
    vad: {
      MicVAD: {
        async new(o) {
          options = o;
          return {
            start: async () => {},
            destroy() {
              o.onSpeechEnd(new Float32Array(16000));
            },
            pause: async () => {},
          };
        },
      },
    },
    fetch: async (url, request) => {
      calls.push({ url, request });
      return {
        ok: true,
        json: async () =>
          url === "/api/health"
            ? { speech: {} }
            : url.endsWith("/audio-turn")
              ? { generation: 2, accepted: true }
              : data,
      };
    },
    setInterval() {},
    Blob,
    Float32Array,
    DataView,
    ArrayBuffer,
    Date,
    console,
  });
  context.window.vad = context.vad;
  vm.runInContext(
    fs.readFileSync("src/voice_agent/static/app.js", "utf8")
      .replace("restore().then(() => setInterval(poll, 600));", ""),
    context,
  );
  const run = (code) => vm.runInContext(code, context);
  return { run, calls, tracks, options: () => options, data };
}

test("empty tab storage recovers the server session without opening the microphone", async () => {
  const h = harness();
  h.data.state.task = { goal: "确认费用", required_fields: ["费用"] };
  await h.run("restore()");
  assert.equal(h.run("sid"), "session");
  assert.equal(h.run('$("start").disabled'), true);
  assert.equal(h.run('$("stop").disabled'), false);
  assert.equal(h.run('$("record").disabled'), false);
  assert.equal(h.options(), undefined);
});

test("speech end automatically submits PCM WAV, without a record/send click", async () => {
  const h = harness();
  await h.run(
    'sid="session"; snapshot={state:{status:"active"},generation:1,busy:false}; startListening()',
  );
  h.options().onSpeechRealStart();
  h.options().onSpeechEnd(new Float32Array(16000));
  await new Promise(setImmediate);
  const sent = h.calls.find((c) => c.url.endsWith("/audio-turn"));
  assert.ok(sent);
  assert.equal(sent.request.body.size, 32044);
  assert.equal(h.run("snapshot.busy"), true);
  assert.equal(h.run("suppressed.has(1)"), true);
});

test("speech waits while model is busy and is retained in order", async () => {
  const h = harness();
  await h.run(
    'sid="session"; snapshot={state:{status:"active"},generation:1,busy:true}; startListening()',
  );
  h.options().onSpeechEnd(new Float32Array(16000));
  h.options().onSpeechEnd(new Float32Array(32000));
  assert.equal(h.calls.filter((c) => c.url.endsWith("/audio-turn")).length, 0);
  assert.equal(h.run("queue.length"), 2);
  await h.run("snapshot.busy=false; drain()");
  assert.equal(h.run("queue.length"), 1);
  assert.equal(h.run("suppressed.has(2)"), true);
});

test("hangup releases microphone and late VAD callback cannot submit", async () => {
  const h = harness();
  await h.run(
    'sid="session"; snapshot={state:{status:"active"},generation:1}; startListening()',
  );
  await h.run("stopListening()");
  h.options().onSpeechEnd(new Float32Array(16000));
  assert.equal(h.tracks.stopped, true);
  assert.equal(h.calls.filter((c) => c.url.endsWith("/audio-turn")).length, 0);
});

test("speaking interrupts WebAudio immediately and invalidates pending playback", async () => {
  const h = harness();
  await h.run(
    'sid="session"; snapshot={state:{status:"active"},generation:1}; startListening()',
  );
  h.run(
    'globalThis.didStop=false; voiceSource={stop(){didStop=true;}}; voiceURL="/audio/a.wav"',
  );
  h.options().onSpeechRealStart();
  assert.equal(h.run("didStop"), true);
  assert.equal(h.run("voiceSource"), null);
  assert.ok(h.run("playbackToken") > 0);
});

test("handoff can still be hung up before starting another call", () => {
  const h = harness();
  h.run(
    'snapshot={state:{status:"handoff"}, stopped:false, busy:false}; controls()',
  );
  assert.equal(h.run('$("stop").disabled'), false);
  assert.equal(h.run('$("start").disabled'), true);
});

test("hangup while microphone permission is pending stops late stream", async () => {
  const h = harness();
  await h.run(
    'sid="session"; snapshot={state:{status:"active"}}; globalThis.originalGet=navigator.mediaDevices.getUserMedia; navigator.mediaDevices.getUserMedia=()=>new Promise(r=>globalThis.grant=r); globalThis.connecting=startListening(); undefined',
  );
  await h.run("stopListening()");
  h.run("originalGet().then(grant)");
  await assert.rejects(h.run("connecting"), /取消/);
  assert.equal(h.tracks.stopped, true);
});
