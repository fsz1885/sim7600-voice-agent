import React, {useEffect, useState} from 'react';
type API = (path:string, body?:unknown)=>Promise<any>;
export {ModelSettings} from './model-settings';

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
