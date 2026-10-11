import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {Conversation} from './types';

export function ConversationSidebar({selected, version, onOpen, onChanged}: {
  selected: string; version: number; onOpen:(c:Conversation)=>void;
  onChanged:(c:Conversation)=>Promise<void>;
}) {
  const [scope,setScope]=useState('active'),[query,setQuery]=useState(''),[rows,setRows]=useState<Conversation[]>([]);
  const [more,setMore]=useState(false),[loading,setLoading]=useState(false),[error,setError]=useState('');
  const [menu,setMenu]=useState(''),[editing,setEditing]=useState<Conversation|null>(null),[trash,setTrash]=useState<Conversation|null>(null);
  const [title,setTitle]=useState(''),[description,setDescription]=useState(''),[busy,setBusy]=useState(false),[localVersion,reload]=useState(0);
  const dialog=useRef<HTMLDialogElement>(null), sequence=useRef(0), trigger=useRef<HTMLButtonElement|null>(null);
  const url=(offset=0)=>`/conversations?scope=${scope}&q=${encodeURIComponent(query)}&offset=${offset}&limit=50`;
  useEffect(()=>{
    const request=++sequence.current;setLoading(true);setMenu('');
    const timer=setTimeout(()=>{api<Conversation[]>(url()).then(data=>{
      if(request===sequence.current){setRows(data);setMore(data.length===50);setLoading(false);}
    }).catch(e=>{if(request===sequence.current){setError(e.message);setLoading(false);}})},180);
    return ()=>{clearTimeout(timer);sequence.current++};
  },[scope,query,version,localVersion]);
  useEffect(()=>{
    if(editing||trash)dialog.current?.showModal();
    else {dialog.current?.close();trigger.current?.focus();}
  },[editing,trash]);
  function close(){if(busy)return;setEditing(null);setTrash(null);setError('');}
  function matchesChange(row:Conversation, values:Record<string,unknown>) {
    switch(values.action) {
      case 'trash': return row.trashed_at!==null;
      case 'archive': return row.archived&&!row.trashed_at;
      case 'restore': return !row.archived&&!row.trashed_at;
      default: return Object.entries(values).every(([key,value])=>row[key as keyof Conversation]===value);
    }
  }
  function visibleInScope(row:Conversation) {
    if(scope==='active')return !row.archived&&!row.trashed_at;
    if(scope==='archive')return row.archived&&!row.trashed_at;
    if(scope==='trash')return !!row.trashed_at;
    return true;
  }
  async function change(c:Conversation, values:Record<string,unknown>) {
    setBusy(true);setError('');
    try {
      let updated:Conversation;
      try {
        updated=await api<Conversation>('/conversations/'+c.id,'PATCH',{revision:c.revision,...values});
      } catch(writeError) {
        // A lost response must not turn a committed mutation into a misleading failure.
        if(!(writeError instanceof TypeError))throw writeError;
        try {
          const current=await api<Conversation>('/conversations/'+c.id);
          if(!matchesChange(current,values))throw writeError;
          updated=current;
        } catch(readError) {
          if(readError===writeError)throw writeError;
          throw new Error('Связь прервалась, и Atlas не смог проверить результат. Обновите страницу перед повторной попыткой.');
        }
      }
      setRows(r=>visibleInScope(updated)?r.map(row=>row.id===updated.id?updated:row):r.filter(row=>row.id!==updated.id));
      setMenu('');setEditing(null);setTrash(null);reload(n=>n+1);
      try {await onChanged(updated);}
      catch {setError('Изменение сохранено, но другие данные не обновились. Обновите страницу.');}
    } catch(e) {setError((e as Error).message);reload(n=>n+1);}
    finally {setBusy(false);}
  }
  return <section className="recent-conversations chat-library" aria-label="Библиотека разговоров">
    <div className="library-heading">Разговоры <small>{scope==='archive'?'Архив':scope==='trash'?'Корзина':''}</small></div>
    <input aria-label="Поиск разговоров" placeholder="Поиск по названию и описанию" value={query} maxLength={200} onChange={e=>setQuery(e.target.value)}/>
    <div className="library-tabs" role="group" aria-label="История разговоров">
      {([['active','Все чаты'],['archive','Архив'],['trash','Корзина']] as const).map(([id,label])=><button key={id} aria-pressed={scope===id} className={scope===id?'selected':''} onClick={()=>setScope(id)}>{label}</button>)}
    </div>
    {error&&!editing&&!trash&&<p role="alert" className="error">{error}</p>}
    <div className="conversation-list" aria-busy={loading}>
      {!loading&&!rows.length&&<p className="empty">{query?'Ничего не найдено.':scope==='active'?'Начните новый разговор.':'Здесь пока пусто.'}</p>}
      {loading&&<small>Обновление списка…</small>}
      {!loading&&rows.map(c=><div key={c.id} className="conversation-item">
        <button className={'conversation-open '+(selected===c.id?'active':'')} title={c.description||c.title} onClick={()=>onOpen(c)}>{c.pinned&&<span aria-label="Закреплён">◇ </span>}<span>{c.title}</span></button>
        <button className="conversation-menu-trigger" aria-label={'Действия: '+c.title} aria-expanded={menu===c.id} onClick={e=>{trigger.current=e.currentTarget;setMenu(menu===c.id?'':c.id)}}>⋯</button>
        {menu===c.id&&<div className="conversation-menu" role="group" aria-label={'Управление: '+c.title} onKeyDown={e=>{if(e.key==='Escape'){e.stopPropagation();setMenu('');trigger.current?.focus()}}}>
          <button disabled={busy} onClick={()=>{setTitle(c.title);setDescription(c.description);setEditing(c);setError('')}}>Переименовать и редактировать</button>
          <button disabled={busy} onClick={()=>void change(c,{pinned:!c.pinned})}>{c.pinned?'Открепить':'Закрепить'}</button>
          {c.archived||c.trashed_at?<button disabled={busy} onClick={()=>void change(c,{action:'restore'})}>Восстановить</button>:<button disabled={busy} onClick={()=>void change(c,{action:'archive'})}>В архив</button>}
          {!c.trashed_at&&<button disabled={busy} className="danger-text" onClick={()=>{setTrash(c);setError('')}}>Удалить в корзину</button>}
          <button onClick={()=>{setMenu('');trigger.current?.focus()}}>Закрыть меню</button>
        </div>}
      </div>)}
      {more&&!loading&&<button disabled={busy} className="text-button" onClick={async()=>{
        const request=sequence.current;setBusy(true);
        try {const data=await api<Conversation[]>(url(rows.length));if(request===sequence.current){setRows(r=>[...r,...data]);setMore(data.length===50)}}
        catch(e){setError((e as Error).message)}finally{setBusy(false)}
      }}>Показать более старые разговоры</button>}
    </div>
    <dialog ref={dialog} className="atlas-dialog" aria-labelledby="conversation-dialog-title" onCancel={e=>{e.preventDefault();close()}}>
      <form onSubmit={e=>{e.preventDefault();if(editing)void change(editing,{title,description});else if(trash)void change(trash,{action:'trash'})}}>
        <h2 id="conversation-dialog-title">{editing?'Редактировать разговор':'Переместить в корзину?'}</h2>
        {editing?<><label>Название<input autoFocus aria-label="Название разговора" value={title} required maxLength={100} onChange={e=>setTitle(e.target.value)}/></label><label>Описание<textarea aria-label="Описание разговора" value={description} maxLength={1000} onChange={e=>setDescription(e.target.value)}/></label><p className="hint">Меняются только название и описание. Сообщения и результаты задач остаются неизменными.</p></>:<p>«{trash?.title}» исчезнет из обычного списка. История сохранится, чат можно восстановить из корзины. Выполненные действия не отменяются.</p>}
        {error&&<p role="alert" className="error">{error}</p>}
        <div className="actions"><button disabled={busy||!!editing&&!title.trim()}>{busy?'Сохранение…':editing?'Сохранить разговор':'В корзину'}</button><button type="button" className="secondary" disabled={busy} onClick={close}>Отмена</button></div>
      </form>
    </dialog>
  </section>;
}
