import {useEffect,useState} from 'react';
import {api} from './api';

type Row=Record<string,any>;
type Attempt=(action:()=>Promise<void>)=>Promise<void>;
const active=(status:string)=>['PENDING','SENT','QUEUED','SNOOZED'].includes(status);
const localTime=(offsetMinutes:number)=>new Date(Date.now()+offsetMinutes*60000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);

export function InitiativeSettings({attempt,onSaved}:{attempt:Attempt;onSaved?:()=>void}){
 const [prefs,setPrefs]=useState<Row|null>(null),[saved,setSaved]=useState('');
 async function load(){setPrefs(await api('/notifications/settings'));setSaved('')}
 useEffect(()=>{attempt(load)},[]);
 if(!prefs)return <div className="content"><p>Загружаю настройки инициатив…</p></div>;
 return <div className="content"><div className="intro"><h2>Инициативы Atlas</h2><p>Важные события показываются карточками в «Активности». Отдельные чаты для них не создаются. Автоматические инициативы выключены, пока вы явно их не включите.</p></div>
  <form className="memory-form" onSubmit={e=>{e.preventDefault();attempt(async()=>{await api('/notifications/settings','PUT',prefs);await load();setSaved('Настройки сохранены. Новые решения применяются только после включения инициатив.') ;onSaved?.()})}}>
   <label><input type="checkbox" checked={!!prefs.enabled} onChange={e=>setPrefs({...prefs,enabled:e.target.checked})}/> Разрешить автоматические инициативы</label>
   <details className="model-explanation"><summary>Как Atlas решает, стоит ли отвлекать вас</summary><p>Для автособытий Atlas отправляет краткое описание кандидата модели структурированных решений RouterAI D1. Она возвращает вероятность «показывать / не показывать», а не текст. Показываются только решения с уверенностью от 0,85; при сомнении, ошибке API или исчерпанном лимите карточка не создаётся.</p><p>По умолчанию модель получает только тип события, краткое описание и срок. Последние до четырёх текстовых реплик текущего разговора не передаются, пока вы отдельно не включите это ниже. Вложения и память не передаются. Контекст не сохраняется в журнале; сохраняются модель, результат, уверенность и хеш контекста.</p></details>
   <label><input type="checkbox" checked={!!prefs.context_enabled} onChange={e=>setPrefs({...prefs,context_enabled:e.target.checked})}/> Разрешить передавать последние реплики текущего разговора для оценки контекста</label>
   <label>Часовой пояс<input aria-label="Часовой пояс инициатив" value={prefs.timezone} onChange={e=>setPrefs({...prefs,timezone:e.target.value})}/></label>
   <div className="actions"><label>Тихие часы с <input aria-label="Начало тихих часов инициатив" type="number" min="0" max="23" value={prefs.quiet_start} onChange={e=>setPrefs({...prefs,quiet_start:+e.target.value})}/></label><label>до <input aria-label="Конец тихих часов инициатив" type="number" min="0" max="23" value={prefs.quiet_end} onChange={e=>setPrefs({...prefs,quiet_end:+e.target.value})}/></label></div>
   <label>Максимум карточек в сутки<input aria-label="Лимит инициатив" type="number" min="0" max="30" value={prefs.daily_limit} onChange={e=>setPrefs({...prefs,daily_limit:+e.target.value})}/></label>
   <label>Повторов максимум<input aria-label="Повторы инициатив" type="number" min="0" max="3" value={prefs.max_repeats} onChange={e=>setPrefs({...prefs,max_repeats:+e.target.value})}/></label>
   <label>Интервал повтора, часов<input aria-label="Интервал повтора инициатив" type="number" min="1" max="168" value={prefs.repeat_hours} onChange={e=>setPrefs({...prefs,repeat_hours:+e.target.value})}/></label>
   <p className="hint-left">Модель настраивается отдельно: Настройки → Модели → «Решения об инициативах». Рекомендуемая модель — Liquid D1. Новые задачи не дают инициативам права запускать инструменты или менять данные.</p>
   <button>Сохранить настройки инициатив</button>{saved&&<p role="status">{saved}</p>}
  </form>
 </div>;
}

export function InitiativeFeed({attempt,onConversation}:{attempt:Attempt;onConversation:(id:string)=>void}){
 const [rows,setRows]=useState<Row[]>([]),[text,setText]=useState(''),[due,setDue]=useState(localTime(60));
 async function load(){setRows(await api('/initiatives'))}
 useEffect(()=>{attempt(load);const timer=setInterval(()=>api<Row[]>('/initiatives').then(setRows).catch(()=>{}),15000);return()=>clearInterval(timer)},[]);
 const visible=rows.filter(item=>active(item.status));
 async function action(id:string,kind:'complete'|'dismiss'|'snooze',hours?:number){
  await api(`/initiatives/${id}/${kind}`,'POST',kind==='snooze'?{hours}:undefined);await load();
 }
 return <section className="initiative-feed" aria-label="Карточки инициатив">
  <div className="section-heading"><div><h2>Важное для вас</h2><p>Короткие карточки вместо отдельных разговоров.</p></div><button className="secondary" onClick={()=>attempt(load)}>Обновить</button></div>
  {visible.map(item=><article className="initiative-card" key={item.id}>
   <small>{item.status==='SNOOZED'?'ОТЛОЖЕНО':item.status==='QUEUED'?'ОТПРАВКА':item.status==='PENDING'?'ОЖИДАЕТСЯ':'НОВОЕ'} · {new Date(item.created_at).toLocaleString('ru')}</small>
   <h3>{item.reason}</h3><p>{item.message}</p>
   {item.status==='SNOOZED'&&<p className="hint-left">Покажу снова {new Date(item.next_allowed).toLocaleString('ru')}.</p>}
   {item.conversation_id&&<button className="secondary" onClick={()=>onConversation(item.conversation_id)}>Открыть исходный разговор</button>}
   {item.status!=='SNOOZED'&&item.status!=='QUEUED'&&<div className="actions"><button onClick={()=>attempt(()=>action(item.id,'complete'))}>Готово</button><button className="secondary" onClick={()=>attempt(()=>action(item.id,'snooze',1))}>Через час</button><button className="secondary" onClick={()=>attempt(()=>action(item.id,'snooze',24))}>Завтра</button><button className="secondary" onClick={()=>attempt(()=>action(item.id,'dismiss'))}>Неактуально</button></div>}
  </article>)}
  {!visible.length&&<p className="empty">Пока нет карточек. Atlas покажет только события, которые модель сочтёт достаточно важными.</p>}
  <details className="initiative-reminder"><summary>Создать личное напоминание</summary><form className="memory-form" onSubmit={e=>{e.preventDefault();attempt(async()=>{await api('/initiatives','POST',{message:text,due_at:new Date(due).toISOString()});setText('');await load()})}}><label>О чём напомнить<textarea aria-label="Текст личного напоминания" required maxLength={1000} value={text} onChange={e=>setText(e.target.value)}/></label><label>Когда<input aria-label="Время личного напоминания" type="datetime-local" required value={due} onChange={e=>setDue(e.target.value)}/></label><button>Добавить карточку</button></form></details>
 </section>;
}
