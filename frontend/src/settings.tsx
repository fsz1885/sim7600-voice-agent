import React, {useEffect, useState} from 'react';

type API = (path:string, body?:unknown)=>Promise<any>;
type Config = {provider:string;base_url:string;model:string;has_key:boolean;timeout_seconds:number};
export function ModelSettings({api,onSaved}:{api:API;onSaved:()=>void}){
  const [config,setConfig]=useState<Config|null>(null),[key,setKey]=useState(''),[clear,setClear]=useState(false);
  const [busy,setBusy]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState(''),[dirty,setDirty]=useState(false);
  useEffect(()=>{api('/settings/model').then(setConfig).catch(e=>setError(String(e)));},[]);
  function change(update:Partial<Config>){setConfig(old=>old?{...old,...update}:old);setDirty(true);setMessage('');}
  async function run(save:boolean){setBusy(true);setError('');setMessage('');try{
    if(save){const result=await api('/settings/model',{provider:config!.provider,base_url:config!.base_url,model:config!.model,api_key:key,clear_key:clear,timeout_seconds:config!.timeout_seconds});setConfig(result);setKey('');setClear(false);setDirty(false);onSaved();setMessage('配置已保存，下一次模型请求生效。');}
    else {const result=await api('/settings/model/test',{});setMessage(`${result.message} · ${result.duration_ms} ms`);}
  }catch(e){setError(String(e));}finally{setBusy(false);}}
  return <section className="settings"><div className="panel settingsForm"><h2>大模型配置</h2><p>支持 Kimi 和 OpenAI 兼容的 Chat Completions 接口。模型需要能返回 JSON 动作。</p>
    {error&&<p role="alert" className="settingsError">{error}</p>}{message&&<p role="status">{message}</p>}
    {config&&<fieldset disabled={busy}><legend>服务与凭据</legend>
      <label>接口类型<select value={config.provider} onChange={e=>change({provider:e.target.value})}><option value="kimi">Kimi（关闭思考）</option><option value="openai-compatible">OpenAI 兼容接口（含 Ollama / llama.cpp）</option></select></label>
      <label>服务地址<input value={config.base_url} placeholder="http://127.0.0.1:11434/v1" onChange={e=>change({base_url:e.target.value})}/></label>
      <small>填写 API 根地址，例如以 /v1 结尾；不要填写 /chat/completions。内网 HTTP 不加密，请仅用于可信网络。</small>
      <label>模型名称<input value={config.model} placeholder="服务端实际安装或可调用的模型 ID" onChange={e=>change({model:e.target.value})}/></label>
      <label>请求超时（秒）<input type="number" min="5" max="180" value={config.timeout_seconds} onChange={e=>change({timeout_seconds:Number(e.target.value)})}/></label>
      <small>本地模型冷启动较慢时可设为 120–180 秒。</small>
      <label>API 密钥<input type="password" autoComplete="new-password" value={key} placeholder={config.has_key?'已配置；留空保留同一服务的密钥':'本地免鉴权服务可留空'} onChange={e=>{setKey(e.target.value);setDirty(true);}}/></label>
      <label className="checkLabel"><input type="checkbox" checked={clear} onChange={e=>{setClear(e.target.checked);setDirty(true);}}/>清除已保存密钥</label>
      <p>保存新服务地址或接口类型时不会沿用旧密钥。密钥不回显，仅保存在本机；请勿把数据目录加入 Git。</p>
      <div className="settingsActions"><button className="primary" onClick={()=>run(true)}>保存配置</button><button disabled={dirty} onClick={()=>run(false)}>{busy?'正在处理…':'测试已保存的配置'}</button></div>
      <small>测试会向所选模型发送一次短请求，可能消耗额度；不会执行工具。修改后请先保存。</small>
    </fieldset>}
  </div></section>;
}

type Device = {sim_ready:boolean;registered:boolean;signal:string[];network:string[];registration:string[];calls:unknown[];owner:string|null};
export function HardwareSettings({api}:{api:API}){
  const [status,setStatus]=useState<Device|null>(null),[ports,setPorts]=useState<any>(null);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[updated,setUpdated]=useState('');
  const [query,setQuery]=useState('ping'),[results,setResults]=useState<{time:string;value:unknown}[]>([]);
  async function run(kind:string){setBusy(true);setError('');try{
    if(kind==='status'){setStatus(await api('/hardware/status'));setUpdated(new Date().toLocaleTimeString());}
    else if(kind==='ports')setPorts(await api('/hardware/ports'));
    else {const value=await api('/hardware/query',{query});setResults(old=>[{time:new Date().toLocaleTimeString(),value},...old].slice(0,12));}
  }catch(e){setError(String(e));if(kind==='status')setStatus(null);if(kind==='ports')setPorts(null);}finally{setBusy(false);}}
  useEffect(()=>{run('ports');},[]);
  return <section className="settings"><div className="panel settingsForm"><h2>SIM7600 状态与诊断</h2><p>通过本机硬件服务查询设备，查询不会拨号。真实语音测试使用上方“SIM7600 实时电话”入口。</p>
    {error&&<p className="settingsError" role="alert">{error}</p>}
    <div className="settingsActions"><button disabled={busy} onClick={()=>run('status')}>查询设备状态</button><button disabled={busy} onClick={()=>run('ports')}>刷新串口列表</button><span role="status">{busy?'查询中…':updated?'状态更新时间 '+updated:''}</span></div>
    <div className="deviceCards"><div><small>SIM 卡</small><strong>{status?(status.sim_ready?'就绪':'未就绪'):'尚未查询'}</strong></div><div><small>网络注册</small><strong>{status?(status.registered?'已注册':'未注册'):'尚未查询'}</strong></div><div><small>当前通话</small><strong>{status?status.calls.length+' 路':'尚未查询'}</strong></div></div>
    {status&&<><p>信号：{status.signal.join(' / ')} · 网络：{status.network.join(' / ')}</p><details><summary>完整状态与通话归属</summary><pre>{JSON.stringify(status,null,2)}</pre></details></>}
    <h3>串口</h3>{ports?<><p>AT：{ports.at_port} · Audio：{ports.audio_port}</p>{ports.ports.length?ports.ports.map((p:any)=><p key={p.device}><b>{p.device}</b> · {p.description}</p>):<p>未发现 SIMCom 串口，请检查 USB 连接和驱动。</p>}</>:<p>串口列表暂不可用。</p>}
    <h3>只读 AT 诊断</h3><label>查询项目<select value={query} onChange={e=>setQuery(e.target.value)}>{[['ping','AT · 通信测试'],['sim','CPIN · SIM 状态'],['signal','CSQ · 信号强度'],['registration','CEREG · LTE 注册'],['network','CPSI · 网络信息'],['calls','CLCC · 通话列表']].map(([value,label])=><option key={value} value={value}>{label}</option>)}</select></label>
    <button disabled={busy} onClick={()=>run('query')}>执行查询</button><p>通话占用中不执行诊断。诊断记录仅保留在当前页面。</p>
    {results.map((r,i)=><details key={i} open={i===0}><summary>{r.time} 查询结果</summary><pre>{JSON.stringify(r.value,null,2)}</pre></details>)}
    <details><summary>连接排查</summary><p>先运行 scripts/start-hardware.ps1 启动 8767 硬件服务。没有端口时检查 SIM7600 USB 和驱动；串口被占用时先退出独立串口工具。超时后可结束通话并重启硬件服务，再次查询。</p></details>
  </div></section>;
}
