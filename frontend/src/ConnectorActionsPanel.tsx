import React,{useEffect,useState} from 'react';
import {api} from './api';

type Row=Record<string,any>;
type Props={connectors:Row[],attempt:(f:()=>Promise<void>)=>Promise<void>};
const when=(value:string|null|undefined)=>value?new Date(value).toLocaleString('ru-RU'):'—';
const labels:Record<string,string>={WAITING_APPROVAL:'ожидает разрешения',APPROVED:'разрешён',STARTED:'выполняется',VERIFIED:'выполнен и проверен',UNKNOWN:'результат неизвестен — остановлено',DENIED:'отклонён',CANCELLED:'отменён'};

export function ConnectorActionsPanel({connectors,attempt}:Props){
 const [actions,setActions]=useState<Row[]>([]),[source,setSource]=useState(''),[name,setName]=useState(''),[method,setMethod]=useState('POST');
 const [target,setTarget]=useState(''),[body,setBody]=useState('{}'),[checkUrl,setCheckUrl]=useState(''),[field,setField]=useState(''),[expected,setExpected]=useState(''),[interval,setInterval]=useState('');
 const [csrfCookie,setCsrfCookie]=useState(''),[csrfHeader,setCsrfHeader]=useState('');
 const [confirmations,setConfirmations]=useState<Record<string,string>>({}),[message,setMessage]=useState('');
 async function refresh(){setActions(await api('/connector-actions'))}
 useEffect(()=>{attempt(refresh)},[]);
 return <article><h3>Действия на подключённых сайтах</h3>
  <p>Сначала передайте сессию через расширение на странице сайта. Действие задаёт точный адрес, метод и JSON. После записи Atlas отдельно читает адрес проверки и сверяет поле. При неизвестном результате действие останавливается без повтора. Для каждого запуска требуется разрешение; регулярный точный PUT можно ограниченно разрешить заранее.</p>
  <form onSubmit={e=>{e.preventDefault();attempt(async()=>{await api('/connector-actions','POST',{
    connector_id:source,name,method,target_url:target,body:JSON.parse(body),verification_url:checkUrl,
    verification_field:field,expected_value:expected,interval_minutes:interval?Number(interval):null,
    csrf_cookie_name:csrfCookie||null,csrf_header_name:csrfHeader||null});
    await refresh();setMessage('Действие сохранено. Проверьте адреса и тело запроса ниже.')})}}>
   <label>Источник<select aria-label="Источник действия" value={source} onChange={e=>setSource(e.target.value)} required><option value="">Выберите источник с сессией</option>{connectors.filter(c=>c.enabled&&c.session_active).map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
   <label>Название<input aria-label="Название действия" value={name} onChange={e=>setName(e.target.value)} maxLength={100} required/></label>
   <label>Метод<select aria-label="Метод действия" value={method} onChange={e=>setMethod(e.target.value)}><option>POST</option><option>PUT</option><option>PATCH</option></select></label>
   <label>Точный адрес записи<input aria-label="Адрес записи" type="url" value={target} onChange={e=>setTarget(e.target.value)} placeholder="https://example.org/api/item" required/></label>
   <label>Тело JSON<textarea aria-label="Тело действия" value={body} onChange={e=>setBody(e.target.value)} rows={4}/></label>
   <label>Адрес проверки GET<input aria-label="Адрес проверки" type="url" value={checkUrl} onChange={e=>setCheckUrl(e.target.value)} required/></label>
   <label>Поле проверки<input aria-label="Поле проверки" value={field} onChange={e=>setField(e.target.value)} placeholder="data.status" required/></label>
   <label>Ожидаемое значение<input aria-label="Ожидаемое значение" value={expected} onChange={e=>setExpected(e.target.value)} required/></label>
   <label>Имя CSRF cookie (если нужен)<input aria-label="Имя CSRF cookie" value={csrfCookie} onChange={e=>setCsrfCookie(e.target.value)}/></label>
   <label>CSRF заголовок<select aria-label="CSRF заголовок" value={csrfHeader} onChange={e=>setCsrfHeader(e.target.value)}><option value="">Не нужен</option><option>X-CSRFToken</option><option>X-CSRF-Token</option><option>X-XSRF-TOKEN</option></select></label>
   <label>Повтор каждые N минут (60–10080; пусто — вручную)<input aria-label="Интервал действия" type="number" min="60" max="10080" value={interval} onChange={e=>setInterval(e.target.value)}/></label>
   <button>Сохранить действие</button>
  </form>
  {message&&<p role="status">{message}</p>}
  <button className="secondary" onClick={()=>attempt(refresh)}>Обновить действия</button>
  {actions.map(a=><section key={a.id}><h4>{a.name} · {a.enabled?'включено':'выключено'}</h4>
   <p>{a.method} {a.target_url}<br/>JSON: <code>{JSON.stringify(a.body)}</code><br/>Проверка: GET {a.verification_url} → {a.verification_field} = {a.expected_value}{a.csrf_cookie_name&&<>; CSRF: cookie {a.csrf_cookie_name} → {a.csrf_header_name}</>}</p>
   <small>Срок до {when(a.expires_at)}. {a.interval_minutes?'Интервал '+a.interval_minutes+' мин; следующий запуск '+when(a.next_run_at):'Только ручной запуск'}. Автоматическое разрешение: {a.auto_approved?'до '+when(a.approval_expires_at)+'; не более '+a.max_runs+' запусков':'выключено'}.</small>
   <div className="actions"><button disabled={!a.valid} onClick={()=>attempt(async()=>{await api('/connector-actions/'+a.id+'/run','POST');await refresh()})}>Запросить запуск</button>
    {a.enabled&&a.method==='PUT'&&a.interval_minutes&&<><input aria-label={'Подтверждение действия '+a.name} placeholder={'Введите «'+a.name+'»'} value={confirmations[a.id]||''} onChange={e=>setConfirmations({...confirmations,[a.id]:e.target.value})}/><button disabled={!a.valid||confirmations[a.id]!==a.name} onClick={()=>attempt(async()=>{await api('/connector-actions/'+a.id+'/auto','PUT',{confirmation:confirmations[a.id],days:7,max_runs:10});await refresh()})}>Разрешить 10 запусков на 7 дней</button></>}
    {a.auto_approved&&<button className="secondary" onClick={()=>attempt(async()=>{await api('/connector-actions/'+a.id+'/auto','DELETE');await refresh()})}>Отозвать авторазрешение</button>}
    {a.enabled&&<button className="secondary" onClick={()=>attempt(async()=>{await api('/connector-actions/'+a.id,'DELETE');await refresh()})}>Отключить действие</button>}
   </div>
   {a.runs?.map((r:Row)=><div key={r.id}><p>Запуск {when(r.scheduled_for)}: {labels[r.status]||r.status}{r.auto_authorized?' · ограниченное авторазрешение':''}</p>
    {r.status==='WAITING_APPROVAL'&&<div className="actions"><button onClick={()=>attempt(async()=>{await api('/connector-actions/runs/'+r.id+'/decision','POST',{approve:true});await refresh()})}>Разрешить этот запуск</button><button className="secondary" onClick={()=>attempt(async()=>{await api('/connector-actions/runs/'+r.id+'/decision','POST',{approve:false});await refresh()})}>Отклонить</button></div>}
   </div>)}
  </section>)}
 </article>
}
