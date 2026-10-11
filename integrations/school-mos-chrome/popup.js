const $=id=>document.getElementById(id);
let activeTab=null, proposed=null;
function status(message,error=false){$('status').textContent=message;$('status').classList.toggle('error',error)}
async function currentTab(){const [tab]=await chrome.tabs.query({active:true,lastFocusedWindow:true});if(!tab?.id||!tab.url)throw new Error('Не удалось определить открытую страницу');return tab}
function inspectPage(){
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!e.closest('[aria-hidden="true"],noscript,[hidden]')};
  const clean=s=>String(s||'').replace(/\s+/g,' ').trim().slice(0,100);
  const headings=[...document.querySelectorAll('h1,h2,h3,th,caption,label,[aria-label]')].filter(visible).slice(0,100).map(e=>({tag:e.tagName.toLowerCase(),label:clean(e.getAttribute('aria-label')||e.innerText||e.textContent),classes:[...e.classList].slice(0,3)})).filter(x=>x.label);
  const counts=new Map();
  for(const e of [...document.querySelectorAll('article,li,tr,[role="row"],[class]')].filter(visible).slice(0,3000)){
    const cls=[...e.classList].filter(c=>/^[A-Za-z_][\w-]{0,45}$/.test(c)).slice(0,3);
    if(!['article','li','tr'].includes(e.tagName.toLowerCase())&&!e.getAttribute('role')&&!cls.length)continue;
    const selector=e.tagName.toLowerCase()+(cls.length?'.'+cls.join('.'):''),key=selector;
    const item=counts.get(key)||{selector,count:0,children:{}};item.count++;
    for(const child of [...e.children].slice(0,20)){const t=child.tagName.toLowerCase();item.children[t]=(item.children[t]||0)+1}
    counts.set(key,item);
  }
  const repeated=[...counts.values()].filter(x=>x.count>=2).sort((a,b)=>b.count-a.count).slice(0,50);
  const links=[...document.querySelectorAll('a[href]')].filter(visible).map(a=>{
    try{const url=new URL(a.href);if(url.origin!==location.origin||!['http:','https:'].includes(url.protocol)||/\b(logout|signout|delete|remove)\b/i.test(url.pathname))return null;return {path:url.pathname.slice(0,200),label:clean(a.innerText||a.getAttribute('aria-label'))}}catch{return null}
  }).filter(x=>x&&x.label&&x.path!==location.pathname);
  const uniqueLinks=[...new Map(links.map(x=>[x.path,x])).values()].slice(0,25);
  const images=[...document.querySelectorAll('img')].filter(visible).slice(0,30).map(e=>({classes:[...e.classList].slice(0,3),parent:e.parentElement?.tagName.toLowerCase()}));
  return {origin:location.origin,path:location.pathname,title:document.title.slice(0,120),headings,repeated_groups:repeated,table_headers:[...document.querySelectorAll('table')].slice(0,8).map(t=>[...t.querySelectorAll('th')].slice(0,20).map(e=>clean(e.innerText||e.textContent))),images,links:uniqueLinks};
}
function extractWithRecipe(recipe){
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!e.closest('[aria-hidden="true"],noscript,[hidden]')};
  const rows=[...document.querySelectorAll(recipe.rows)].filter(visible).slice(0,500);
  if(!rows.length)throw new Error('Селектор записей не нашёл видимых элементов');
  const records=rows.map(row=>{
    const record={};
    for(const [name,selector] of Object.entries(recipe.fields)){
      const el=row.matches(selector)?row:row.querySelector(selector);if(!el||!visible(el))continue;
      if(el.matches('input,textarea,select,[contenteditable="true"],script,style'))continue;
      let value;
      if(name==='image_url'){
        if(!el.matches('img'))continue;
        try{const url=new URL(el.currentSrc||el.src);if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)continue;value=url.href}catch{continue}
      }else value=el.getAttribute('datetime')||el.getAttribute('aria-label')||el.getAttribute('title')||(el.matches('img')?el.alt:'')||el.innerText||'';
      value=String(value).replace(/\s+/g,' ').trim().slice(0,1000);if(value)record[name]=value;
    }
    return record;
  }).filter(r=>r[recipe.key]&&r.title);
  if(!records.length)throw new Error('В записях не найдены уникальный ключ и заголовок');
  if(new Set(records.map(r=>r[recipe.key])).size!==records.length)throw new Error('Ключи записей повторяются; синхронизация остановлена');
  return records;
}
function validateRecipe(recipe){
  try{
    if(!recipe||typeof recipe.rows!=='string'||!recipe.fields||typeof recipe.fields!=='object'||typeof recipe.key!=='string'||!recipe.fields[recipe.key]||!recipe.fields.title)throw new Error('Нужны rows, key, fields.key и fields.title');
    for(const selector of [recipe.rows,...Object.values(recipe.fields)]){if(typeof selector!=='string'||selector.length>240||/[<;]/.test(selector)||/:has|:contains|javascript|url\(/i.test(selector))throw new Error('Рецепт содержит недопустимый селектор')}
    return true;
  }catch(e){throw new Error('Проверьте черновик: '+e.message)}
}
async function atlasRequest(path,method='GET',body){
  const tabs=await chrome.tabs.query({url:'https://atlas.prism-news.xyz/*'});let tab=tabs[0];
  if(!tab){await chrome.tabs.create({url:'https://atlas.prism-news.xyz/',active:true});throw new Error('Atlas открыт в новой вкладке. Войдите в него и повторите действие.')}
  const [result]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',args:[path,method,body],func:async(p,m,b)=>{
    const response=await fetch('/api'+p,{method:m,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Atlas-Client':'web'},body:b===undefined?undefined:JSON.stringify(b)});
    let data;try{data=await response.json()}catch{throw new Error('Atlas вернул неожиданный ответ (HTTP '+response.status+')')}
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Запрос отклонён (HTTP '+response.status+')');return data;
  }});
  if(result?.error)throw new Error(result.error.message||'Не удалось связаться с Atlas');return result.result;
}
function sourceKey(){
  const host=activeTab.url?new URL(activeTab.url).hostname:'site';const path=new URL(activeTab.url).pathname.split('/').filter(Boolean).slice(0,2).join('-');
  return (host+'-'+path).toLowerCase().replace(/[^a-z0-9_-]+/g,'-').replace(/^-+/,'').slice(0,41).replace(/[-_]+$/,'')||'web-source';
}
function pageBase(){const u=new URL(activeTab.url);return {origin:u.origin+u.pathname.slice(0,200),host:u.hostname,path:u.pathname}}
async function runOnPage(func,args=[]){
  if(!activeTab?.id)activeTab=await currentTab();
  const u=new URL(activeTab.url);if(!['http:','https:'].includes(u.protocol))throw new Error('Откройте обычную веб-страницу, а не chrome:// или локальный файл');
  const [result]=await chrome.scripting.executeScript({target:{tabId:activeTab.id},func,args});
  if(result?.error)throw new Error(result.error.message||'Ошибка на странице');return result?.result;
}
async function inspect(){
  try{
    activeTab=await currentTab();const outline=await runOnPage(inspectPage);$('page').textContent=outline.origin+outline.path;
    const links=$('links');links.replaceChildren();
    if(outline.links.length){const heading=document.createElement('p');heading.textContent='Связанные страницы этого сайта (откроются в новой вкладке):';links.append(heading)}
    for(const link of outline.links){const button=document.createElement('button');button.textContent=link.label+' · '+link.path;button.addEventListener('click',()=>chrome.tabs.create({url:outline.origin+link.path,active:true}));links.append(button)}
    const sources=await atlasRequest('/connectors');const existing=sources.find(c=>!c.recipe?.kind&&c.origin===outline.origin+outline.path);
    const {connectors=[]}=await chrome.storage.local.get('connectors');$('automatic').checked=!!connectors.find(c=>c.id===existing?.id)?.automatic;
    status('Передаю цель и очищенную структуру страницы настроенной модели Atlas…');
    const result=await atlasRequest('/connectors/draft','POST',{page_url:outline.origin+outline.path,page_title:outline.title,outline:JSON.stringify(outline),request:$('goal').value.trim(),existing_recipe:existing?.recipe||null});
    const base=pageBase();proposed={id:existing?.id||sourceKey(),name:existing?.name||base.host+' · '+base.path,origin:result.origin,recipe:result.recipe,existing:!!existing};
    $('recipe').value=JSON.stringify(proposed,null,2);$('save').disabled=false;$('preview').disabled=false;$('preview-data').textContent='';
    await runOnPage(validateRecipeOnPage,[proposed.recipe]);status('Черновик готов. Проверьте поля и селекторы, затем сохраните источник.');
  }catch(e){status(e.message||String(e),true)}
}
async function preview(){
  try{const parsed=JSON.parse($('recipe').value);validateRecipe(parsed.recipe);const records=await runOnPage(extractWithRecipe,[parsed.recipe]);$('preview-data').textContent=JSON.stringify({count:records.length,first_records:records.slice(0,3)},null,2);status('Предпросмотр готов. Проверьте, что здесь только нужные данные.')}
  catch(e){$('preview-data').textContent='';status(e.message||String(e),true)}
}
function validateRecipeOnPage(recipe){
  const rows=document.querySelectorAll(recipe.rows);if(!rows.length)throw new Error('Рецепт пока не подходит открытой странице: контейнеры не найдены');
  const row=rows[0];for(const [name,selector] of Object.entries(recipe.fields)){const el=row.matches(selector)?row:row.querySelector(selector);if(!el&&name===recipe.key)throw new Error('В первой записи не найдено поле ключа '+name)}
  return true;
}
async function save(permissionPromise){
  try{const parsed=JSON.parse($('recipe').value);if(!parsed?.id||!parsed?.name||!parsed?.origin)throw new Error('Добавьте id, name и origin в JSON');validateRecipe(parsed.recipe);
    if(!proposed||parsed.id!==proposed.id||parsed.origin!==proposed.origin)throw new Error('Адрес и ID черновика менять нельзя');
    const previewRecords=await runOnPage(extractWithRecipe,[parsed.recipe]);$('preview-data').textContent=JSON.stringify({count:previewRecords.length,first_records:previewRecords.slice(0,3)},null,2);
    let automatic=false;if($('automatic').checked){automatic=!!(await permissionPromise);if(!automatic)$('automatic').checked=false}
    const saved=proposed.existing?await atlasRequest('/connectors/'+encodeURIComponent(parsed.id)+'/recipe','PUT',{recipe:parsed.recipe}):await atlasRequest('/connectors','POST',{id:parsed.id,name:parsed.name,origin:parsed.origin,recipe:parsed.recipe});
    const {connectors=[]}=await chrome.storage.local.get('connectors');const prior=connectors.find(c=>c.id===parsed.id);const stored={...prior,...saved,id:parsed.id,name:parsed.name,origin:parsed.origin,recipe:parsed.recipe,automatic:automatic||!!prior?.automatic};
    stored.automatic=automatic;await chrome.storage.local.set({connectors:[...connectors.filter(c=>c.id!==parsed.id),stored]});
    proposed={...stored,existing:true};status('Источник сохранён. Предпросмотр выше; загрузите данные отдельной кнопкой.');
  }catch(e){status(e.message||String(e),true)}
}
async function sync(){
  try{activeTab=await currentTab();const u=new URL(activeTab.url);const {connectors=[]}=await chrome.storage.local.get('connectors');
    let entry=connectors.find(c=>{const x=new URL(c.origin);return x.origin===u.origin&&u.pathname.startsWith(x.pathname)});
    if(!entry){const server=await atlasRequest('/connectors');entry=server.find(c=>{const x=new URL(c.origin);return !c.recipe?.kind&&x.origin===u.origin&&u.pathname.startsWith(x.pathname)});if(entry){connectors.push(entry);await chrome.storage.local.set({connectors})}}
    if(!entry)throw new Error('Для этого адреса ещё нет сохранённого адаптера. Сначала исследуйте страницу и сохраните его.');
    const records=await runOnPage(extractWithRecipe,[entry.recipe]);status('Передаю '+records.length+' выбранных записей в Atlas…');
    const result=await atlasRequest('/connectors/'+encodeURIComponent(entry.id)+'/import','POST',{records});status(`Готово: ${result.count} записей, новых ${result.new}, изменено ${result.changed}.`);
  }catch(e){status(e.message||String(e),true)}
}
async function transferSession(){
  try{
    if(!activeTab?.url)throw new Error('Откройте страницу сайта и повторите');
    const page=new URL(activeTab.url);if(page.protocol!=='https:')throw new Error('Передача сессии доступна только для HTTPS');
    const permission=await chrome.permissions.request({permissions:['cookies'],origins:[page.origin+'/*']});
    if(!permission)throw new Error('Доступ к cookies сайта не предоставлен');
    const sources=await atlasRequest('/connectors');const matches=sources.filter(c=>c.enabled&&new URL(c.origin).origin===page.origin&&page.pathname.startsWith(new URL(c.origin).pathname));
    if(matches.length!==1)throw new Error('Для этой страницы нужен один сохранённый источник Atlas');
    const source=matches[0];
    const raw=await chrome.cookies.getAll({url:source.origin});
    const cookies=raw.filter(c=>c.secure&&(!c.expirationDate||c.expirationDate>Date.now()/1000)).map(c=>({name:c.name,value:c.value,path:c.path||'/'}));
    if(!cookies.length)throw new Error('Для адреса источника нет действующих HTTPS cookies');
    if(cookies.length>30)throw new Error('Сайт использует больше 30 cookies; перенос остановлен');
    if(!confirm('Передать на VPS '+cookies.length+' cookies для '+page.hostname+' на 7 дней? Имена: '+cookies.map(c=>c.name).join(', ')))return;
    const result=await atlasRequest('/connectors/'+encodeURIComponent(source.id)+'/session','PUT',{cookies,days:7});
    status('Сессия передана для '+source.name+' до '+new Date(result.session_expires_at).toLocaleString('ru-RU')+'.');
  }catch(e){status(e.message||String(e),true)}
}
$('inspect').addEventListener('click',inspect);$('preview').addEventListener('click',preview);$('save').addEventListener('click',()=>{
  let permissionPromise=Promise.resolve(false);
  if($('automatic').checked&&proposed?.origin){const origin=new URL(proposed.origin).origin;if(origin.startsWith('https://'))permissionPromise=chrome.permissions.request({origins:[origin+'/*']})}
  save(permissionPromise);
});$('sync').addEventListener('click',sync);$('transfer-session').addEventListener('click',transferSession);
currentTab().then(t=>{activeTab=t;const u=new URL(t.url);$('page').textContent=u.origin+u.pathname}).catch(e=>status(e.message,true));
