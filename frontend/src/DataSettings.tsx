import React,{useEffect,useState} from 'react';
import {api} from './api';

type DemoPlan={digest:string;counts:Record<string,number>;protected_demo_tasks:number;unknown_tasks:number;owner_memories:number};
const labels:Record<string,string>={conversations:'Чаты',messages:'Сообщения',tasks:'Задачи',notes:'Заметки',initiatives:'Инициативы',events:'События',assignments:'Задания дневника',study_tasks:'Учебные задачи',demo_homework:'Демо-домашние задания',skills:'Демо-навыки',tool_calls:'Вызовы инструментов',approvals:'Подтверждения',deliveries:'Доставки',push_deliveries:'Push-доставки',voice_requests:'Голосовые запросы',answer_fragments:'Фрагменты ответов',memories:'Демо-память',memory_facts:'Факты демо-памяти',memory_conflicts:'Конфликты демо-памяти',embeddings:'Векторы демо-памяти',space_memories:'Привязки демо-памяти'};
export function DataSettings({onReset}:{onReset:()=>Promise<void>}) {
  const [plan,setPlan]=useState<DemoPlan|null>(null),[confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState(''),[result,setResult]=useState('');
  async function load(){setError('');try{setPlan(await api<DemoPlan>('/maintenance/demo'));setConfirmation('')}catch(e){setError((e as Error).message)}}
  useEffect(()=>{void load()},[]);
  return <div className="content data-settings"><article><h2>Экспорт данных</h2><p>Разговоры, память и метаданные в JSON. Экспорт содержит личные данные — храните его в безопасном месте.</p><button onClick={async()=>{try{
    const data=await api('/export');const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download='atlas-export.json';a.click();URL.revokeObjectURL(url);
  }catch(e){setError((e as Error).message)}}}>Экспортировать данные</button></article>
    <article className="danger-zone"><h2>Очистка подтверждённого демо</h2><p>Только записи с доказанным демо-происхождением и связанные с ними данные. Смешанные чаты, реальные запросы, спорные записи, память владельца, модели, ключи и подключения сохраняются. Журнал и учёт расходов не удаляются.</p><p>Очистка необратима из интерфейса. Для восстановления нужна резервная копия сервера; эта кнопка её не создаёт.</p>
      {plan&&<><p>Сохранится записей памяти владельца: {plan.owner_memories}. Задач без доказанного происхождения: {plan.unknown_tasks}. Защищённых демо-задач: {plan.protected_demo_tasks}.</p>
      {Object.keys(plan.counts).length?<><dl className="cleanup-counts">{Object.entries(plan.counts).map(([name,count])=><div key={name}><dt>{labels[name]||name}</dt><dd>{count}</dd></div>)}</dl><form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');setResult('');try{
        const r=await api<{verified:boolean;removed:Record<string,number>}>('/maintenance/demo/reset','POST',{digest:plan.digest,confirmation});
        setResult(r.verified?'Удаление проверено. Удалено записей: '+Object.values(r.removed).reduce((a,b)=>a+b,0)+'. Реальные и спорные данные сохранены.':'Проверка не завершена');await load();await onReset();
      }catch(e){setError((e as Error).message)}finally{setBusy(false)}}}><label>Для подтверждения введите СБРОСИТЬ ДЕМО<input aria-label="Подтверждение очистки демо" value={confirmation} onChange={e=>setConfirmation(e.target.value)} autoComplete="off"/></label><button disabled={busy||confirmation!=='СБРОСИТЬ ДЕМО'}>{busy?'Проверка и очистка…':'Удалить подтверждённое демо'}</button></form></>:<p role="status">Подтверждённого демо для удаления не найдено.</p>}</>}
      <button disabled={busy} className="secondary" onClick={()=>void load()}>Пересмотреть план очистки</button>{error&&<p role="alert" className="error">{error}</p>}{result&&<p role="status">{result}</p>}
    </article>
  </div>;
}
