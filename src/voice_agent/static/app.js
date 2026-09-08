const $ = (id) => document.getElementById(id);
let sid = null,
  snapshot = null,
  lastEvent = 0,
  historyKey = "",
  polling = false;
let recording = null,
  player = null,
  pending = false,
  health = null;
const stageNames = {
  session: "会话",
  input: "输入",
  asr: "语音转写",
  llm: "模型校验",
  fields: "字段证据",
  reply: "回复",
  tts: "语音合成",
  playback: "播放",
  interruption: "插话",
  complete: "结果",
  stop: "结束",
  error: "异常",
};
const statusNames = {
  unknown: "待收集",
  partial: "待澄清",
  confirmed: "已确认",
};
function notice(text) {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
async function api(path, body, binary = false) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "X-Voice-Console": "1",
            "Content-Type": binary ? "audio/wav" : "application/json",
          },
          body: binary ? body : JSON.stringify(body),
        },
  );
  if (!response.ok) {
    let data;
    try {
      data = await response.json();
    } catch {
      data = {};
    }
    const error = new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`,
    );
    error.status = response.status;
    throw error;
  }
  return response.json();
}
async function refreshHealth() {
  try {
    health = await api("/api/health");
    $("llmDot").className = "dot " + (health.llm ? "" : "bad");
    $("modelStatus").textContent =
      health.model + (health.llm ? " · 已安装" : " · 未就绪");
    for (const type of ["asr", "tts"]) {
      $(type + "Dot").className = "dot " + (health.speech[type] ? "" : "bad");
      $(type + "Status").textContent =
        (type === "asr" ? "SenseVoice" : "Matcha") +
        " · CPU · " +
        (health.speech[type] ? "已安装" : "待安装");
    }
  } catch {
    notice("无法连接本地服务，请检查服务是否运行。");
  }
}
function controls() {
  const open = snapshot && !snapshot.stopped;
  const active =
    snapshot && !snapshot.stopped && snapshot.state.status !== "handoff";
  const busy = pending || snapshot?.busy;
  $("start").disabled = Boolean(open || busy);
  for (const id of ["goal", "fields", "mode", "speech"])
    $(id).disabled = Boolean(open || busy);
  $("input").disabled = !active || busy;
  $("send").disabled = !active || busy;
  $("record").disabled = !active || Boolean(recording);
  $("stop").disabled = !open;
  $("interrupt").disabled = !voiceSource && (!player || player.paused);
  $("sessionStatus").textContent = !snapshot
    ? "等待开始"
    : snapshot.stopped
      ? "已停止"
      : snapshot.state.status === "completed"
        ? "任务完成"
        : snapshot.state.status === "handoff"
          ? "待后续处理"
          : busy
            ? "正在处理"
            : recording
              ? "正在聆听"
              : "等待回答";
}
let fieldsKey = "";
function renderFields(fields) {
  const key = JSON.stringify([
    sid,
    fields,
    snapshot?.events.filter((e) => e.stage === "asr" && e.audio),
  ]);
  if (key === fieldsKey) return;
  fieldsKey = key;
  $("fieldCards").replaceChildren();
  let confirmed = 0;
  for (const [name, value] of Object.entries(fields)) {
    if (value.status === "confirmed") confirmed++;
    const card = node("div", undefined, "field-card"),
      head = node("div", undefined, "field-head");
    head.append(
      node("span", name),
      node("span", statusNames[value.status], "tag " + value.status),
    );
    card.append(head);
    if (value.value) card.append(node("p", value.value, "field-value"));
    if (value.evidence?.length) {
      const details = node("details");
      details.append(node("summary", `${value.evidence.length} 条原话证据`));
      for (const e of value.evidence) {
        details.append(node("blockquote", `第 ${e.turn} 轮 · ${e.quote}`));
        const source = snapshot?.events.find(
          (x) => x.stage === "asr" && x.turn === e.turn && x.audio,
        );
        if (source) {
          const audio = node("audio");
          audio.controls = true;
          audio.preload = "none";
          audio.src = source.audio;
          details.append(audio);
        }
      }
      card.append(details);
    }
    $("fieldCards").append(card);
  }
  const total = Object.keys(fields).length;
  $("progressCount").textContent = `${confirmed} / ${total}`;
  $("progressBar").style.width = (total ? (confirmed / total) * 100 : 0) + "%";
}
function renderMessages(state) {
  const key = JSON.stringify(state.history);
  if (key === historyKey) return;
  historyKey = key;
  $("messages").replaceChildren();
  state.history.forEach((m, i) => {
    const row = node("div", undefined, "message " + m.role);
    row.append(
      node("div", m.role === "user" ? "对方 · 已提交" : "助手", "who"),
      node("div", m.content, "bubble"),
    );
    $("messages").append(row);
  });
  $("messages").scrollTop = $("messages").scrollHeight;
}
function renderEvent(e) {
  if (lastEvent === 0) $("events").replaceChildren();
  const row = node(
    "div",
    undefined,
    "event " + (e.status === "error" ? "error" : ""),
  );
  const content = node("div", e.label + (e.status === "running" ? "…" : ""));
  if (e.text) content.append(node("p", e.text));
  if (e.reason) content.append(node("p", "校验说明：" + e.reason));
  if (e.updates && Object.keys(e.updates).length)
    content.append(
      node(
        "p",
        Object.entries(e.updates)
          .map(([k, v]) => `${k} → ${v.value} (${statusNames[v.status]})`)
          .join("；"),
      ),
    );
  if (e.audio) {
    const a = node("audio");
    a.controls = true;
    a.preload = "none";
    a.src = e.audio;
    a.dataset.url = e.audio;
    a.onplay = () => {
      if (player !== a) interrupt();
      player = a;
      playbackReport(e.audio, "started");
      controls();
    };
    a.onended = () => {
      playbackReport(e.audio, "ended");
      controls();
    };
    content.append(a);
  }
  row.append(
    node(
      "time",
      new Date(e.time * 1000).toLocaleTimeString("zh-CN", { hour12: false }),
    ),
    node("span", stageNames[e.stage] || e.stage, "stage"),
    content,
    node(
      "span",
      e.duration_ms !== undefined
        ? (e.duration_ms / 1000).toFixed(2) + "s"
        : "",
      "duration",
    ),
  );
  $("events").append(row);
  document.querySelectorAll("[data-stage]").forEach((n) => {
    n.classList.toggle("current", n.dataset.stage === e.stage);
    n.classList.toggle(
      "failed",
      n.dataset.stage === e.stage && e.status === "error",
    );
  });
}
async function playbackReport(url, status) {
  if (!sid) return;
  try {
    await api(`/api/sessions/${sid}/playback`, {
      audio: url.split("/").pop(),
      status,
    });
  } catch {
    /* A reload may expire the session; audio stays locally playable. */
  }
}
function interrupt() {
  playbackToken++;
  if (voiceSource) {
    voiceSource.onended = null;
    voiceSource.stop();
    voiceSource = null;
    playbackReport(voiceURL, "interrupted");
  }
  if (player && !player.paused) {
    const url = player.dataset.url;
    player.pause();
    playbackReport(url, "interrupted");
  }
  controls();
}
let voiceContext = null,
  voiceSource = null,
  voiceURL = null;
let playbackToken = 0;
async function play(url) {
  interrupt();
  const token = ++playbackToken;
  if (!voiceContext || voiceContext.state !== "running") return;
  try {
    const bytes = await (await fetch(url)).arrayBuffer();
    const buffer = await voiceContext.decodeAudioData(bytes);
    if (
      token !== playbackToken ||
      speaking ||
      queue.length ||
      snapshot?.stopped
    )
      return;
    voiceSource = voiceContext.createBufferSource();
    voiceSource.buffer = buffer;
    voiceSource.connect(voiceContext.destination);
    voiceURL = url;
    voiceSource.onended = () => {
      voiceSource = null;
      playbackReport(url, "ended");
      controls();
    };
    voiceSource.start();
    playbackReport(url, "started");
  } catch {
    notice("回复播放失败，音频仍可在溯源记录中回放。");
    playbackReport(url, "failed");
  }
  controls();
}
async function poll() {
  if (!sid || polling) return;
  polling = true;
  try {
    snapshot = await api(`/api/sessions/${sid}`);
    renderMessages(snapshot.state);
    renderFields(snapshot.state.fields);
    $("turnCount").textContent = snapshot.state.turns + " 轮";
    for (const e of snapshot.events)
      if (e.id > lastEvent) {
        renderEvent(e);
        lastEvent = e.id;
        if (
          e.stage === "tts" &&
          e.audio &&
          !snapshot.stopped &&
          !suppressed.has(e.generation) &&
          !speaking &&
          !queue.length
        )
          play(e.audio);
      }
    if (snapshot.stopped || snapshot.state.status === "handoff")
      await stopListening();
    else drain();
    controls();
  } catch (e) {
    notice(e.message);
  } finally {
    polling = false;
  }
}
$("start").onclick = async () => {
  notice("");
  pending = true;
  controls();
  try {
    if ($("mode").value === "local" && !health?.llm)
      throw new Error(
        "本地模型尚未就绪。请完成模型安装并刷新状态，或明确选择规则模拟。",
      );
    const fields = $("fields")
      .value.split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
    await startListening();
    const data = await api("/api/sessions", {
      goal: $("goal").value.trim(),
      required_fields: fields,
      mode: $("mode").value,
      speech: $("speech").checked,
    });
    interrupt();
    queue = [];
    suppressed.clear();
    sid = data.id;
    sessionStorage.setItem("voice-session", sid);
    snapshot = data;
    lastEvent = 0;
    historyKey = "";
    $("input").value = "";
    $("sessionId").textContent =
      (data.mode === "local" ? "本地大模型" : "规则模拟") +
      " / " +
      sid.slice(0, 8);
    $("export").href = `/api/sessions/${sid}/export`;
    $("export").hidden = false;
    await poll();
  } catch (e) {
    await stopListening();
    notice(e.message);
  } finally {
    pending = false;
    controls();
  }
};
$("send").onclick = async () => {
  if (!sid || !snapshot || snapshot.busy || pending) return;
  const text = $("input").value.trim();
  if (!text) return;
  interrupt();
  pending = true;
  controls();
  notice("");
  try {
    await api(`/api/sessions/${sid}/turn`, { text });
    $("input").value = "";
    await poll();
  } catch (e) {
    notice(e.message);
  } finally {
    pending = false;
    controls();
  }
};
$("input").onkeydown = (e) => {
  if (e.ctrlKey && e.key === "Enter") {
    $("send").click();
    e.preventDefault();
  }
};
$("stop").onclick = async () => {
  if (!sid) return;
  interrupt();
  await stopListening();
  try {
    snapshot = await api(`/api/sessions/${sid}/stop`, {});
    await poll();
  } catch (e) {
    notice(e.message);
  }
  controls();
};
$("interrupt").onclick = interrupt;
$("refresh").onclick = refreshHealth;
function wavBlob(chunks, rate) {
  const length = chunks.reduce((n, c) => n + c.length, 0),
    buffer = new ArrayBuffer(44 + length * 2),
    view = new DataView(buffer);
  const text = (o, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, 36 + length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, length * 2, true);
  let offset = 44;
  for (const chunk of chunks)
    for (const value of chunk) {
      view.setInt16(offset, Math.max(-1, Math.min(1, value)) * 32767, true);
      offset += 2;
    }
  return new Blob([buffer], { type: "audio/wav" });
}
let speaking = false,
  queue = [],
  sendingAudio = false,
  micStream = null;
let suppressed = new Set(),
  speechStarted = 0,
  splitting = false;
let connectionJob = null,
  micEpoch = 0;
async function startListening() {
  if (connectionJob) return connectionJob;
  connectionJob = connectMicrophone();
  try {
    await connectionJob;
  } finally {
    connectionJob = null;
  }
}
async function connectMicrophone() {
  if (recording) return;
  const epoch = micEpoch;
  if (!navigator.mediaDevices?.getUserMedia || !window.vad)
    throw new Error("请使用 Chrome / Edge，并检查本地 VAD 资源。");
  voiceContext ??= new AudioContext();
  await voiceContext.resume();
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  if (epoch !== micEpoch) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
    throw new Error("语音连接已取消");
  }
  try {
    ort.env.wasm.numThreads = 1;
    recording = await vad.MicVAD.new({
      model: "v5",
      baseAssetPath: "/vendor/vad/",
      onnxWASMBasePath: "/vendor/ort/",
      ortConfig: (instance) => {
        instance.env.wasm.numThreads = 1;
        instance.env.wasm.proxy = false;
      },
      audioContext: voiceContext,
      startOnLoad: false,
      getStream: async () => micStream,
      resumeStream: async () => micStream,
      pauseStream: async () => {},
      positiveSpeechThreshold: 0.6,
      negativeSpeechThreshold: 0.35,
      redemptionMs: 900,
      preSpeechPadMs: 300,
      minSpeechMs: 250,
      submitUserSpeechOnPause: true,
      onSpeechStart: () => {
        speechStarted = Date.now();
      },
      onSpeechRealStart: () => {
        if (
          !recording ||
          !sid ||
          snapshot?.stopped ||
          snapshot?.state.status === "handoff"
        )
          return;
        speaking = true;
        interrupt();
        suppressed.add(snapshot.generation);
        api(`/api/sessions/${sid}/interrupt`, {})
          .then((r) => suppressed.add(r.generation))
          .catch((e) => notice(e.message));
        $("recordStatus").textContent = "正在听你说 · 停顿后自动提交";
      },
      onVADMisfire: () => {
        speaking = false;
        speechStarted = 0;
      },
      onSpeechEnd: (audio) => {
        speaking = false;
        speechStarted = 0;
        if (
          !recording ||
          !sid ||
          snapshot?.stopped ||
          snapshot?.state.status === "handoff"
        )
          return;
        if (queue.length >= 8) {
          notice("待处理发言过多，请等待助手处理后重说本段。");
          return;
        }
        queue.push(wavBlob([audio], 16000));
        $("recordStatus").textContent = "语音已收取 · 自动识别中";
        drain();
      },
      onFrameProcessed: () => {
        if (speechStarted && Date.now() - speechStarted > 25000 && !splitting) {
          splitting = true;
          const mic = recording;
          mic
            ?.pause()
            .then(() => {
              if (recording === mic) return mic.start();
            })
            .finally(() => {
              splitting = false;
            });
        }
      },
    });
    if (epoch !== micEpoch) {
      await stopListening();
      return;
    }
    await recording.start();
    $("recordStatus").textContent = "麦克风已连接 · 直接说话，自动判断停顿";
    $("record").textContent = "麦克风已连接";
  } catch (e) {
    await stopListening();
    throw e;
  }
}
async function stopListening() {
  micEpoch++;
  const mic = recording;
  recording = null;
  micStream?.getTracks().forEach((t) => t.stop());
  micStream = null;
  try {
    mic?.destroy();
  } catch {
    // vad-web destroy() throws if model loading finished before audio initialization.
    // Media tracks have already been stopped, including cancellation during startup.
  }
  queue = [];
  speaking = false;
  speechStarted = 0;
  $("record").textContent = "恢复语音连接";
  $("recordStatus").textContent = "麦克风未连接";
}
async function drain() {
  if (
    sendingAudio ||
    !queue.length ||
    snapshot?.busy ||
    pending ||
    snapshot?.stopped
  )
    return;
  sendingAudio = true;
  const audio = queue.shift();
  try {
    const result = await api(`/api/sessions/${sid}/audio-turn`, audio, true);
    if (snapshot.stopped) return;
    snapshot.busy = true;
    snapshot.generation = result.generation;
    if (speaking || queue.length) suppressed.add(result.generation);
    $("recordStatus").textContent = "持续聆听 · 可随时补充或更正";
  } catch (e) {
    if (e.status === 409 && !snapshot.stopped) {
      queue.unshift(audio);
      snapshot.busy = true;
    } else {
      notice(`这段语音未提交：${e.message}。请重新说一遍。`);
    }
  } finally {
    sendingAudio = false;
  }
}
$("record").onclick = () =>
  startListening()
    .then(controls)
    .catch((e) => notice(e.message));
window.addEventListener("beforeunload", () =>
  micStream?.getTracks().forEach((t) => t.stop()),
);
renderFields(
  Object.fromEntries(
    $("fields")
      .value.split("\n")
      .map((n) => [n, { status: "unknown" }]),
  ),
);
async function restore() {
  const saved = sessionStorage.getItem("voice-session");
  if (saved)
    try {
      snapshot = await api(`/api/sessions/${saved}`);
      sid = saved;
      $("goal").value = snapshot.state.task.goal;
      $("fields").value = snapshot.state.task.required_fields.join("\n");
      $("mode").value = snapshot.mode;
      $("sessionId").textContent =
        (snapshot.mode === "local" ? "本地大模型" : "规则模拟") +
        " / " +
        sid.slice(0, 8);
      $("export").href = `/api/sessions/${sid}/export`;
      $("export").hidden = false;
      for (const event of snapshot.events) {
        renderEvent(event);
        lastEvent = event.id;
      }
      renderMessages(snapshot.state);
      renderFields(snapshot.state.fields);
      controls();
    } catch {
      sessionStorage.removeItem("voice-session");
      notice(
        "上次会话已不在内存中。历史 JSON 保存在本机 local-data 目录，可开始新会话。",
      );
    }
  await refreshHealth();
  setInterval(poll, 600);
}
restore();
