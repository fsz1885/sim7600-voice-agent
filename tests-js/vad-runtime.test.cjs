const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('bundled MicVAD starts with the supplied AudioContext and does not own it', async () => {
  const audioContext = { audioWorklet: { async addModule() {} }, close() { throw new Error('Shared context must remain open'); } };
  const context = vm.createContext({
    self: { ort: {} }, console,
    AudioContext: class { constructor() { throw new Error('Unexpected second context'); } },
    AudioWorkletNode: class { port = { postMessage() {} }; },
    MediaStreamAudioSourceNode: class { connect() {} disconnect() {} },
  });
  vm.runInContext(fs.readFileSync('src/voice_agent/static/vendor/vad/bundle.min.js', 'utf8'), context);
  const mic = new context.self.vad.MicVAD({
    audioContext, getStream: async () => ({}), pauseStream: async () => {},
    processorType: 'AudioWorklet', baseAssetPath: '/vendor/vad/', workletOptions: {},
  }, { resume() {}, pause() {} }, 512);
  await mic.start();
  assert.equal(mic.listening, true);
  assert.equal(mic.getAudioInstances().audioContext, audioContext);
  mic.destroy();
  assert.equal(mic.listening, false);
});
