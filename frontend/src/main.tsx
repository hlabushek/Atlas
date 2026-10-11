import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError, cancellable } from "./api";
import { useAtlasRealtime } from "./useAtlasRealtime";
import {
  AtlasConfig,
  Audit,
  Conversation,
  Memory,
  Message,
  PlanStep,
  SectionId,
  sections,
  Task,
} from "./types";
import "./style.css";
import "./workspace.css";
import './attachments.css';
import { SpacesPanel } from "./SpacesPanel";
import { MessageBody } from "./MessageBody";
import { ConversationSidebar } from "./ConversationSidebar";
import { SettingsNavigation, settingsSections } from "./SettingsNavigation";
import { DataSettings } from "./DataSettings";
import { useMobileDrawer } from "./useMobileDrawer";
import { Integrations, ChatExtras } from "./IntegrationsPanel";
import { AttachmentComposer, MessageAttachments, useAttachments } from './Attachments';
import { ModelSettings } from "./ModelSettingsPanel";
import { VoiceCenter } from "./VoiceCenter";
import { ToolProgress } from './ToolProgress';
import { Devices } from "./DevicesPanel";
import { Skills, Diary } from "./SkillsPanels";
import { Telegram } from "./ChannelsPanels";
import { InitiativeFeed, InitiativeSettings } from "./Initiatives";
import {
  AgentLauncher,
  TaskTrace,
  Approvals,
  Tools,
  Notes,
} from "./AgentPanels";
import { AuthScreen } from "./AuthScreen";
import { LandingPage } from "./LandingPage";
const categories = [
  "PROFILE",
  "EPISODIC",
  "PROJECTS",
  "RELATIONSHIPS",
  "PREFERENCES",
  "TASKS",
  "KNOWLEDGE",
  "AGENT_HISTORY",
];
function App() {
  const messageList = useRef<HTMLDivElement>(null);
  const [visited, setVisited] = useState({spaces:false,voice:false,models:false});
  const [conversationVersion,setConversationVersion]=useState(0),[currentConversation,setCurrentConversation]=useState<Conversation|null>(null);
  const [auth, setAuth] = useState(false),
    [authState, setAuthState] = useState<
      "loading" | "unauthenticated" | "ready" | "unavailable"
    >("loading"),
    [password, setPassword] = useState(""),
    [section, setSection] = useState<SectionId>(() =>
      window.location.hash === "#models" ? "models" : window.location.hash === "#voice" ? "voice" : "chat",
    );
  useEffect(()=>{if(section==='spaces'||section==='voice'||section==='models')setVisited(v=>v[section]?v:{...v,[section]:true})},[section]);
  useEffect(() => {
    const openModels = () => {
      if (window.location.hash === "#models") setSection("models");
      if (window.location.hash === "#voice") setSection("voice");
    };
    window.addEventListener("hashchange", openModels);
    return () => window.removeEventListener("hashchange", openModels);
  }, []);
  const [error, setError] = useState(""),
    [config, setConfig] = useState<AtlasConfig>({
      provider: "",
      model: "",
    }),
    [conversations, setConversations] = useState<Conversation[]>([]),
    [cid, setCid] = useState("");
  const drawer=useMobileDrawer(auth);
  function navigate(id:SectionId){setSection(id);drawer.setOpen(false);}
  const [messages, setMessages] = useState<Message[]>([]),
    [tasks, setTasks] = useState<Task[]>([]),
    [memories, setMemories] = useState<Memory[]>([]),
    [audit, setAudit] = useState<Audit[]>([]);
  useEffect(()=>{if(section==='chat'&&messageList.current)messageList.current.scrollTop=messageList.current.scrollHeight},[messages.at(-1)?.id,section]);
  const [taskOffset, setTaskOffset] = useState(0),
    [memoryOffset, setMemoryOffset] = useState(0),
    [auditOffset, setAuditOffset] = useState(0),
    [hasMoreTasks, setHasMoreTasks] = useState(false),
    [hasMoreMemory, setHasMoreMemory] = useState(false),
    [hasMoreAudit, setHasMoreAudit] = useState(false),
    [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [memoryText, setMemoryText] = useState(""),
    [category, setCategory] = useState("PROFILE"),
    [query, setQuery] = useState(""),
    [editId, setEditId] = useState("");
  const [theme, setTheme] = useState(
    localStorage.getItem("atlas-theme") || "light",
  );
  const [modelRole, setModelRole] = useState("auto");
  const attachments = useAttachments(cid, auth, section==='chat');
  const sendIdentity = useRef<{body:string;key:string}|null>(null);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("atlas-theme", theme);
  }, [theme]);
  async function attempt(action: () => Promise<void>) {
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function refresh() {
    const [cs, ts, ms, as] = await Promise.all([
      api<Conversation[]>("/conversations"),
      api<Task[]>("/tasks"),
      api<Memory[]>("/memory?q=" + encodeURIComponent(query)),
      api<Audit[]>("/audit"),
    ]);
    setConversations(cs);
    setTasks(ts);
    setMemories(ms);
    setAudit(as);
    setConversationVersion(n=>n+1);
    setTaskOffset(ts.length);
    setMemoryOffset(ms.length);
    setAuditOffset(as.length);
    setHasMoreTasks(ts.length === 100);
    setHasMoreMemory(ms.length === 100);
    setHasMoreAudit(as.length === 100);
  }
  async function loadMoreTasks() {
    const rows = await api<Task[]>("/tasks?limit=100&offset=" + taskOffset);
    setTasks((current) => [...current, ...rows]);
    setTaskOffset((current) => current + rows.length);
    setHasMoreTasks(rows.length === 100);
  }
  async function loadMoreMemory() {
    const rows = await api<Memory[]>(
      "/memory?limit=100&offset=" +
        memoryOffset +
        "&q=" +
        encodeURIComponent(query),
    );
    setMemories((current) => [...current, ...rows]);
    setMemoryOffset((current) => current + rows.length);
    setHasMoreMemory(rows.length === 100);
  }
  async function loadMoreAudit() {
    const rows = await api<Audit[]>("/audit?limit=100&offset=" + auditOffset);
    setAudit((current) => [...current, ...rows]);
    setAuditOffset((current) => current + rows.length);
    setHasMoreAudit(rows.length === 100);
  }
  async function loadOlderMessages() {
    if (!cid || !messages.length) return;
    const rows = await api<Message[]>(
      "/conversations/" +
        cid +
        "/messages?limit=100&before=" +
        encodeURIComponent(messages[0].created_at),
    );
    setMessages((current) => [...rows, ...current]);
    setHasOlderMessages(rows.length === 100);
  }
  async function bootstrap() {
    setAuthState("loading");
    try {
      const c = await api<AtlasConfig>("/settings");
      setConfig(c);
      setAuth(true);
      setAuthState("ready");
    } catch (e) {
      setAuth(false);
      setAuthState(
        e instanceof ApiError && e.status === 401
          ? "unauthenticated"
          : "unavailable",
      );
    }
  }
  useEffect(() => {
    void bootstrap();
  }, []);
  useEffect(() => {
    if (auth) attempt(refresh);
  }, [auth, query]);
  useEffect(() => {
    let current = true;
    if (auth && cid)
      attempt(async () => {
        const [rows,metadata] = await Promise.all([api<Message[]>(
          "/conversations/" + cid + "/messages?limit=100",
        ),api<Conversation>('/conversations/'+cid)]);
        if(current){setMessages(rows);setCurrentConversation(previous=>previous?.id===metadata.id&&previous.revision>metadata.revision?previous:metadata);setHasOlderMessages(rows.length === 100);}
      });
    return ()=>{current=false};
  }, [auth, cid]);
  const connected = useAtlasRealtime({
    enabled: auth,
    conversationId: cid,
    onTasks: setTasks,
    onMessages: setMessages,
  });
  async function newConversation() {
    const c = await api("/conversations", "POST", { title: "Новый разговор" });
    setCid(c.id);
    setCurrentConversation(c);
    setMessages([]);
    setHasOlderMessages(false);
    await refresh();
  }
  async function send() {
    if ((!text.trim() && !attachments.ready.length) || busy || attachments.blocked) return;
    setBusy(true);
    await attempt(async () => {
      let id = cid;
      if (!id) {
        const c = await api("/conversations", "POST", {
          title: text.slice(0, 50) || attachments.ready[0]?.name.slice(0,50) || 'Файлы',
        });
        id = c.id;
        attachments.move(id);
        setCid(id);
        setCurrentConversation(c);
      }
      const payload = {
        content: text,
        model_role: modelRole,
        attachment_ids: attachments.ready.map(f=>f.id),
      };
      const identity = JSON.stringify({id,...payload});
      if(sendIdentity.current?.body!==identity)sendIdentity.current={body:identity,key:crypto.randomUUID()};
      await api("/conversations/" + id + "/messages", "POST", {...payload,request_key:sendIdentity.current!.key});
      setText("");
      attachments.clear();
      sendIdentity.current=null;
      const rows = await api<Message[]>(
        "/conversations/" + id + "/messages?limit=100",
      );
      setMessages(rows);
      setHasOlderMessages(rows.length === 100);
      await refresh();
    });
    setBusy(false);
  }
  if (authState === "loading")
    return (
      <AuthScreen
        state="loading"
        password=""
        error=""
        onPasswordChange={() => {}}
        onLogin={() => {}}
        onRetry={() => {}}
      />
    );
  if (authState === "unavailable")
    return (
      <AuthScreen
        state="unavailable"
        password=""
        error=""
        onPasswordChange={() => {}}
        onLogin={() => {}}
        onRetry={() => void bootstrap()}
      />
    );
  if (!auth)
    return (
      <AuthScreen
        state="unauthenticated"
        password={password}
        error={error}
        onPasswordChange={setPassword}
        onRetry={() => void bootstrap()}
        onLogin={() =>
          attempt(async () => {
            const submittedPassword = password;
            setPassword("");
            await api("/login", "POST", { password: submittedPassword });
            setConfig(await api<AtlasConfig>("/settings"));
            setAuth(true);
            setAuthState("ready");
          })
        }
      />
    );
  return (
    <div className="shell">
      {drawer.mobile&&drawer.open&&<button className="sidebar-backdrop" aria-label="Закрыть меню Atlas" onClick={()=>drawer.setOpen(false)}/>}
      <aside id="atlas-sidebar" ref={drawer.sidebar} className={'app-sidebar '+(drawer.open?'mobile-open':'')} aria-label="Меню Atlas">
        <button className="drawer-close" aria-label="Закрыть меню" onClick={()=>drawer.setOpen(false)}>×</button>
        <div className="brand">
          A
          <span>
            Atlas<small>ВАШ ЛИЧНЫЙ ПОМОЩНИК</small>
          </span>
        </div>
        <button className="new-chat" onClick={() => attempt(async()=>{await newConversation();navigate('chat')})}>＋ Новый разговор</button>
        <nav className="utility-nav" aria-label="Личные данные">
          {sections.filter(([id])=>['activity','memory','approvals'].includes(id)).map(([id,label])=><button key={id} aria-label={label} className={section===id?'selected':''} onClick={()=>navigate(id)}>{label}{id==='activity'&&tasks.some(t=>cancellable(t.status))&&<span className="nav-count">{tasks.filter(t=>cancellable(t.status)).length}</span>}{id==='approvals'&&tasks.some(t=>t.status==='WAITING_APPROVAL')&&<span className="dot online"/>}</button>)}
        </nav>
        <ConversationSidebar selected={cid} version={conversationVersion} onOpen={c=>{setCurrentConversation(c);setCid(c.id);navigate('chat')}} onChanged={async c=>{if(c.id===cid)setCurrentConversation(c);await refresh()}}/>
        <button className="settings-entry" aria-label="Настройки" aria-pressed={settingsSections.includes(section)} onClick={()=>navigate('settings')}>⚙ Настройки</button>
        <footer>
          <span className={"dot " + (connected ? "online" : "")} />{" "}
          {connected ? "API подключён" : "Переподключение"}
          <button
            onClick={() =>
              attempt(async () => {
                await api("/logout", "POST");
                setAuth(false);
                setMessages([]);
                setMemories([]);
                setTasks([]);
                setCid("");
                setCurrentConversation(null);
                setVisited({spaces:false,voice:false,models:false});
              })
            }
          >
            Выйти
          </button>
        </footer>
      </aside>
      <main className="app-main" inert={drawer.mobile&&drawer.open}>
        <header className="app-header">
          <button ref={drawer.trigger} className="mobile-menu-button secondary" aria-label="Меню" aria-controls="atlas-sidebar" aria-expanded={drawer.open} onClick={()=>drawer.setOpen(true)}>☰</button><span className="mobile-brand">Atlas</span>
          <nav className="mode-tabs" aria-label="Режим Atlas">
            {([['chat','Разговор'],['spaces','Пространства'],['voice','Голос']] as const).map(([id,label])=><button key={id} className={section===id?'selected':''} aria-pressed={section===id} onClick={()=>setSection(id)}>{label}</button>)}
          </nav>
          <button
            className="secondary"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? "Светлая тема" : "Тёмная тема"}
          </button>
        </header>
        <div className="mode">
          <span className={'dot '+(connected?'online':'')} />
          {config.provider === "demo"
            ? "Демо-режим · без языковой модели"
            : config.provider + " · " + config.model}
          {config.model_ready === false && (
            <span>Ключ провайдера не настроен</span>
          )}
          <span>{connected ? 'События подключены' : 'Переподключение событий'}</span>
        </div>
        {settingsSections.includes(section)?<SettingsNavigation section={section} onSelect={setSection}/>:!['chat','spaces','voice'].includes(section)&&<h1 className="panel-title">{sections.find(([id])=>id===section)?.[1]}</h1>}
        {error && (
          <div className="error" role="alert">
            {error}
          </div>
        )}
        {section === "chat" && (
          <div className="chat-layout">
            <div className="chat" onDragOver={e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault()}} onDrop={e=>{if(e.dataTransfer.files.length){e.preventDefault();if(!busy)attachments.add(Array.from(e.dataTransfer.files))}}} onPaste={e=>{if(e.clipboardData.files.length){e.preventDefault();if(!busy)attachments.add(Array.from(e.clipboardData.files))}}}>
              <div className="chat-heading"><span>{currentConversation?.id===cid?currentConversation.title:conversations.find(c=>c.id===cid)?.title||'Ваш разговор с Atlas'}</span><span className="hint-left">История сохраняется</span></div>
              {currentConversation?.id===cid&&currentConversation.description&&<p className="chat-description">{currentConversation.description}</p>}
              <div className="messages" ref={messageList}>
                {hasOlderMessages && (
                  <button
                    className="secondary"
                    onClick={() => attempt(loadOlderMessages)}
                  >
                    Загрузить более старые сообщения
                  </button>
                )}
                {!messages.length && (
                  <div className="welcome">
                    <div className="orb" aria-hidden="true">A</div>
                    <small>МЕНЬШЕ СУЕТЫ. БОЛЬШЕ ЯСНОСТИ.</small>
                    <h2>Что у вас на уме?</h2>
                    <p>
                      Разберём вопрос, найдём идею или наметим следующий шаг.
                    </p>
                    <div className="chips">
                      <button className="secondary" onClick={()=>setText('Помоги составить план на сегодня')}>Спланировать день ↗</button>
                      <button className="secondary" onClick={()=>setText('Помоги разобраться в сложной теме')}>Разобраться в теме ↗</button>
                      <button className="secondary" onClick={()=>setSection('spaces')}>Начать проект ↗</button>
                    </div>
                  </div>
                )}
                {messages.map((m) => (
                  <article key={m.id} className={m.role}>
                    <small>{m.role === "user" ? "ВЫ" : "ATLAS"}</small>
                    {m.role==='assistant'?<MessageBody content={m.content}/>:<p>{m.content}</p>}
                    <MessageAttachments files={m.attachments} messageId={m.id}/>
                    <time>
                      {new Date(m.created_at).toLocaleTimeString("ru", {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </time>
                  </article>
                ))}
                {tasks
                  .filter(
                    (t) => t.conversation_id === cid && cancellable(t.status),
                  )
                  .map((t) => (
                    <div className="pending" key={t.id}>
                      {t.status === "PENDING"
                        ? "Ожидает фонового обработчика…"
                        : "Atlas готовит ответ…"}
                      {t.model_profile?.model && <small> · {t.model_profile.role==='planner'?'Вдумчивый':'Быстрый'}: {t.model_profile.model}</small>}
                      <button className="text-button" onClick={()=>attempt(async()=>{await api('/tasks/'+t.id+'/cancel','POST');await refresh()})}>Остановить</button>
                    </div>
                  ))}
                {tasks.filter(t=>t.conversation_id===cid&&t.tool_events?.length).map(t=>
                  <ToolProgress key={'tools-'+t.id} events={t.tool_events || []}/>)}
                {tasks
                  .filter(
                    (t) => t.conversation_id === cid && t.status === "FAILED",
                  )
                  .map((t) => (
                    <p role="alert" className="error" key={t.id}>
                      {t.error}
                    </p>
                  ))}
              </div>
              {tasks.some(t=>t.conversation_id===cid&&t.status==='WAITING_APPROVAL')&&<Approvals attempt={attempt} taskIds={tasks.filter(t=>t.conversation_id===cid).map(t=>t.id)}/>}
              {currentConversation?.id===cid&&(currentConversation.archived||currentConversation.trashed_at)?<div className="inactive-chat"><p>Этот разговор {currentConversation.trashed_at?'в корзине':'в архиве'}. Историю можно читать; для продолжения восстановите чат.</p><button onClick={()=>attempt(async()=>{const c=await api<Conversation>('/conversations/'+cid,'PATCH',{revision:currentConversation.revision,action:'restore'});setCurrentConversation(c);await refresh()})}>Восстановить разговор</button></div>:<>
              <AttachmentComposer controller={attachments} text={text} onText={setText} busy={busy} onSend={()=>void send()} modelRole={modelRole} onModelRole={setModelRole}/>
              <ChatExtras
                compact={drawer.mobile}
                attempt={attempt}
                onTranscript={setText}
                answer={
                  messages.filter((m) => m.role === "assistant").at(-1)
                    ?.content || ""
                }
              />
              <p className="hint">
                Atlas может ошибаться. Важные действия — с вашим подтверждением.
              </p>
              </>}
            </div>
          </div>
        )}
        {(section==='spaces'||visited.spaces)&&<div hidden={section!=='spaces'}><SpacesPanel attempt={attempt} conversations={conversations} memories={memories} tasks={tasks} onRefresh={refresh} onConversation={id=>{setCid(id);setSection('chat');void attempt(refresh)}}/></div>}
        {section === "memory" && (
          <div className="content">
            <div className="intro">
              <h2>Контекст, который остаётся</h2>
              <p>
                Подтверждённые вами сведения. Семантический поиск подключается в
                Интеграциях; конфликты разрешаете вы.
              </p>
            </div>
            <input
              aria-label="Поиск памяти"
              placeholder="Поиск по памяти…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <form
              className="memory-form"
              onSubmit={(e) => {
                e.preventDefault();
                attempt(async () => {
                  await api(
                    "/memory" + (editId ? "/" + editId : ""),
                    editId ? "PUT" : "POST",
                    { category, content: memoryText },
                  );
                  setMemoryText("");
                  setEditId("");
                  await refresh();
                });
              }}
            >
              <select
                aria-label="Категория"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
              <textarea
                aria-label="Воспоминание"
                value={memoryText}
                onChange={(e) => setMemoryText(e.target.value)}
                placeholder="Что Atlas должен помнить?"
                required
                maxLength={4000}
              />
              <button>
                {editId ? "Сохранить изменения" : "Добавить в память"}
              </button>
              {editId && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setEditId("");
                    setMemoryText("");
                  }}
                >
                  Отмена
                </button>
              )}
            </form>
            <div className="memory-grid">
              {memories.map((m) => (
                <article key={m.id}>
                  <small>
                    {m.category} · {m.source}
                  </small>
                  <p>{m.content}</p>
                  <div>
                    <button
                      className="secondary"
                      onClick={() => {
                        setEditId(m.id);
                        setMemoryText(m.content);
                        setCategory(m.category);
                      }}
                    >
                      Изменить
                    </button>
                    <button
                      className="secondary"
                      onClick={() =>
                        attempt(async () => {
                          await api("/memory/" + m.id, "DELETE");
                          await refresh();
                        })
                      }
                    >
                      Удалить
                    </button>
                  </div>
                </article>
              ))}
            </div>
            {hasMoreMemory && (
              <button
                className="secondary"
                onClick={() => attempt(loadMoreMemory)}
              >
                Показать ещё записи
              </button>
            )}
            {!memories.length && <p className="empty">Память пока пуста.</p>}
          </div>
        )}
        {section === "activity" && (
          <div className="content">
            <div className="intro">
              <h2>Работа за пределами чата</h2>
              <p>Очередь и сохранённые результаты фонового обработчика.</p>
            </div>
            <InitiativeFeed attempt={attempt} onConversation={(id)=>{setCid(id);setSection('chat')}} />
            <AgentLauncher attempt={attempt} onCreated={refresh} />
            <button className="secondary" onClick={() => attempt(refresh)}>
              Обновить
            </button>
            {tasks.map((t) => (
              <article className="task" key={t.id}>
                <small>
                  {t.status} · {t.id.slice(0, 8)}
                </small>
                <h3>{t.goal}</h3>
                {t.kind === "agent" && (
                  <p>
                    Шаг {t.current_step} / {t.plan.length}
                    {t.dry_run ? " · Dry Run" : ""}
                  </p>
                )}
                {t.plan?.map((p: PlanStep | string, i: number) => (
                  <p key={i}>
                    {i + 1}. {typeof p === "string" ? p : p.tool}
                  </p>
                ))}
                {t.error && <p className="error">{t.error}</p>}
                {[
                  "PENDING",
                  "RUNNING",
                  "PLANNING",
                  "WAITING_APPROVAL",
                ].includes(t.status) && (
                  <button
                    className="secondary"
                    onClick={() =>
                      attempt(async () => {
                        await api("/tasks/" + t.id + "/pause", "POST");
                        await refresh();
                      })
                    }
                  >
                    Пауза
                  </button>
                )}
                {t.status === "PAUSED" &&
                  !t.error?.startsWith("Неизвестный результат") && (
                    <button
                      className="secondary"
                      onClick={() =>
                        attempt(async () => {
                          await api("/tasks/" + t.id + "/resume", "POST");
                          await refresh();
                        })
                      }
                    >
                      Продолжить
                    </button>
                  )}
                {cancellable(t.status) && (
                  <button
                    className="secondary"
                    onClick={() =>
                      attempt(async () => {
                        await api("/tasks/" + t.id + "/cancel", "POST");
                        await refresh();
                      })
                    }
                  >
                    Отменить
                  </button>
                )}
                {t.kind === "agent" && (
                  <TaskTrace
                    id={t.id}
                    version={t.status + ":" + t.current_step}
                    attempt={attempt}
                  />
                )}
              </article>
            ))}
            {!tasks.length && (
              <p className="empty">Нет задач. Отправьте сообщение в чате.</p>
            )}
            {hasMoreTasks && (
              <button
                className="secondary"
                onClick={() => attempt(loadMoreTasks)}
              >
                Показать более старые задачи
              </button>
            )}
            <Notes />
            <h2>Журнал</h2>
            {audit.map((a) => (
              <p className="audit" key={a.id}>
                {a.event}{" "}
                <time>{new Date(a.created_at).toLocaleString("ru")}</time>
              </p>
            ))}
            {hasMoreAudit && (
              <button
                className="secondary"
                onClick={() => attempt(loadMoreAudit)}
              >
                Показать более старые записи журнала
              </button>
            )}
          </div>
        )}
        {section === "approvals" && <Approvals attempt={attempt} />}
        {section === "tools" && <Tools attempt={attempt} />}
        {section === "telegram" && (
          <Telegram
            attempt={attempt}
            onConversation={(id) => {
              setCid(id);
              setSection("chat");
            }}
          />
        )}
        {section === "skills" && <Skills attempt={attempt} />}
        {section === "diary" && <Diary attempt={attempt} />}
        {section === "initiative-settings" && <InitiativeSettings attempt={attempt} />}
        {section === "devices" && <Devices attempt={attempt} />}
        {(section==='voice'||visited.voice)&&<div hidden={section!=='voice'}><VoiceCenter attempt={attempt} active={section==='voice'&&auth} onTranscript={value=>{setText(value);navigate('chat')}}/></div>}
        {(['integrations','budget','data','diagnostics'] as string[]).includes(section)&&<Integrations key={section} attempt={attempt} view={section as 'integrations'|'budget'|'data'|'diagnostics'}/>}
        {section==='data'&&<DataSettings onReset={async()=>{setCid('');setCurrentConversation(null);setMessages([]);await refresh()}}/>}
        {(section==='models'||visited.models)&&<div hidden={section!=='models'}><div className="content"><ModelSettings attempt={attempt} onSaved={async()=>setConfig(await api('/settings'))}/></div></div>}
        {section === "settings" && (
          <div className="content">
            <div className="intro">
              <h2>Вы управляете Atlas</h2>
              <p>
                Выберите нужную категорию выше. Изменения моделей и разрешений не скрыты в настройках внешнего вида.
              </p>
            </div>
            <article>
              <h2>Внешний вид</h2>
              <label>Тема<select aria-label="Тема интерфейса" value={theme} onChange={e=>setTheme(e.target.value)}><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
              <p>
                Тема сохраняется в этом браузере. Черновик разговора остаётся при переключении режимов.
              </p>
            </article>
            <article><h2>Быстрый переход</h2><div className="actions"><button className="secondary" onClick={()=>setSection('models')}>Выбрать модели</button><button className="secondary" onClick={()=>setSection('voice')}>Открыть Voice Center</button><button className="secondary" onClick={()=>setSection('data')}>Управлять данными</button></div><p className="hint">Программа на ПК отвечает за микрофон, Whisper и озвучивание. Здесь — модели Atlas и серверные разрешения.</p></article>
          </div>
        )}
      </main>
    </div>
  );
}
const publicAbout = /^\/about\/?$/.test(location.pathname);
createRoot(document.getElementById("root")!).render(publicAbout ? <LandingPage /> : <App />);

if (!publicAbout && "serviceWorker" in navigator && location.port !== "5173")
  navigator.serviceWorker.register("/sw.js").catch(() => {});
