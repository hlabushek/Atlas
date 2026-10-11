import {useState} from 'react';
import {api} from './api';

type View={url:string,title:string,screenshot:string};
type Props={source:Record<string,any>,refresh:()=>Promise<void>,attempt:(run:()=>Promise<void>)=>Promise<void>};

export function ConnectorBrowserPanel({source,refresh,attempt}:Props){
 const [view,setView]=useState<View|null>(null),[entry,setEntry]=useState(''),[address,setAddress]=useState('');
 const prefix='/connectors/'+source.id;
 async function step(body:Record<string,unknown>){setView(await api(prefix+'/browser/step','POST',body))}
 return <div className="browser-panel"><p>Сбор: {source.fetch_mode==='browser'?'Chromium на VPS':'HTTP на VPS'}{source.session_kind==='browser'?' · вход сохранён в браузере':''}. Браузерный режим нужен для страниц, которые заполняются JavaScript. Вход выполняете вы; пароль не сохраняется в настройках Atlas.</p>
 <div className="actions"><button className="secondary" disabled={!source.enabled} onClick={()=>attempt(async()=>{await api(prefix+'/fetch-mode','PUT',{mode:source.fetch_mode==='browser'?'http':'browser'});await refresh()})}>{source.fetch_mode==='browser'?'Использовать HTTP':'Использовать браузер VPS'}</button>
 <button className="secondary" disabled={!source.enabled||source.fetch_mode!=='browser'} onClick={()=>attempt(async()=>{setView(await api(prefix+'/browser/start','POST'));setAddress('')})}>Открыть вход на VPS</button>
 {view&&<><button onClick={()=>attempt(async()=>{await api(prefix+'/browser/save','POST');setView(null);setEntry('');await refresh()})}>Сохранить вход на 7 дней</button><button className="secondary" onClick={()=>attempt(async()=>{await api(prefix+'/browser','DELETE');setView(null);setEntry('')})}>Закрыть без сохранения</button></>}</div>
 {view&&<div><p>{view.title} · {view.url}</p><img alt="Страница браузера VPS" src={view.screenshot} style={{maxWidth:'100%',border:'1px solid #888',cursor:'pointer'}} onClick={event=>{const rect=event.currentTarget.getBoundingClientRect();const x=Math.round((event.clientX-rect.left)*1200/rect.width);const y=Math.round((event.clientY-rect.top)*800/rect.height);attempt(()=>step({kind:'click',x,y}))}}/>
 <form onSubmit={event=>{event.preventDefault();const value=entry;setEntry('');attempt(()=>step({kind:'type',value}))}}><input type="password" aria-label="Текст для браузера VPS" autoComplete="off" value={entry} maxLength={200} onChange={event=>setEntry(event.target.value)}/><button disabled={!entry}>Ввести в выбранное поле</button></form>
 <div className="actions">{['Tab','Enter','Backspace','Escape'].map(key=><button key={key} className="secondary" onClick={()=>attempt(()=>step({kind:'key',value:key}))}>{key}</button>)}<button className="secondary" onClick={()=>attempt(()=>step({kind:'refresh'}))}>Обновить снимок</button></div>
 <form onSubmit={event=>{event.preventDefault();attempt(()=>step({kind:'navigate',url:address}))}}><input aria-label="Адрес страницы на этом сайте" type="url" value={address} onChange={event=>setAddress(event.target.value)} placeholder={view.url}/><button disabled={!address}>Перейти</button></form></div>}
 </div>
}
