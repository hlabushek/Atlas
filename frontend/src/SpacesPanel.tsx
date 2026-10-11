import {useEffect, useState} from 'react';
import {api, cancellable} from './api';
import {Conversation, Memory, Task} from './types';
import {AgentLauncher, Approvals, TaskTrace} from './AgentPanels';

type Space = {id:string;name:string;description:string;archived:boolean};
type Detail = {space:Space;conversations:Conversation[];memories:Memory[];tasks:Task[];notes:{id:string;title:string;content:string}[]};
type Props = {attempt:(f:()=>Promise<void>)=>Promise<void>;conversations:Conversation[];memories:Memory[];tasks:Task[];onConversation:(id:string)=>void;onRefresh:()=>Promise<void>};
const status:Record<string,string>={PENDING:'В очереди',PLANNING:'Составляет план',RUNNING:'Выполняется',WAITING_APPROVAL:'Нужно подтверждение',WAITING_DEVICE:'Ожидает устройство',PAUSED:'На паузе',COMPLETED:'Готово',FAILED:'Ошибка',CANCELLED:'Отменено'};

export function SpacesPanel({attempt,conversations,memories,tasks,onConversation,onRefresh}:Props){
 const [spaces,setSpaces]=useState<Space[]>([]),[sid,setSid]=useState(''),[detail,setDetail]=useState<Detail|null>(null),[name,setName]=useState(''),[description,setDescription]=useState(''),[showArchived,setShowArchived]=useState(false),[editing,setEditing]=useState(false),[conversation,setConversation]=useState(''),[memory,setMemory]=useState(''),[busy,setBusy]=useState(false);
 async function load(){const rows=await api<Space[]>('/spaces?limit=200');setSpaces(rows)}
 async function reload(){if(sid)setDetail(await api<Detail>('/spaces/'+sid));await onRefresh()}
 const taskVersion=tasks.map(t=>t.id+':'+t.status+':'+t.current_step).join('|');
 useEffect(()=>{void attempt(load)},[]);
 useEffect(()=>{let live=true;if(!sid){setDetail(null);return}void attempt(async()=>{const row=await api<Detail>('/spaces/'+sid);if(live)setDetail(row)});return()=>{live=false}},[sid,taskVersion]);
 async function mutate(action:()=>Promise<void>){if(busy)return;setBusy(true);await attempt(async()=>{await action();await load();await reload()});setBusy(false)}
 const shown=spaces.filter(s=>showArchived||!s.archived);
 const selected=detail?.space.id===sid?detail:null;
 return <div className="spaces-page">
  <div className="page-intro"><small>МЕСТО ДЛЯ ВАШИХ ПРОЕКТОВ</small><h1>Всё важное — вместе</h1><p>Разговоры, задачи и контекст. Без потерянных нитей.</p></div>
  <div className="spaces-layout"><div className="space-picker">
   <div className="section-heading"><h2>Ваши пространства</h2><button className="icon-button" aria-label="Создать пространство" onClick={()=>{setSid('');setName('');setDescription('');setEditing(true)}}>＋</button></div>
   {!shown.length&&<p className="empty">Создайте первое пространство для учёбы, работы или личного проекта.</p>}
   {shown.map(s=><button disabled={busy} key={s.id} aria-label={'Открыть пространство '+s.name} className={'space-card '+(sid===s.id?'selected':'')} onClick={()=>{setSid(s.id);setEditing(false)}}><span className="space-mark" aria-hidden="true">{s.name.charAt(0).toUpperCase()}</span><span><strong>{s.name}</strong><small>{s.archived?'В архиве':s.description||'Рабочее пространство'}</small></span></button>)}
   <label className="archive-toggle"><input type="checkbox" checked={showArchived} onChange={e=>setShowArchived(e.target.checked)}/>Показать архив</label>
  </div><div className="space-body">
  {editing&&<form className="memory-form surface" onSubmit={e=>{e.preventDefault();void mutate(async()=>{const row=await api<Space>('/spaces'+(sid?'/'+sid:''),sid?'PUT':'POST',{name,description,archived:selected?.space.archived||false});setSid(row.id);setEditing(false);setDetail(await api<Detail>('/spaces/'+row.id))})}}><h2>{sid?'Изменить пространство':'Новое пространство'}</h2><label>Название<input aria-label="Название пространства" required maxLength={100} value={name} onChange={e=>setName(e.target.value)}/></label><label>Описание<textarea aria-label="Описание пространства" maxLength={1000} value={description} onChange={e=>setDescription(e.target.value)}/></label><div className="actions"><button disabled={busy}>{sid?'Сохранить пространство':'Создать пространство'}</button><button type="button" className="secondary" onClick={()=>setEditing(false)}>Отмена</button></div></form>}
  {!sid&&!editing&&<div className="space-empty"><span className="space-empty-mark" aria-hidden="true">◈</span><h2>Дайте идее своё место</h2><p>Начните с названия. Добавьте разговор и выберите факты, которые Atlas должен учитывать в нём.</p><button onClick={()=>{setName('');setDescription('');setEditing(true)}}>Новое пространство</button></div>}
  {sid&&!selected&&!editing&&<p role="status">Загружаю пространство…</p>}
  {selected&&!editing&&<><div className="space-title"><div><small>{selected.space.archived?'АРХИВ':'ПРОСТРАНСТВО'}</small><h2>{selected.space.name}</h2><p>{selected.space.description||'Добавьте разговор, чтобы начать работу.'}</p></div><div className="actions"><button className="secondary" onClick={()=>{setName(selected.space.name);setDescription(selected.space.description);setEditing(true)}}>Изменить</button><button disabled={busy} className="secondary" onClick={()=>mutate(async()=>{await api('/spaces/'+sid,'PUT',{name:selected.space.name,description:selected.space.description,archived:!selected.space.archived})})}>{selected.space.archived?'Восстановить':'В архив'}</button></div></div>
   <div className="space-columns"><div>
    <section className="surface"><div className="section-heading"><h3>Разговоры</h3><span>{selected.conversations.length}</span></div>
    {selected.conversations.map(c=><div key={c.id} className="linked-row"><button className="text-button" onClick={()=>onConversation(c.id)}>{c.title}<span aria-hidden="true">↗</span></button><button className="icon-button" aria-label={'Открепить разговор '+c.title} disabled={busy} onClick={()=>mutate(async()=>{await api('/spaces/'+sid+'/conversations/'+c.id,'DELETE')})}>×</button></div>)}
    {!selected.space.archived&&<><button disabled={busy} className="secondary" onClick={()=>mutate(async()=>{const c=await api<Conversation>('/conversations','POST',{title:selected.space.name});await api('/spaces/'+sid+'/conversations/'+c.id,'PUT');onConversation(c.id)})}>Новый разговор в пространстве</button><form className="link-form" onSubmit={e=>{e.preventDefault();void mutate(async()=>{await api('/spaces/'+sid+'/conversations/'+conversation,'PUT');setConversation('')})}}><select aria-label="Добавить разговор" value={conversation} onChange={e=>setConversation(e.target.value)}><option value="">Выберите существующий разговор</option>{conversations.filter(c=>!selected.conversations.some(x=>x.id===c.id)).map(c=><option key={c.id} value={c.id}>{c.title}</option>)}</select><button disabled={busy||!conversation}>Добавить</button></form></>}
    </section>
    <section className="surface"><h3>Задачи</h3>{selected.tasks.map(t=><article className="space-task" data-task-id={t.id} key={t.id}><span className={'status-tag status-'+t.status.toLowerCase()}>{status[t.status]||t.status}</span><h4>{t.goal}</h4>{t.error&&<p role="alert">{t.error}</p>}<div className="actions">{cancellable(t.status)&&<button className="secondary" disabled={busy} onClick={()=>mutate(async()=>{await api('/tasks/'+t.id+'/cancel','POST')})}>Отменить задачу</button>}{t.status==='PAUSED'&&<button className="secondary" disabled={busy} onClick={()=>mutate(async()=>{await api('/tasks/'+t.id+'/resume','POST')})}>Продолжить</button>}</div>{t.kind==='agent'&&<TaskTrace id={t.id} version={t.status} attempt={attempt}/>}</article>)}{!selected.tasks.length&&<p className="empty">Пока нет задач. Здесь появятся запросы из разговоров пространства.</p>}
     {!selected.space.archived&&selected.conversations.length>0&&<details><summary>Поручить задачу с инструментами</summary><AgentLauncher attempt={attempt} conversationId={selected.conversations[0].id} onCreated={reload}/></details>}
    </section>
    {selected.tasks.some(t=>t.status==='WAITING_APPROVAL')&&<Approvals attempt={attempt} taskIds={selected.tasks.map(t=>t.id)}/>}
    <section className="surface"><h3>Заметки</h3>{selected.notes.map(n=><article className="agent-note" key={n.id}><h4>{n.title}</h4><pre>{n.content}</pre></article>)}{!selected.notes.length&&<p className="empty">Здесь будут результаты подтверждённых задач этого пространства.</p>}</section>
   </div><div className="space-context surface"><small>КОНТЕКСТ ДЛЯ ATLAS</small><h3>Что важно помнить</h3><p className="hint-left">До пяти выбранных фактов учитываются в новых веб-ответах связанных разговоров. Общая память остаётся доступной; это не изолированная база знаний. Связь не даёт новых разрешений.</p>
    {selected.memories.map(m=><div key={m.id} className="context-card"><p>{m.content}</p><button className="text-button" disabled={busy} onClick={()=>mutate(async()=>{await api('/spaces/'+sid+'/memory/'+m.id,'DELETE')})}>Открепить факт</button></div>)}
    {!selected.space.archived&&<form className="memory-form" onSubmit={e=>{e.preventDefault();void mutate(async()=>{await api('/spaces/'+sid+'/memory/'+memory,'PUT');setMemory('')})}}><select aria-label="Добавить факт" value={memory} onChange={e=>setMemory(e.target.value)}><option value="">Выберите запись памяти</option>{memories.filter(m=>!selected.memories.some(x=>x.id===m.id)).map(m=><option key={m.id} value={m.id}>{m.content.slice(0,100)}</option>)}</select><button disabled={busy||!memory||selected.memories.length>=5} className="secondary">Добавить в контекст</button></form>}
   </div></div>
  </>}
  </div></div>
 </div>
}
