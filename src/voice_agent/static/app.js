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
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`,
    );
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
  const active =
    snapshot && !snapshot.stopped && snapshot.state.status === "active";
  const busy = pending || snapshot?.busy;
  $("start").disabled = Boolean(active || busy);
  for (const id of ["goal", "fields", "mode", "speech"])
    $(id).disabled = Boolean(active || busy);
  $("input").disabled = !active || busy || Boolean(recording);
  $("send").disabled = !active || busy || Boolean(recording);
  $("record").disabled = !active || busy;
  $("stop").disabled = !active && !busy;
  $("interrupt").disabled = !player || player.paused;
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
              ? "正在录音"
              : "等待回答";
}
function renderFields(fields) {
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
      for (const e of value.evidence)
        details.append(node("blockquote", `第 ${e.turn} 轮 · ${e.quote}`));
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
  if (player && !player.paused) {
    const url = player.dataset.url;
    player.pause();
    playbackReport(url, "interrupted");
  }
  controls();
}
async function play(url) {
  interrupt();
  player = new Audio(url);
  player.dataset.url = url;
  player.onended = () => {
    playbackReport(url, "ended");
    controls();
  };
  player.onerror = () => {
    playbackReport(url, "failed");
    notice("回复音频播放失败，可在过程记录中重试播放。");
    controls();
  };
  try {
    await player.play();
    await playbackReport(url, "started");
  } catch {
    notice("浏览器阻止了自动播放，请在过程记录中点击音频播放。");
    await playbackReport(url, "failed");
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
        if (e.stage === "tts" && e.audio && !snapshot.stopped) play(e.audio);
      }
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
    const data = await api("/api/sessions", {
      goal: $("goal").value.trim(),
      required_fields: fields,
      mode: $("mode").value,
      speech: $("speech").checked,
    });
    interrupt();
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
  if (recording) await stopRecording(false);
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
async function startRecording() {
  interrupt();
  notice("");
  if (!navigator.mediaDevices?.getUserMedia)
    throw new Error("此浏览器无法录音，请用 Chrome 或 Edge 打开本机地址。");
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
  });
  try {
    if (snapshot?.stopped || snapshot?.state.status !== "active") {
      throw new Error("会话已结束，录音未开始。");
    }
    const context = new AudioContext({ sampleRate: 16000 });
    await context.resume();
    const source = context.createMediaStreamSource(stream),
      processor = context.createScriptProcessor(4096, 1, 1),
      silent = context.createGain();
    silent.gain.value = 0;
    const rec = {
      stream,
      context,
      source,
      processor,
      silent,
      chunks: [],
      samples: 0,
      started: Date.now(),
      timer: null,
    };
    processor.onaudioprocess = (e) => {
      if (recording !== rec) return;
      const input = e.inputBuffer.getChannelData(0);
      const remaining = context.sampleRate * 30 - rec.samples;
      if (remaining > 0) {
        const chunk = new Float32Array(input.slice(0, remaining));
        rec.chunks.push(chunk);
        rec.samples += chunk.length;
      }
    };
    source.connect(processor);
    processor.connect(silent);
    silent.connect(context.destination);
    recording = rec;
    rec.timer = setInterval(() => {
      const seconds = Math.min(
        30,
        Math.floor((Date.now() - rec.started) / 1000),
      );
      $("recordStatus").textContent = `正在录音 ${seconds} / 30 秒`;
      if (seconds >= 30) stopRecording(true);
    }, 250);
    $("record").textContent = "■ 停止录音";
    $("record").classList.add("recording");
    controls();
  } catch (e) {
    stream.getTracks().forEach((t) => t.stop());
    throw e;
  }
}
async function stopRecording(upload) {
  const rec = recording;
  if (!rec) return;
  recording = null;
  clearInterval(rec.timer);
  rec.processor.disconnect();
  rec.source.disconnect();
  rec.silent.disconnect();
  rec.stream.getTracks().forEach((t) => t.stop());
  await rec.context.close();
  $("record").textContent = "● 录音";
  $("record").classList.remove("recording");
  $("recordStatus").textContent = "录音转写后可修改，再发送";
  if (!upload) {
    controls();
    return;
  }
  pending = true;
  controls();
  try {
    const result = await api(
      `/api/sessions/${sid}/transcribe`,
      wavBlob(rec.chunks, rec.context.sampleRate),
      true,
    );
    $("input").value = result.text;
    notice("转写已完成，请核对金额、否定和条件后发送。");
    await poll();
  } catch (e) {
    notice(e.message);
  } finally {
    pending = false;
    controls();
    $("input").focus();
  }
}
$("record").onclick = async () => {
  if (pending) return;
  pending = true;
  controls();
  try {
    if (recording) await stopRecording(true);
    else await startRecording();
  } catch (e) {
    notice(
      e.name === "NotAllowedError"
        ? "麦克风权限未开启，请允许浏览器访问麦克风，或输入文字。"
        : e.message,
    );
  } finally {
    pending = false;
    controls();
  }
};
window.addEventListener("beforeunload", () => {
  if (recording) recording.stream.getTracks().forEach((t) => t.stop());
});
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
