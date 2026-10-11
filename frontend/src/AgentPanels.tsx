import {useEffect,useState} from 'react';
import {api} from './api';
type Row=Record<string,any>;
type Attempt=(action:()=>Promise<void>)=>Promise<void>;
export function AgentLauncher({attempt,onCreated,conversationId}:{attempt:Attempt,onCreated:()=>Promise<void>,conversationId?:string}){
 const [goal,setGoal]=useState(''),[dry,setDry]=useState(false),[busy,setBusy]=useState(false);
 return <form className="memory-form" onSubmit={e=>{e.preventDefault();if(busy)return;setBusy(true);attempt(async()=>{
  const c=conversationId?{id:conversationId}:await api('/conversations','POST',{title:goal.slice(0,50)});
  await api('/tasks','POST',{conversation_id:c.id,request_key:crypto.randomUUID(),goal,dry_run:dry});setGoal('');await onCreated();
 }).finally(()=>setBusy(false))}}><h3>Новая инструментальная задача</h3><p className="hint-left">В демо: поиск по памяти → заметка с результатами. Создание заметки потребует вашего подтверждения.</p><textarea aria-label="Цель задачи" placeholder="Например: собрать сведения о проекте Atlas" required value={goal} maxLength={4000} onChange={e=>setGoal(e.target.value)}/><label><input type="checkbox" checked={dry} onChange={e=>setDry(e.target.checked)}/> Dry Run — только предварительный просмотр</label><button disabled={busy}>{dry?'Посмотреть план действий':'Запустить задачу'}</button></form>;
}
export function TaskTrace({id,version,attempt}:{id:string,version:string,attempt:Attempt}){
 const [detail,setDetail]=useState<Row|null>(null),[open,setOpen]=useState(false);
 useEffect(()=>{if(open)api('/tasks/'+id).then(setDetail).catch(()=>{})},[id,version,open]);
 return <div><button className="secondary" onClick={()=>{setOpen(!open);if(!open)attempt(async()=>setDetail(await api('/tasks/'+id)))}}>{open?'Скрыть шаги':'Шаги и результаты'}</button>{open&&detail&&<div className="trace">{detail.calls.map((c:Row)=><article key={c.id}><small>ШАГ {c.step+1} · {c.status} · ПОПЫТОК {c.attempts}</small><h4>{c.tool}</h4><pre>{JSON.stringify(c.arguments,null,2)}</pre>{c.result&&<pre>{JSON.stringify(c.result,null,2)}</pre>}{c.error&&<p className="error">{c.error}</p>}</article>)}{!detail.calls.length&&<p>Шаги ещё не начаты.</p>}</div>}</div>;
}
export function Approvals({attempt,taskIds}:{attempt:Attempt,taskIds?:string[]}){
 const [rows,setRows]=useState<Row[]>([]);
 async function load(){setRows(await api('/approvals'))}
 useEffect(()=>{let stopped=false;const update=()=>api<Row[]>('/approvals').then(r=>{if(!stopped)setRows(r)}).catch(()=>{});update();const timer=setInterval(update,2000);return()=>{stopped=true;clearInterval(timer)}},[]);
 const visible=taskIds?rows.filter(a=>taskIds.includes(a.task_id)&&a.status==='PENDING'):rows;
 return <div className="content approval-panel"><div className="intro"><h2>Разрешение на конкретное действие</h2><p>Проверьте инструмент и параметры. Подтверждение действует один раз; запрет инструмента имеет приоритет.</p></div><button className="secondary" onClick={()=>attempt(load)}>Обновить</button>{visible.map(a=><article key={a.id} data-approval-id={a.id}><small>{a.status} · {a.call.tool}</small><h3>{a.reason}</h3><pre>{JSON.stringify(a.call.arguments,null,2)}</pre>{a.status==='PENDING'&&<div className="actions"><button onClick={()=>attempt(async()=>{await api('/approvals/'+a.id,'POST',{approve:true});await load()})}>Разрешить действие</button><button className="secondary" onClick={()=>attempt(async()=>{await api('/approvals/'+a.id,'POST',{approve:false});await load()})}>Отклонить</button></div>}</article>)}{!visible.length&&<p className="empty">Нет операций, ожидающих подтверждения.</p>}</div>;
}
export function Tools({attempt}:{attempt:Attempt}){
 const [rows,setRows]=useState<Row[]>([]);
 async function load(){setRows(await api('/tools'))}
 useEffect(()=>{attempt(load)},[]);
 return <div className="content"><div className="intro"><h2>Доступные инструменты</h2><p>Встроенные инструменты и включённые навыки. Записи и внешние вызовы требуют отдельного подтверждения.</p></div>{rows.map(t=><article key={t.name}><small>{t.permission} · {t.blocked?'ЗАПРЕЩЁН':'ДОСТУПЕН'}</small><h3>{t.name}</h3><p>{t.description}</p><button className="secondary" onClick={()=>attempt(async()=>{await api('/tools/'+t.name+'/permission','PUT',{allowed:t.blocked});await load()})}>{t.blocked?'Снять запрет':'Запретить инструмент'}</button></article>)}</div>;
}
export function Notes(){
 const [rows,setRows]=useState<Row[]>([]);
 useEffect(()=>{let stopped=false;const update=()=>api<Row[]>('/notes').then(r=>{if(!stopped)setRows(r)}).catch(()=>{});update();const timer=setInterval(update,2000);return()=>{stopped=true;clearInterval(timer)}},[]);
 return <div><h2>Заметки, созданные агентом</h2>{rows.map(n=><article className="agent-note" key={n.id}><h3>{n.title}</h3><pre>{n.content}</pre></article>)}{!rows.length&&<p className="empty">Заметок пока нет.</p>}</div>;
}
