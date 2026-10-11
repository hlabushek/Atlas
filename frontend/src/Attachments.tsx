import React, {useEffect, useRef, useState} from 'react';
import {api} from './api';

export interface FileInfo {
  id: string; name: string; mime: string; bytes: number; text_bytes: number; warnings: string[]; width?:number; height?:number;
}
interface Limits {extensions: string[]; max_files: number; document_bytes: number; text_file_bytes: number; context_bytes: number; roles:Record<string,{model:string;vision:boolean}>}
interface Draft {key: string; scope: string; file?: File; info?: FileInfo; state: 'uploading'|'ready'|'error'; progress: number; error?: string}
export const fileSize = (bytes: number) => bytes < 1024 ? `${bytes} Б` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} КБ` : `${(bytes / 1048576).toFixed(1)} МБ`;

export function useAttachments(scope: string, enabled: boolean, active=true) {
  const [all, setAll] = useState<Draft[]>([]), [limits, setLimits] = useState<Limits|null>(null);
  const jobs = useRef(new Map<string, XMLHttpRequest>());
  const rows = useRef<Draft[]>([]);
  const queued = useRef<{scope:string;files:File[]}[]>([]);
  const update = (f: (v: Draft[])=>Draft[]) => {rows.current = f(rows.current); setAll(rows.current)};
  useEffect(()=>{let alive=true;if(enabled&&active)api<Limits>('/files/capabilities').then(v=>{if(alive)setLimits(v)}).catch(()=>{if(alive){setLimits(null);window.dispatchEvent(new CustomEvent('atlas-file-error',{detail:'Не удалось получить параметры вложений. Вернитесь в разговор или обновите страницу.'}))}});return()=>{alive=false}},[enabled,active]);
  useEffect(()=>{if(limits){const pending=queued.current.splice(0);pending.forEach(q=>add(q.files,q.scope))}},[limits]);
  useEffect(()=>()=>{jobs.current.forEach(xhr=>xhr.abort())},[]);
  useEffect(()=>{if(!enabled){jobs.current.forEach(xhr=>xhr.abort());jobs.current.clear();queued.current=[];update(()=>[]);setLimits(null)}},[enabled]);
  function upload(key: string, file: File) {
    const xhr = new XMLHttpRequest(); jobs.current.set(key, xhr);
    const patch = (v: Partial<Draft>) => update(rows=>rows.map(r=>r.key===key?{...r,...v}:r));
    patch({state:'uploading',progress:0,error:undefined});
    xhr.open('POST','/api/files'); xhr.setRequestHeader('X-Atlas-Client','web'); xhr.timeout=60000;
    xhr.upload.onprogress=e=>{if(e.lengthComputable)patch({progress:Math.round(e.loaded/e.total*100)})};
    const fail = (error: string)=>{jobs.current.delete(key);patch({state:'error',error})};
    xhr.onerror=()=>fail('Сеть недоступна. Повторите загрузку');
    xhr.ontimeout=()=>fail('Сервер не успел обработать файл. Повторите загрузку');
    xhr.onload=()=>{jobs.current.delete(key);try{
      const body=JSON.parse(xhr.responseText);
      if(xhr.status<200||xhr.status>=300){fail(typeof body.detail==='string'?body.detail:'Не удалось загрузить файл');return}
      patch({state:'ready',info:body,progress:100});
    }catch{fail('Сервер временно недоступен. Повторите загрузку')}};
    const body=new FormData(); body.append('file',file); xhr.send(body);
  }
  function add(files: File[], target=scope) {
    if(!limits){queued.current.push({scope:target,files});setAll([...rows.current]);window.dispatchEvent(new CustomEvent('atlas-file-error',{detail:'Подготавливаю вложения…'}));return}
    for(const file of files){
      const key=crypto.randomUUID(), count=rows.current.filter(r=>r.scope===target).length;
      if(count>=limits.max_files){window.dispatchEvent(new CustomEvent('atlas-file-error',{detail:`В одном сообщении до ${limits.max_files} файлов`}));break}
      const extension=file.name.split('.').at(-1)?.toLowerCase()||'';
      const maximum=['txt','md','csv','json'].includes(extension)?limits.text_file_bytes:limits.document_bytes;
      const error=!limits.extensions.includes(extension)?'Этот формат не поддерживается. Выберите PDF, DOCX, текстовый файл, PNG, JPEG или WebP':file.size>maximum?`Файл слишком большой. Максимум ${fileSize(maximum)}`:undefined;
      update(rows=>[...rows,{key,scope:target,file,state:error?'error':'uploading',progress:0,error}]);
      if(!error)upload(key,file);
    }
  }
  const drafts=all.filter(r=>r.scope===scope);
  return {drafts,limits,add,
    ready:drafts.filter(r=>r.state==='ready').map(r=>r.info!),
    blocked:queued.current.some(q=>q.scope===scope)||drafts.some(r=>r.state!=='ready')||drafts.reduce((n,r)=>n+(r.info?.text_bytes||0),0)>(limits?.context_bytes||100000),
    refresh:()=>api<Limits>('/files/capabilities').then(setLimits).catch(()=>window.dispatchEvent(new CustomEvent('atlas-file-error',{detail:'Параметры вложений недоступны. Проверьте соединение и повторите.'}))),
    remove:(key:string)=>{jobs.current.get(key)?.abort();jobs.current.delete(key);update(rows=>rows.filter(r=>r.key!==key))},
    retry:(key:string)=>{const row=rows.current.find(r=>r.key===key);if(row?.file)upload(key,row.file)},
    clear:()=>{const keys=new Set(drafts.map(r=>r.key));update(rows=>rows.filter(r=>!keys.has(r.key)))},
    move:(target:string)=>update(rows=>rows.map(r=>r.scope===scope?{...r,scope:target}:r)),
    attach:(info:FileInfo)=>{if(!limits||drafts.length>=limits.max_files)return;if(!drafts.some(r=>r.info?.id===info.id))update(rows=>[...rows,{key:crypto.randomUUID(),scope,info,state:'ready',progress:100}])},
  };
}
type Controller=ReturnType<typeof useAttachments>;
function AttachmentInfo({onClose}:{onClose:()=>void}){
 const ref=useRef<HTMLDialogElement>(null);useEffect(()=>{ref.current?.showModal();return()=>ref.current?.close()},[]);
 return <dialog ref={ref} className="file-preview attachment-info-dialog" aria-label="О вложениях" onCancel={onClose} onClose={onClose}><header><h2>Вложения в Atlas</h2><button type="button" aria-label="Закрыть информацию" onClick={onClose}>×</button></header><p>Нажмите ＋, перетащите файл в чат или вставьте скриншот Ctrl+V. До пяти файлов в сообщении.</p><dl><dt>Картинки</dt><dd>PNG, JPEG, WebP · до 5 МиБ и 16 Мп. Для понимания изображения нужна vision-модель. «Авто» выбирает совместимую настроенную модель.</dd><dt>Документы</dt><dd>PDF и DOCX · до 5 МиБ, PDF до 50 страниц. Читается извлечённый текст, без изображений и OCR сканов.</dd><dt>Текст</dt><dd>TXT, MD, CSV, JSON · UTF-8, до 64 КиБ. Общий текст вложений и вопроса — до 100 КБ.</dd></dl><p>Загрузка хранит файл на сервере Atlas, но не отправляет его модели. Отправка происходит только вместе с сообщением. Изображения передаются без EXIF, до 2048 px. Проверьте файлы на личные данные и секреты.</p><p>× убирает файл из черновика. Удаление из библиотеки запрещает новые прикрепления, но не стирает вложения уже отправленных сообщений.</p></dialog>;
}
function FileIcon({file,messageId}:{file:FileInfo;messageId?:string}){return <span className="file-icon" aria-hidden="true">{file.mime.startsWith('image/')?<img alt="" src={'/api/files/'+file.id+'/image'+(messageId?'?message_id='+messageId:'')}/>:file.name.split('.').at(-1)?.toUpperCase()}</span>}

function FilePreview({file,messageId,onClose}:{file:FileInfo;messageId?:string;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null),[text,setText]=useState(''),[error,setError]=useState('');
  const query=messageId?'?message_id='+encodeURIComponent(messageId):'';
  useEffect(()=>{dialog.current?.showModal();let active=true;
    api<{text:string}>('/files/'+file.id+'/preview'+query).then(r=>{if(active)setText(r.text)}).catch(e=>{if(active)setError(e.message)});
    return()=>{active=false;dialog.current?.close()};
  },[file.id,query]);
  return <dialog ref={dialog} className="file-preview" aria-labelledby="file-preview-title" onCancel={onClose} onClose={onClose}>
    <header><div><h2 id="file-preview-title">{file.name}</h2><small>{fileSize(file.bytes)} · {file.mime.startsWith('image/')?`${file.width} × ${file.height}`:'Извлечённый текст'}</small></div><button type="button" onClick={onClose} aria-label="Закрыть предпросмотр">×</button></header>
    {file.warnings?.map(w=><p className="hint" key={w}>{w}</p>)}
    {file.mime.startsWith('image/')?<img className="file-image-preview" src={'/api/files/'+file.id+'/image'+query} alt={file.name}/>:error?<p role="alert">{error}</p>:text?<pre>{text}</pre>:<p role="status">Загружаю текст…</p>}
    <a href={'/api/files/'+file.id+query}>Скачать исходный файл</a>
  </dialog>;
}

export function MessageAttachments({files,messageId}:{files?:FileInfo[]|null;messageId:string}) {
  const [preview,setPreview]=useState<FileInfo|null>(null);
  if(!files?.length)return null;
  return <><div className="message-files">{files.map(f=><div className="file-card" key={f.id}><button type="button" className="file-open" onClick={()=>setPreview(f)} aria-label={'Открыть файл '+f.name}><FileIcon file={f} messageId={messageId}/><span><strong>{f.name}</strong><small>{fileSize(f.bytes)} · {f.mime.startsWith('image/')?'Изображение':f.warnings?.length?'Только текст':'Файл'}</small></span></button><a aria-label={'Скачать '+f.name} href={'/api/files/'+f.id+'?message_id='+messageId}>↓</a></div>)}</div>{preview&&<FilePreview file={preview} messageId={messageId} onClose={()=>setPreview(null)}/>}</>;
}

export function AttachmentComposer({controller,text,onText,busy,onSend,modelRole,onModelRole}:{controller:Controller;text:string;onText:(s:string)=>void;busy:boolean;onSend:()=>void;modelRole:string;onModelRole:(s:string)=>void}) {
  const input=useRef<HTMLInputElement>(null), libraryDialog=useRef<HTMLDialogElement>(null);
  const [drag,setDrag]=useState(false),[notice,setNotice]=useState(''),[preview,setPreview]=useState<FileInfo|null>(null),[library,setLibrary]=useState<FileInfo[]>([]),[showLibrary,setShowLibrary]=useState(false),[showInfo,setShowInfo]=useState(false),[libraryError,setLibraryError]=useState('');
  const {drafts,limits}=controller;
  useEffect(()=>{if(limits)setNotice('')},[limits]);
  useEffect(()=>{const handle=(e:Event)=>setNotice((e as CustomEvent<string>).detail);window.addEventListener('atlas-file-error',handle);return()=>window.removeEventListener('atlas-file-error',handle)},[]);
  useEffect(()=>{if(showLibrary){libraryDialog.current?.showModal();api<FileInfo[]>('/files').then(setLibrary).catch(e=>setLibraryError(e.message))}},[showLibrary]);
  function add(files:File[]){if(busy)return;setNotice('');controller.add(files)}
  const total=controller.ready.reduce((n,f)=>n+f.text_bytes,0)+new TextEncoder().encode(text).length;
  const oversized=total>(limits?.context_bytes||100000);
  const hasImages=controller.ready.some(f=>f.mime.startsWith('image/'));
  const imageRole=modelRole==='auto'?['main','planner'].find(r=>limits?.roles?.[r]?.vision):limits?.roles?.[modelRole]?.vision?modelRole:undefined;
  const visionBlocked=hasImages&&!imageRole;
  return <><form className={'composer attachment-composer'+(drag?' dragging':'')} onSubmit={e=>{e.preventDefault();if(!controller.blocked&&!oversized&&!visionBlocked)onSend()}}
    onDragOver={e=>{if(e.dataTransfer.types.includes('Files')){e.preventDefault();setDrag(true)}}} onDragLeave={e=>{if(!e.currentTarget.contains(e.relatedTarget as Node))setDrag(false)}}
    onDrop={e=>{e.preventDefault();e.stopPropagation();setDrag(false);add(Array.from(e.dataTransfer.files))}}>
    {drafts.length>0&&<div className="draft-files" aria-label="Вложения черновика">{drafts.map(d=><div className={'file-card '+d.state} key={d.key}>
      <button type="button" className="file-open" disabled={!d.info} onClick={()=>d.info&&setPreview(d.info)}><>{d.info?<FileIcon file={d.info}/>:<span className="file-icon" aria-hidden="true">{d.file?.name.split('.').at(-1)?.toUpperCase()}</span>}</><span><strong>{d.info?.name||d.file?.name}</strong><small role="status">{d.state==='uploading'?(d.progress===100?'Обрабатываю текст…':`Загрузка ${d.progress}%`):d.state==='ready'?`${fileSize(d.info!.bytes)} · Готов${d.info!.mime.startsWith('image/')?' · Картинка':d.info!.warnings?.length?' · Только текст':''}`:'Ошибка загрузки'}</small></span></button>
      <button type="button" disabled={busy} aria-label={'Убрать '+(d.info?.name||d.file?.name)} onClick={()=>controller.remove(d.key)}>×</button>
      {d.state==='error'&&<div className="file-error"><span role="alert">{d.error}</span><button type="button" disabled={busy} onClick={()=>controller.retry(d.key)}>Повторить</button></div>}
    </div>)}</div>}
    {drag&&<div className="drop-hint">Отпустите файлы, чтобы прикрепить</div>}
    <div className="composer-row">
      <input ref={input} type="file" multiple hidden accept={limits?.extensions.map(e=>'.'+e).join(',')} aria-label="Выбрать файлы" onChange={e=>{add(Array.from(e.target.files||[]));e.target.value=''}}/>
      <textarea aria-label="Сообщение" placeholder={drafts.length?'Что сделать с файлами?':'Напишите Atlas…'} value={text} onChange={e=>onText(e.target.value)} disabled={busy} maxLength={16000}
        onPaste={e=>{if(e.clipboardData.files.length){e.preventDefault();e.stopPropagation();add(Array.from(e.clipboardData.files))}}}
        onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!controller.blocked&&!oversized&&!visionBlocked)onSend()}}}/>
    </div>
    <div className="attachment-toolbar">
      <button type="button" className="attach-button icon-button" disabled={busy||!limits||drafts.length>=limits.max_files} aria-label="Прикрепить файлы" title="Прикрепить файлы · перетащить · Ctrl+V" onClick={()=>input.current?.click()}>＋</button>
      <button type="button" className="library-button icon-button" aria-label="Из библиотеки" title="Из библиотеки" disabled={busy||!limits||drafts.length>=limits.max_files} onClick={()=>{setLibraryError('');setShowLibrary(true)}}><svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 9h18"/></svg></button>
      <select className="composer-model" aria-label="Режим ответа" title="Режим ответа" value={modelRole} onChange={e=>onModelRole(e.target.value)} disabled={busy}><option value="auto">Авто</option><option value="main">Быстрый</option><option value="planner">Вдумчивый</option></select>
      <button type="button" className="file-info-button icon-button" aria-label="О вложениях" title="Форматы, лимиты и приватность" onClick={()=>setShowInfo(true)}>i</button>
      <button className="send-button" disabled={busy||controller.blocked||oversized||visionBlocked||(!text.trim()&&!controller.ready.length)} aria-label="Отправить сообщение" title="Отправить · Enter">↑</button>
    </div>
    {(notice||oversized)&&<p role="alert" className="file-notice">{oversized?'Слишком много текста: до 100 КБ на сообщение. Разделите файлы.':notice} {!limits&&<button type="button" className="text-button" onClick={()=>void controller.refresh()}>Повторить подключение</button>}</p>}
    {hasImages&&<p className={'file-privacy image-route'+(visionBlocked?' incompatible':'')}>{imageRole?`Картинки → ${limits?.roles?.[imageRole]?.model}`:'Модель не поддерживает изображения или каталог недоступен.'} {visionBlocked&&modelRole!=='auto'&&<button type="button" className="text-button" onClick={()=>onModelRole('auto')}>Выбрать Авто</button>}</p>}

  </form>{showInfo&&<AttachmentInfo onClose={()=>setShowInfo(false)}/>} {preview&&<FilePreview file={preview} onClose={()=>setPreview(null)}/>}
  {showLibrary&&<dialog ref={libraryDialog} className="file-preview" aria-label="Библиотека файлов" onCancel={()=>setShowLibrary(false)} onClose={()=>setShowLibrary(false)}><header><h2>Библиотека файлов</h2><button aria-label="Закрыть библиотеку" onClick={()=>setShowLibrary(false)}>×</button></header><p>Прикрепите ранее загруженный файл к этому сообщению. PNG/JPEG/WebP и PDF/DOCX до 5 МиБ; TXT/MD/CSV/JSON до 64 КиБ. До 5 файлов.</p>{libraryError&&<p role="alert">{libraryError}</p>}{!library.length&&!libraryError&&<p>Загруженных файлов нет.</p>}{library.map(f=><button className="library-file" key={f.id} onClick={()=>{controller.attach(f);setShowLibrary(false)}}>{f.name} · {fileSize(f.bytes)}</button>)}</dialog>}</>;
}
