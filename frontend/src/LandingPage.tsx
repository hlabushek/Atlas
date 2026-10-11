import React, {useState} from 'react';
import './landing.css';

function Symbol({name='spark',className=''}:{name?:string;className?:string}){
 const paths:Record<string,React.ReactNode>={
  spark:<path d="m12 2 2.8 7.2L22 12l-7.2 2.8L12 22l-2.8-7.2L2 12l7.2-2.8Z"/>,
  voice:<><rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/></>,
  task:<><rect x="4" y="3" width="16" height="18" rx="3"/><path d="m8 9 2 2 5-5M8 16h8"/></>,
  memory:<><path d="M12 4C8 1 3 4 3 7c0 2-2 3-1 6 0 3 2 4 4 4 0 3 4 5 6 2M12 4c4-3 9 0 9 3 0 2 2 3 1 6 0 3-2 4-4 4 0 3-4 5-6 2V4M6 9c3 0 3 2 3 4M18 9c-3 0-3 2-3 4"/></>,
  shield:<><path d="m12 2 9 4v6c0 5-9 10-9 10S3 17 3 12V6Z"/><path d="m8 12 3 3 5-6"/></>,
  model:<><path d="m12 2 9 5v10l-9 5-9-5V7Z M3 7l9 5 9-5M12 12v10"/></>,
  devices:<><rect x="2" y="3" width="16" height="12" rx="2"/><path d="M5 20h10M10 15v5"/><rect x="17" y="10" width="5" height="11" rx="1"/></>,
  arrow:<path d="M5 19 19 5M5 5h14v14"/>,
 };
 return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]||paths.spark}</svg>;
}

const examples={
 voice:{label:'Голос',tag:'Пример диалога',prompt:'Атлас, что такое нейросеть?',answer:'Нейросеть — программа, которая учится на примерах. Она находит закономерности и использует их, чтобы отвечать на вопросы или решать задачи.',foot:'Коротко, по делу. Подробнее — по вашей просьбе.',icon:'voice'},
 tasks:{label:'Задачи',tag:'Пример с подтверждением',prompt:'Атлас, создай заметку с планом.',answer:'Сначала — план и точное действие. Затем — ваше подтверждение, выполнение и проверка результата.',foot:'Важное действие не проходит незаметно.',icon:'task'},
 memory:{label:'Память',tag:'Пример работы с контекстом',prompt:'Вернёмся к моему проекту.',answer:'Сохранённые вами сведения помогают продолжить разговор. Память можно посмотреть, отредактировать или удалить.',foot:'Полезный контекст, которым управляете вы.',icon:'memory'},
};
type Example = keyof typeof examples;

const features=[
 {number:'01',icon:'voice',title:'Скажите. Не набирайте.',text:'Локальное распознавание речи, короткие голосовые ответы и отдельный центр управления. Обращение «Атлас», разговорное окно или удержание клавиши.',note:'Голос на Windows',size:'wide'},
 {number:'02',icon:'task',title:'От запроса — к результату.',text:'Atlas умеет планировать задачи и вызывать разрешённые инструменты. Показывает шаги, ждёт подтверждения и проверяет результат.',note:'План · разрешение · проверка',size:''},
 {number:'03',icon:'memory',title:'Контекст не теряется.',text:'Диалоги и явная память помогают вернуться к важному. Вы можете видеть, менять и удалять сохранённые сведения.',note:'Память под вашим контролем',size:''},
 {number:'04',icon:'model',title:'Ваш выбор интеллекта.',text:'Выбирайте подключённого провайдера и модель для ответов, планирования и поиска. Расходы видны, бюджет можно ограничить.',note:'Модели и бюджет',size:'wide'},
];

export function LandingPage(){
 const [active,setActive]=useState<Example>('voice');
 const example=examples[active];
 function switchTab(event:React.KeyboardEvent<HTMLButtonElement>,key:Example){
  const keys=Object.keys(examples) as Example[];
  let index=keys.indexOf(key);
  if(event.key==='ArrowRight') index=(index+1)%keys.length;
  else if(event.key==='ArrowLeft') index=(index+keys.length-1)%keys.length;
  else if(event.key==='Home') index=0;
  else if(event.key==='End') index=keys.length-1;
  else return;
  event.preventDefault();
  setActive(keys[index]);
  document.getElementById('al-tab-'+keys[index])?.focus();
 }
 return <div className="atlas-landing">
  <a className="al-skip" href="#about-main">Перейти к содержимому</a>
  <header className="al-header al-wrap">
   <a className="al-logo" href="/about" aria-label="Atlas — о проекте"><Symbol/><span>atlas<span className="al-logo-dot">.</span></span></a>
   <nav className="al-nav" aria-label="Навигация по странице"><a href="#capabilities">Возможности</a><a href="#trust">Ваш контроль</a><a className="al-nav-login" href="/">Открыть Atlas <Symbol name="arrow"/></a></nav>
  </header>
  <main id="about-main" className="al-main">
   <section className="al-hero al-wrap" aria-labelledby="al-title">
    <div className="al-hero-copy"><div className="al-eyebrow"><span className="al-dot"/>ЛИЧНЫЙ ИИ-АССИСТЕНТ</div>
     <h1 id="al-title">Меньше рутины.<br/>Больше <em>своего.</em></h1>
     <p className="al-lead">Говорите, планируйте, возвращайтесь к важному. Atlas объединяет голос, задачи и память в одном личном пространстве.</p>
     <div className="al-hero-actions"><a className="al-button" href="/">Открыть Atlas <Symbol name="arrow"/></a><a className="al-text-link" href="#capabilities">Что он умеет <span aria-hidden="true">↓</span></a></div>
     <p className="al-hero-note"><Symbol name="shield"/>Помогает действовать. Оставляет контроль вам.</p>
    </div>
    <div className="al-showcase">
     <div className="al-showcase-top"><span><span className="al-dot"/>ATLAS VOICE</span><span>ИЛЛЮСТРАЦИЯ</span></div>
     <div className="al-orbit" aria-hidden="true"><div className="al-orbit-ring al-ring-one"/><div className="al-orbit-ring al-ring-two"/><div className="al-sphere"><Symbol/></div><span className="al-orbit-label">Голос. Контекст. Действие.</span></div>
     <div className="al-preview-tabs" role="tablist" aria-label="Примеры возможностей">{(Object.keys(examples) as Example[]).map(key=><button key={key} id={'al-tab-'+key} role="tab" tabIndex={active===key?0:-1} aria-selected={active===key} aria-controls="al-example" onKeyDown={event=>switchTab(event,key)} onClick={()=>setActive(key)}>{examples[key].label}</button>)}</div>
     <div className="al-example" id="al-example" role="tabpanel" aria-labelledby={'al-tab-'+active}>
      <div className="al-example-tag"><Symbol name={example.icon}/>{example.tag}</div><p className="al-example-prompt">«{example.prompt}»</p>
      <div className="al-example-answer"><span className="al-answer-symbol"><Symbol/></span><p>{example.answer}</p></div>
      <div className="al-example-foot"><span className="al-wave" aria-hidden="true">{[1,2,3,4,5,6,7].map(x=><i key={x}/>)}</span>{example.foot}</div>
     </div><div className="al-preview-disclaimer">Пример интерфейса. Микрофон не включается.</div>
    </div>
   </section>
   <div className="al-channel-strip al-wrap"><p>Один помощник.<br/><strong>Несколько способов быть на связи.</strong></p><div><span><Symbol name="devices"/>Веб-интерфейс</span><span><Symbol name="voice"/>Голос на Windows</span><span className="al-telegram">↗ Telegram <small>после подключения</small></span></div></div>
   <section className="al-capabilities al-wrap" id="capabilities" aria-labelledby="al-cap-title">
    <div className="al-section-heading"><div><p className="al-eyebrow">НЕ ПРОСТО ЧАТ</p><h2 id="al-cap-title">Хорош в разговоре.<br/><em>Полезен в деле.</em></h2></div><p>Не отдельные функции, а связное пространство: от первой фразы до проверенного результата.</p></div>
    <div className="al-feature-grid">{features.map(f=><article className={'al-feature '+(f.size==='wide'?'al-feature-wide':'')} key={f.number}><div className="al-feature-top"><span className="al-icon-tile"><Symbol name={f.icon}/></span><span>{f.number}</span></div><h3>{f.title}</h3><p>{f.text}</p><div className="al-feature-note"><span className="al-dot"/>{f.note}</div>{f.icon==='voice'&&<div className="al-voice-example"><span>«Атлас, сделай тише»</span><span>«Ещё на пять» <small>с обращением или в режиме продолжения</small></span></div>}{f.icon==='model'&&<div className="al-model-example"><span>Ответы</span><span>Планы</span><span>Поиск</span><span className="al-model-check">Вы выбираете ↗</span></div>}</article>)}</div>
   </section>
   <section className="al-trust" id="trust" aria-labelledby="al-trust-title"><div className="al-wrap al-trust-grid"><div><p className="al-eyebrow">ДОВЕРИЕ — ЧАСТЬ АРХИТЕКТУРЫ</p><h2 id="al-trust-title">Помощник.<br/><em>Не автопилот.</em></h2><p className="al-trust-lead">Хороший ИИ не просто умеет много.<br/>Он знает границы своих возможностей.</p><div className="al-trust-seal"><Symbol name="shield"/><span>Ваше пространство.<br/><strong>Ваши правила.</strong></span></div></div><div className="al-trust-list">
    <article><span>01</span><div><h3>Важное — с разрешения.</h3><p>Рискованные действия требуют подтверждения. Инструменты работают в пределах выданных прав, а ход задачи остаётся в журнале.</p></div></article>
    <article><span>02</span><div><h3>Вы видите, куда уходит фраза.</h3><p>Распознавание голоса работает локально. Для диалога на сервер отправляется текст; можно выбрать только локальные команды или полностью выключить микрофон.</p></div></article>
    <article><span>03</span><div><h3>Понятная приватность.</h3><p>Аудио по умолчанию не сохраняется и не отправляется. Для голосового чата доступен режим без постоянной истории текста; обработка временно использует очередь.</p></div></article>
    <article><span>04</span><div><h3>Остановить — тоже команда.</h3><p>Esc прерывает озвучку. Отдельная отмена останавливает задачу по общим правилам Atlas; уже выполненные действия автоматически не откатываются.</p></div></article>
   </div></div></section>
   <section className="al-start al-wrap" aria-labelledby="al-start-title"><div><p className="al-eyebrow">ПРОЩЕ, ЧЕМ КАЖЕТСЯ</p><h2 id="al-start-title">Начните с одной фразы.</h2><p>Откройте Atlas, выберите модель и настройте голос под себя.<br/>Никакой магии. Понятные возможности, которые можно проверить.</p></div><a className="al-button" href="/#voice">Перейти к настройке голоса <Symbol name="arrow"/></a></section>
   <section className="al-faq al-wrap" aria-label="Что важно знать"><details><summary>Нужен ли интернет?</summary><p>Локальные голосовые команды и распознавание могут работать на ПК без сервера. Серверный Atlas и внешняя модель требуют подключения; расходы зависят от выбранного провайдера.</p></details><details><summary>Все интеграции работают сразу?</summary><p>Нет. Telegram, сайты и внешние инструменты требуют настройки и разрешений. Реальный школьный дневник нужно подключать отдельно; demo не выдаётся за интеграцию.</p></details><details><summary>Atlas всегда понимает речь без ошибок?</summary><p>Нет. Точность зависит от микрофона, модели и условий. Для сравнения Vosk и Whisper есть локальная лаборатория явных проб; скорость и качество оцениваются по вашим замерам.</p></details></section>
  </main>
  <footer className="al-footer al-wrap"><a className="al-logo" href="/about"><Symbol/><span>atlas<span className="al-logo-dot">.</span></span></a><p>Личный ассистент. Осознанный контроль.</p><a href="/">Войти в своё пространство <span aria-hidden="true">↗</span></a><span>© 2026 Atlas</span></footer>
 </div>;
}
