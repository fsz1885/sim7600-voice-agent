import React, {useEffect, useState} from 'react';

type API = (path:string, body?:unknown)=>Promise<any>;
type Config = {id:string|null;name:string;vendor:string;provider:string;base_url:string;model:string;has_key:boolean;
  timeout_seconds:number;max_tokens:number;temperature:number|null;top_p:number|null;
  frequency_penalty:number|null;presence_penalty:number|null;seed:number|null;reasoning:string};
type Library = {active_id:string;profiles:Config[]};
const vendors = [
  {id:'kimi',name:'Kimi / Moonshot',caption:'云端 · Kimi Coding / 开放平台',provider:'kimi',url:'https://api.kimi.com/coding/v1'},
  {id:'llamacpp',name:'llama.cpp',caption:'本地 / 内网部署',provider:'openai-compatible',url:'http://127.0.0.1:8080/v1'},
  {id:'ollama',name:'Ollama',caption:'本地 / 内网部署',provider:'openai-compatible',url:'http://127.0.0.1:11434/v1'},
  {id:'custom',name:'自定义提供商',caption:'OpenAI 兼容 Chat Completions',provider:'openai-compatible',url:''},
];
const blank = ():Config=>({id:null,name:'',vendor:'custom',provider:'openai-compatible',base_url:'',model:'',has_key:false,
  timeout_seconds:60,max_tokens:1600,temperature:null,top_p:null,frequency_penalty:null,presence_penalty:null,seed:null,reasoning:'default'});

export function ModelSettings({api,onSaved}:{api:API;onSaved:()=>void}){
  const [library,setLibrary]=useState<Library|null>(null),[draft,setDraft]=useState<Config|null>(null);
  const [filter,setFilter]=useState('all'),[search,setSearch]=useState(''),[key,setKey]=useState(''),[clear,setClear]=useState(false);
  const [busy,setBusy]=useState(false),[dirty,setDirty]=useState(false),[message,setMessage]=useState(''),[error,setError]=useState('');
  const [modelIds,setModelIds]=useState<string[]>([]),[deleting,setDeleting]=useState<string|null>(null);
  const [showKey,setShowKey]=useState(false),[savedKey,setSavedKey]=useState(''),[discovery,setDiscovery]=useState('');
  async function refresh(){const data=await api('/settings/models');setLibrary(data);return data as Library;}
  useEffect(()=>{refresh().catch(e=>setError(String(e)));},[]);
  async function run(fn:()=>Promise<void>){setBusy(true);setError('');setMessage('');try{await fn();}catch(e){setError(String(e));}finally{setBusy(false);}}
  function edit(config:Config){setShowKey(false);setSavedKey('');setDiscovery('');setDraft({...config});setKey('');setClear(false);setDirty(false);setModelIds([]);setDeleting(null);setMessage('');setError('');}
  function change(update:Partial<Config>){if('base_url' in update||'provider' in update){setModelIds([]);setDiscovery('');setSavedKey('');setShowKey(false);}setDraft(old=>old?{...old,...update}:old);setDirty(true);setMessage('');}
  function chooseVendor(id:string){const v=vendors.find(v=>v.id===id)!;change({vendor:id,provider:v.provider,base_url:v.url,has_key:false});setKey('');setClear(false);setModelIds([]);}
  async function save(){
    const {has_key,...body}=draft!;
    const result=await api('/settings/models',{...body,api_key:key,clear_key:clear});
    edit(result);const updated=await refresh();onSaved();setMessage(updated.active_id===result.id?'当前模型已保存，下一次请求生效。':'已保存。选择“设为当前”后用于新的任务请求。');
  }
  const active=library?.profiles.find(p=>p.id===library.active_id);
  const visible=library?.profiles.filter(p=>(filter==='all'||p.vendor===filter)&&(p.name+' '+p.model+' '+p.base_url).toLowerCase().includes(search.toLowerCase()))||[];
  return <section className="modelHub">
    <div className="modelHeader"><div><h2>模型服务</h2><p>按提供商管理连接，为语音任务选择合适的模型。</p></div><button className="primary" disabled={busy||!!draft} onClick={()=>edit(blank())}>＋ 添加模型</button></div>
    <div className="activeModel"><span className="modelDot"/><div><small>当前使用</small><strong>{active?.name||'加载中…'}</strong></div><code>{active?.model}</code><span>已保存 {library?.profiles.length||0} 个模型</span></div>
    {error&&<p role="alert" className="settingsError">{error}</p>}{message&&<p role="status" className="modelNotice">{message}</p>}
    <div className="modelLayout"><aside className="providerList"><h3>提供商</h3><button className={filter==='all'?'selected':''} onClick={()=>setFilter('all')}>全部模型 <small>{library?.profiles.length||0}</small></button>{vendors.map(v=><button key={v.id} className={filter===v.id?'selected':''} onClick={()=>setFilter(v.id)}><b>{v.name}</b><small>{library?.profiles.filter(p=>p.vendor===v.id).length||0}</small><em>{v.caption}</em></button>)}</aside>
    <div className="modelContent"><input className="modelSearch" aria-label="搜索模型" placeholder="搜索名称、模型 ID 或地址" value={search} onChange={e=>setSearch(e.target.value)}/>
      <div className="modelCards">{visible.map(p=><article className="panel modelCard" key={p.id}><div className="modelCardTitle"><div><small>{vendors.find(v=>v.id===p.vendor)?.name}</small><h3>{p.name}</h3></div>{p.id===library?.active_id&&<span className="badge">使用中</span>}</div><code>{p.model}</code><p className="modelEndpoint">{p.base_url}</p><div className="modelTags"><span>{p.has_key?'密钥已配置':'未配置密钥'}</span><span>超时 {p.timeout_seconds}s</span><span>输出 {p.max_tokens} tokens</span></div><div className="settingsActions"><button disabled={busy||!!draft} onClick={()=>edit(p)}>编辑</button><button disabled={busy||!!draft||p.id===library?.active_id} onClick={()=>run(async()=>{await api(`/settings/models/${p.id}/activate`,{});await refresh();onSaved();setMessage(`已切换至 ${p.name}`);})}>设为当前</button><button disabled={busy||!!draft} onClick={()=>run(async()=>{const r=await api(`/settings/models/${p.id}/test`,{});setMessage(`${p.name} · ${r.message} · ${r.duration_ms} ms`);})}>连接测试</button><button disabled={busy||!!draft||p.id===library?.active_id} onClick={()=>setDeleting(p.id)}>删除</button></div>{deleting===p.id&&<div className="modelNotice">删除已保存的“{p.name}”？<button disabled={busy} onClick={()=>run(async()=>{await api(`/settings/models/${p.id}/delete`,{});setDeleting(null);await refresh();})}>确认删除</button><button onClick={()=>setDeleting(null)}>取消</button></div>}</article>)}</div>
      {!visible.length&&<div className="panel"><h3>还没有匹配的模型</h3><p>添加提供商连接，保存后即可在这里切换使用。</p></div>}
    </div></div>
    {draft&&<div className="panel settingsForm modelEditor"><div className="modelHeader"><h2>{draft.id?'编辑模型':'添加模型'}</h2><button disabled={busy} onClick={()=>{setDraft(null);setDirty(false);setKey('');setShowKey(false);setSavedKey('');}}>关闭编辑</button></div><fieldset disabled={busy}><legend>连接设置</legend>
      <div className="parameterGrid"><label>提供商<select value={draft.vendor} onChange={e=>chooseVendor(e.target.value)}>{vendors.map(v=><option key={v.id} value={v.id}>{v.name}</option>)}</select></label><label>显示名称<input value={draft.name} placeholder="例如：3070 Laptop · 日常语音" onChange={e=>change({name:e.target.value})}/></label></div>
      <label>API 根地址<input value={draft.base_url} placeholder="http://主机地址:8080/v1" onChange={e=>change({base_url:e.target.value})}/></label><small>使用 /v1 等 API 根路径，程序自动追加 /chat/completions。当前支持 Kimi 与 OpenAI 兼容协议。</small>
      <label>模型 ID<input list="discovered-models" value={draft.model} placeholder="服务端实际模型 ID" onChange={e=>change({model:e.target.value})}/><datalist id="discovered-models">{modelIds.map(id=><option key={id} value={id}/>)}</datalist></label>
      <button disabled={!draft.base_url.trim()} onClick={()=>run(async()=>{setModelIds([]);setDiscovery('正在向服务器请求模型列表…');try{const {has_key,...body}=draft;const r=await api('/settings/models/discover',{...body,model:draft.model.trim()||'model-discovery',api_key:key,clear_key:clear});setModelIds(r.models);setDiscovery(r.models.length?`获取成功：服务器返回 ${r.models.length} 个模型。点击下方模型即可选择。`:'请求成功，但服务器返回空列表。请检查该账户是否有可用模型。');}catch(e){setDiscovery(String(e));}})}>{busy?'正在处理…':'从服务器获取模型列表'}</button>
      {discovery&&<p role="status" className="modelNotice">{discovery}</p>}
      {modelIds.length>0&&<div className="serverModelList" aria-label="服务器返回的模型列表">{modelIds.map(id=><button key={id} className={draft.model===id?'primary':''} onClick={()=>change({model:id})}>{id}{draft.model===id?' ✓':''}</button>)}</div>}
      <small>可在保存前获取；使用当前填写的地址与密钥，不修改已保存配置。列表可见不等于当前任务格式一定兼容。</small>
      {modelIds.length>0&&<label>服务器模型<select value={modelIds.includes(draft.model)?draft.model:''} onChange={e=>{if(e.target.value)change({model:e.target.value});}}><option value="">请选择模型</option>{modelIds.map(id=><option key={id} value={id}>{id}</option>)}</select></label>}
      <label>API Key<input type={showKey?"text":"password"} autoComplete="new-password" value={key} placeholder={draft.has_key?'已配置；留空保留该模型同一端点的密钥':'无鉴权的本地服务可留空'} onChange={e=>{setKey(e.target.value);setModelIds([]);setDirty(true);}}/></label>
      <button onClick={()=>run(async()=>{if(showKey){setShowKey(false);setSavedKey('');return;}if(!key&&draft.id){const r=await api(`/settings/models/${draft.id}/reveal-key`,{});setSavedKey(r.api_key);}setShowKey(true);})}>{showKey?'隐藏 API Key':'显示 API Key'}</button>
      {showKey&&!key&&<label>该模型已保存的 API Key<input type="text" readOnly value={savedKey} placeholder="尚未保存密钥" autoComplete="off"/></label>}
      <label className="checkLabel"><input type="checkbox" checked={clear} onChange={e=>{setClear(e.target.checked);setDirty(true);}}/>清除该模型的密钥</label>
      <details open><summary>生成参数</summary><p><small>可选参数留空时使用服务默认值。不同服务支持范围不同；修改后请运行连接测试。</small></p><div className="parameterGrid">
        <label>最大输出 tokens<input type="number" min="64" max="32768" value={draft.max_tokens} onChange={e=>change({max_tokens:Number(e.target.value)})}/></label>
        <label>请求超时 / 秒<input type="number" min="5" max="180" value={draft.timeout_seconds} onChange={e=>change({timeout_seconds:Number(e.target.value)})}/></label>
        {([['temperature','Temperature · 随机性',0,2,0.1],['top_p','Top P · 采样范围',0.01,1,0.05],['frequency_penalty','频率惩罚',-2,2,0.1],['presence_penalty','存在惩罚',-2,2,0.1],['seed','随机种子',0,2147483647,1]] as const).map(([field,label,min,max,step])=><label key={field}>{label}<input type="number" min={min} max={max} step={step} placeholder="服务默认" value={draft[field]??''} onChange={e=>change({[field]:e.target.value===''?null:Number(e.target.value)})}/></label>)}
        <label>推理强度<select disabled={draft.provider==='kimi'} value={draft.provider==='kimi'?'none':draft.reasoning} onChange={e=>change({reasoning:e.target.value})}>{[['default','服务默认（不发送参数）'],['none','关闭 / none'],['low','低 / low'],['medium','中 / medium'],['high','高 / high']].map(([v,l])=><option key={v} value={v}>{l}</option>)}</select><small>{draft.provider==='kimi'?'Kimi 电话任务固定关闭思考。':'仅用于支持 reasoning_effort 的服务。'}</small></label>
      </div></details><div className="settingsActions"><button className="primary" disabled={!draft.model.trim()||!draft.base_url.trim()} onClick={()=>run(save)}>{busy?'处理中…':'保存模型'}</button><span>{dirty?'有未保存的修改':'配置保存在本机，密钥不回显'}</span></div>
    </fieldset></div>}
  </section>;
}
