import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {ModelSettings, HardwareSettings} from './settings';

type Task = {id:string;goal:string;status:string;steps:number;plan:string[];result:string;
  pending:null|{action:{tool:string;arguments:Record<string,unknown>}};
  messages:{role:string;content:string;name?:string}[];
  executions:{id:string;tool:string;status:string;result?:{artifact?:string;message?:string}}[]};
type Event = {id:number;type:string;time:number;payload:{message?:string}};
type Health = {model:string;thinking:string;configured:boolean;endpoint:string;
  speech:{asr:boolean;tts:boolean};tools:{name:string;description:string;requires_authorization:boolean}[]};
const labels:Record<string,string> = {queued:'排队中',running:'执行中',paused:'已暂停',waiting_user:'等待回复',
  waiting_approval:'等待授权',completed:'已完成',failed:'未完成',cancelled:'已停止'};
async function api(path:string, body?:unknown) {
  const res=await fetch('/api'+path, {method:body===undefined?'GET':'POST',
    headers:{'Content-Type':'application/json','X-Agent-UI':'1'},body:body===undefined?undefined:JSON.stringify(body)});
  const data=await res.json(); if(!res.ok) throw new Error(typeof data.detail==='string'?data.detail:'请求参数无效'); return data;
}

function App(){
  const [phone,setPhone]=useState<{status:string;task_id?:string;turns?:number;sent_bytes?:number;received_bytes?:number;error?:string}>({status:'idle'});
  const [phoneNumber,setPhoneNumber]=useState(''),[phoneGoal,setPhoneGoal]=useState('进行双向语音测试，确认对方能听清，然后简短回答问题。');
  const phoneActive=['preparing','dialing','active'].includes(phone.status);
  useEffect(()=>{const poll=()=>api('/phone').then(setPhone).catch(()=>{});poll();const timer=window.setInterval(poll,1000);return()=>window.clearInterval(timer);},[]);
  const [tasks,setTasks]=useState<Task[]>([]),[task,setTask]=useState<Task|null>(null),[health,setHealth]=useState<Health|null>(null);
  const [page,setPage]=useState('tasks'),[goal,setGoal]=useState(''),[numbers,setNumbers]=useState(''),[text,setText]=useState('');
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[events,setEvents]=useState<Event[]>([]);
  const [recording,setRecording]=useState(false),[autoSpeak,setAutoSpeak]=useState(false),[voiceStatus,setVoiceStatus]=useState('');
  const selected=useRef<string|null>(null),player=useRef<HTMLAudioElement|null>(null),voiceGeneration=useRef(0);
  const mic=useRef<{stream:MediaStream;ctx:AudioContext;node:ScriptProcessorNode;source:MediaStreamAudioSourceNode;gain:GainNode;chunks:Float32Array[];timer:number}|null>(null);
  const upload=useRef<HTMLInputElement|null>(null), spoken=useRef<string>('');
  const refresh=async()=>setTasks(await api('/tasks'));
  const attempt=async(fn:()=>Promise<void>)=>{setError('');setBusy(true);try{await fn();}catch(e){setError(String(e));setVoiceStatus('');}finally{setBusy(false);}};
  useEffect(()=>{Promise.all([refresh(),api('/health').then(setHealth)]).catch(e=>setError(String(e)));return()=>{interrupt();if(mic.current) releaseMic();};},[]);
  useEffect(()=>{
    if(!task?.id)return; const id=task.id;selected.current=id;setEvents([]);
    const stream=new EventSource(`/api/tasks/${id}/events`);
    stream.onmessage=e=>{const row=JSON.parse(e.data);setEvents(old=>old.some(x=>x.id===row.id)?old:[...old,row].slice(-150));
      api('/tasks/'+id).then(data=>{if(selected.current===id)setTask(data);}).catch(e=>setError(String(e)));refresh().catch(()=>{});};
    return()=>stream.close();
  },[task?.id]);
  useEffect(()=>{
    if(!task||!autoSpeak||task.status==='running'||!task.result)return;
    const key=task.id+':'+task.steps;if(spoken.current===key)return;spoken.current=key;
    say(task.result).catch(e=>setError(String(e)));
  },[task?.id,task?.steps,task?.status,autoSpeak]);
  function interrupt(){voiceGeneration.current++;player.current?.pause();player.current=null;setVoiceStatus('');}
  async function say(value:string){
    interrupt();const generation=voiceGeneration.current;
    const chunks=value.match(/[\s\S]{1,180}/g)||[];
    for(const chunk of chunks){
      if(generation!==voiceGeneration.current)return;setVoiceStatus('正在合成…');
      const data=await api('/synthesize',{text:chunk});if(generation!==voiceGeneration.current)return;
      const audio=new Audio('/api/audio/'+data.audio);player.current=audio;
      await new Promise<void>((resolve,reject)=>{audio.onended=()=>resolve();audio.onpause=()=>resolve();audio.onerror=()=>reject(new Error('音频播放失败'));audio.play().then(()=>setVoiceStatus('正在播放')).catch(reject);});
    }
    if(generation===voiceGeneration.current)setVoiceStatus('');
  }
  function releaseMic(){const current=mic.current;if(!current)return;window.clearTimeout(current.timer);current.node.disconnect();current.source.disconnect();current.gain.disconnect();current.stream.getTracks().forEach(t=>t.stop());current.ctx.close();mic.current=null;setRecording(false);}
  async function record(){
    if(recording){await finishRecording();return;}
    interrupt();const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
    const ctx=new AudioContext();const source=ctx.createMediaStreamSource(stream),node=ctx.createScriptProcessor(2048,1,1),gain=ctx.createGain();gain.gain.value=0;
    const chunks:Float32Array[]=[];node.onaudioprocess=e=>chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    source.connect(node);node.connect(gain);gain.connect(ctx.destination);await ctx.resume();
    mic.current={stream,ctx,node,source,gain,chunks,timer:window.setTimeout(()=>finishRecording().catch(e=>setError(String(e))),25000)};
    setRecording(true);setVoiceStatus('录音中 · 再点一次结束，最长 25 秒');
  }
  async function finishRecording(){
    const current=mic.current;if(!current)return;
    const chunks=current.chunks,rate=current.ctx.sampleRate;releaseMic();
    const size=chunks.reduce((n,c)=>n+c.length,0),input=new Float32Array(size);let offset=0;
    for(const c of chunks){input.set(c,offset);offset+=c.length;}
    // Browser OfflineAudioContext provides band-limited conversion to the ASR input rate.
    const offline=new OfflineAudioContext(1,Math.max(1,Math.ceil(size*16000/rate)),16000);
    const buffer=offline.createBuffer(1,size,rate);buffer.copyToChannel(input,0);
    const source=offline.createBufferSource();source.buffer=buffer;source.connect(offline.destination);source.start();
    const pcm=(await offline.startRendering()).getChannelData(0),bytes=new ArrayBuffer(44+pcm.length*2),view=new DataView(bytes);
    const str=(at:number,value:string)=>{for(let i=0;i<value.length;i++)view.setUint8(at+i,value.charCodeAt(i));};
    str(0,'RIFF');view.setUint32(4,36+pcm.length*2,true);str(8,'WAVE');str(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,16000,true);view.setUint32(28,32000,true);view.setUint16(32,2,true);view.setUint16(34,16,true);str(36,'data');view.setUint32(40,pcm.length*2,true);
    for(let i=0;i<pcm.length;i++)view.setInt16(44+i*2,Math.max(-1,Math.min(1,pcm[i]))*32767,true);
    await transcribe(new Blob([bytes],{type:'audio/wav'}));
  }
  async function transcribe(blob:Blob){
    const target=selected.current;setVoiceStatus('识别中…');const res=await fetch('/api/transcribe',{method:'POST',headers:{'X-Agent-UI':'1','Content-Type':'audio/wav'},body:blob});
    const data=await res.json();if(!res.ok)throw new Error(data.detail);
    if(selected.current!==target){setVoiceStatus('任务已切换，未将转写提交至新任务');return;}
    if(target)setText(data.text);else setGoal(data.text);setVoiceStatus('转写已填入输入框，请确认后提交');
  }
  async function choose(id:string){interrupt();selected.current=id;setTask(await api('/tasks/'+id));setPage('tasks');setText('');}
  async function control(op:string){if(!task)return;interrupt();setTask(await api(`/tasks/${task.id}/${op}`,{}));await refresh();}
  return <div className="shell"><aside className="nav"><div className="brand"><span className="logo">声</span><div>声程<small>自主语音工作台</small></div></div>
    <button className={page==='tasks'?'navItem active':'navItem'} onClick={()=>setPage('tasks')}>◈　任务工作台</button>
    <button className={page==='tools'?'navItem active':'navItem'} onClick={()=>setPage('tools')}>⚙　工具与模型</button>
    <button className={page==='hardware'?'navItem active':'navItem'} onClick={()=>setPage('hardware')}>◉　SIM7600 调试</button>
    <button className={page==='model'?'navItem active':'navItem'} onClick={()=>setPage('model')}>⚙　大模型配置</button>
    <div className="navHeading">最近任务 <span>{tasks.length}</span></div><div className="taskList">{tasks.map(t=><button key={t.id} className={'taskLink '+(t.id===task?.id?'chosen':'')} onClick={()=>attempt(()=>choose(t.id))}><b>{t.goal}</b><small>{labels[t.status]}</small></button>)}</div>
    <div className="navFoot"><i/>本机工作台 · 任务持久保存<small>电话工具与浏览器语音独立运行</small></div></aside>
    <main><header><div><small>WORKSPACE / VOICE AGENT</small><h1>{page==='model'?'大模型配置':page==='hardware'?'SIM7600 调试':page==='tools'?'工具与模型':'让目标开始行动'}</h1></div><button onClick={()=>{interrupt();selected.current=null;setTask(null);setPage('tasks');}}>＋ 新建任务</button></header>
    {error&&<div className="error" role="alert">{error}<button onClick={()=>setError('')}>关闭</button></div>}
    <details className="panel"><summary>SIM7600 实时电话 · {({idle:'待机',preparing:'模型预热',dialing:'拨号中',active:'通话中',ended:'已结束',stopped:'已停止',error:'失败'} as Record<string,string>)[phone.status]}</summary>
      <p>接听后自动进行语音对话，最长 120 秒。测试音频保存在本机；说“停止测试”可结束。</p>
      <input aria-label="测试电话号码" placeholder="输入授权拨打的号码" value={phoneNumber} onChange={e=>setPhoneNumber(e.target.value)} disabled={phoneActive}/>
      <input aria-label="电话任务目标" value={phoneGoal} onChange={e=>setPhoneGoal(e.target.value)} disabled={phoneActive}/>
      <button className="primary" disabled={busy||phoneActive||!phoneNumber.trim()||!phoneGoal.trim()} onClick={()=>attempt(async()=>{const t=await api('/phone/start',{number:phoneNumber.trim(),goal:phoneGoal,seconds:120});await choose(t.id);setPhone(await api('/phone'));await refresh();})}>拨打并开始语音对话</button>
      <button disabled={!phoneActive} onClick={()=>attempt(async()=>setPhone(await api('/phone/stop',{})))}>挂断电话</button>
      <button disabled={phone.status!=='active'} onClick={()=>attempt(async()=>{await api('/phone/interrupt',{});})}>打断播报</button>
      <p>接收 {phone.received_bytes||0} 字节 · 发送 {phone.sent_bytes||0} 字节 · 已识别 {phone.turns||0} 轮 {phone.error||''}</p>
      {phone.task_id&&<button onClick={()=>attempt(()=>choose(phone.task_id!))}>查看电话转写与事件</button>}
    </details>
    {page==='model'?<ModelSettings api={api} onSaved={()=>api('/health').then(setHealth).catch(e=>setError(String(e)))}/>:page==='hardware'?<HardwareSettings api={api}/>:page==='tools'?<section className="settings"><div className="panel"><h2>当前模型</h2><strong>{health?.model}</strong><p>{health?.thinking==='disabled'?'非思考模式':'由服务端决定思考模式'} · {health?.configured?'模型已配置（请测试连接）':'缺少配置'}</p><code>{health?.endpoint}</code><p>ASR {health?.speech.asr?'就绪':'未安装'} · TTS {health?.speech.tts?'就绪':'未安装'}</p><button onClick={()=>attempt(async()=>setHealth(await api('/health')))}>刷新状态</button></div><div className="toolGrid">{health?.tools.map(t=><div className="panel" key={t.name}><small>{t.requires_authorization?'按任务授权':'已注册能力'}</small><h3>{t.name}</h3><p>{t.description}</p></div>)}</div></section>:
    !task?<section className="start"><div className="eyebrow">从一个目标开始</div><h2>说出你想完成的事。</h2><p>助手会选择可用工具、记录执行过程，并在需要时向你提问。</p>
      <div className="composer"><textarea aria-label="任务目标" placeholder="例如：阅读现有知识文档，整理设备接入的注意事项并保存为笔记。" value={goal} onChange={e=>setGoal(e.target.value)}/><div className="composeFoot"><button className={recording?'recording':''} onClick={()=>attempt(record)}>{recording?'■ 结束录音':'● 语音输入'}</button><button onClick={()=>upload.current?.click()}>上传 WAV</button><button className="primary" disabled={busy||!goal.trim()} onClick={()=>attempt(async()=>{const t=await api('/tasks',{goal,allowed_numbers:numbers.split(/[,，\s]+/).filter(Boolean)});selected.current=t.id;setTask(t);await refresh();})}>开始执行 →</button></div></div>
      <details><summary>本任务的电话授权范围</summary><p>填写后即授权此任务拨打这些号码；留空时，拨号动作需单独确认。</p><input placeholder="号码以逗号分隔" value={numbers} onChange={e=>setNumbers(e.target.value)}/></details>
      <div className="examples">{['查看电话设备是否就绪，说明当前状态。','阅读知识文档并整理一份简洁的接入说明，保存为笔记。','帮我规划一次电话询价，需要先了解什么？'].map(x=><button key={x} onClick={()=>setGoal(x)}>{x}<span>↗</span></button>)}</div></section>:
    <section className="taskView"><div className="taskTop"><div><span className={'badge '+task.status}>{labels[task.status]}</span><h2>{task.goal}</h2></div><div><button disabled={busy||task.status!=='running'} onClick={()=>attempt(()=>control('pause'))}>暂停</button><button disabled={busy||!['paused','waiting_user'].includes(task.status)} onClick={()=>attempt(()=>control('resume'))}>继续</button><button disabled={busy||['completed','cancelled'].includes(task.status)} onClick={()=>attempt(()=>control('cancel'))}>停止任务</button></div></div>
      <div className="columns"><aside className="panel plan"><h3>执行计划</h3>{task.plan.length?task.plan.map((p,i)=><p key={i}><span>{i+1}</span>{p}</p>):<p className="muted">计划会随实际进展更新</p>}<small>已运行 {task.steps} 个决策步骤</small></aside>
      <div className="conversation"><div className="messages">{task.messages.filter(m=>m.role!=='tool').map((m,i)=><article key={i} className={m.role}><small>{m.role==='user'?'你':'助手'}</small><p>{m.content}</p>{m.role==='assistant'&&<button onClick={()=>attempt(()=>say(m.content))}>▷ 播放</button>}</article>)}{task.status==='running'&&<div className="working"><i/> 正在处理目标与工具结果…</div>}</div>
      {task.pending&&<div className="approval"><b>待确认操作：{task.pending.action.tool}</b><pre>{JSON.stringify(task.pending.action.arguments,null,2)}</pre><button disabled={busy} onClick={()=>attempt(async()=>setTask(await api(`/tasks/${task.id}/approval`,{approve:true})))}>允许这次操作</button><button disabled={busy} onClick={()=>attempt(async()=>setTask(await api(`/tasks/${task.id}/approval`,{approve:false})))}>拒绝</button></div>}
      <div className="reply"><textarea aria-label="补充消息" value={text} onChange={e=>setText(e.target.value)} placeholder="补充信息；运行中请先暂停任务"/><div><button onClick={()=>attempt(record)}>{recording?'■ 停止录音':'● 语音'}</button><button onClick={interrupt}>停止播放</button><button className="primary" disabled={busy||!text.trim()||!['paused','waiting_user'].includes(task.status)} onClick={()=>attempt(async()=>{setTask(await api(`/tasks/${task.id}/message`,{text}));setText('');})}>发送</button></div></div></div>
      <aside className="panel evidence"><h3>操作与结果</h3>{task.executions.map(e=><div className="execution" key={e.id}><b>{e.tool}</b><small>{e.status}</small>{e.result?.artifact&&<a href={'/api/artifacts/'+e.result.artifact}>下载笔记 ↗</a>}{e.result?.message&&<p>{e.result.message}</p>}</div>)}<h3>事件记录</h3>{events.slice(-12).map(e=><div className="event" key={e.id}><small>{new Date(e.time*1000).toLocaleTimeString()}</small><span>{e.type}</span>{e.payload.message&&<p>{e.payload.message}</p>}</div>)}</aside></div></section>}
    <footer><span>{voiceStatus||'语音输入由本机识别；提交后的文字与工具结果发送至当前配置的模型。'}</span><label><input type="checkbox" checked={autoSpeak} onChange={e=>setAutoSpeak(e.target.checked)}/> 自动播报回复</label></footer>
    <input ref={upload} type="file" accept=".wav" hidden onChange={e=>{const f=e.target.files?.[0];if(f)attempt(()=>transcribe(f));e.target.value='';}}/>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
