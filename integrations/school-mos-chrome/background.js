const running=new Set();

function matchConnector(url,connectors){
  let page;try{page=new URL(url)}catch{return null}
  return connectors.find(c=>{
    try{const source=new URL(c.origin);return !c.recipe?.kind&&c.automatic===true&&source.origin===page.origin&&page.pathname.startsWith(source.pathname)}catch{return false}
  })||null;
}

function extractRecords(recipe){
  const visible=e=>{const r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.visibility!=='hidden'&&s.display!=='none'&&!e.closest('[aria-hidden="true"],noscript,[hidden]')};
  const rows=[...document.querySelectorAll(recipe.rows)].filter(visible).slice(0,500);
  if(!rows.length)throw new Error('Контейнеры записей не найдены');
  const records=rows.map(row=>{
    const result={};
    for(const [name,selector] of Object.entries(recipe.fields)){
      const el=row.matches(selector)?row:row.querySelector(selector);if(!el||!visible(el)||el.matches('input,textarea,select,[contenteditable="true"],script,style'))continue;
      let raw;
      if(name==='image_url'){
        if(!el.matches('img'))continue;
        try{const url=new URL(el.currentSrc||el.src);if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)continue;raw=url.href}catch{continue}
      }else raw=el.getAttribute('datetime')||el.getAttribute('aria-label')||el.getAttribute('title')||(el.matches('img')?el.alt:'')||el.innerText||'';
      const value=String(raw).replace(/\s+/g,' ').trim().slice(0,1000);
      if(value)result[name]=value;
    }
    return result;
  }).filter(r=>r[recipe.key]&&r.title);
  if(!records.length)throw new Error('У записей нет ключа или заголовка');
  if(new Set(records.map(r=>r[recipe.key])).size!==records.length)throw new Error('Ключи записей повторяются');
  return records;
}

async function atlasRequest(path,method='GET',body){
  const tabs=await chrome.tabs.query({url:'https://atlas.prism-news.xyz/*'});const tab=tabs[0];if(!tab?.id)throw new Error('Atlas не открыт');
  const [result]=await chrome.scripting.executeScript({target:{tabId:tab.id},world:'MAIN',args:[path,method,body],func:async(p,m,b)=>{
    const response=await fetch('/api'+p,{method:m,credentials:'same-origin',headers:{'Content-Type':'application/json','X-Atlas-Client':'web'},body:b===undefined?undefined:JSON.stringify(b)});
    let data;try{data=await response.json()}catch{throw new Error('Atlas вернул HTTP '+response.status)}
    if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'HTTP '+response.status);return data;
  }});
  if(result?.error)throw new Error(result.error.message||'Ошибка Atlas');return result.result;
}

async function syncOne(tab,connector){
  if(!tab?.id||running.has(tab.id))return;running.add(tab.id);
  try{
    const origin=new URL(connector.origin).origin;
    if(!await chrome.permissions.contains({origins:[origin+'/*']}))return;
    const sources=await atlasRequest('/connectors');const registered=sources.find(c=>c.id===connector.id&&c.enabled);
    if(!registered)return;
    // Allow client-rendered pages a short time to populate; never navigate or click the source page.
    await new Promise(resolve=>setTimeout(resolve,1800));
    const [result]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:extractRecords,args:[connector.recipe]});
    if(result?.error)throw new Error(result.error.message||'Страница не прочиталась');
    const imported=await atlasRequest('/connectors/'+encodeURIComponent(connector.id)+'/import','POST',{records:result.result});
    await chrome.action.setBadgeBackgroundColor({tabId:tab.id,color:'#22863a'});
    await chrome.action.setBadgeText({tabId:tab.id,text:String(imported.count)});
    await chrome.action.setTitle({tabId:tab.id,title:`Atlas: обновлено ${imported.count}, новых ${imported.new}, изменено ${imported.changed}`});
  }catch(error){
    await chrome.action.setBadgeBackgroundColor({tabId:tab.id,color:'#b42318'});
    await chrome.action.setBadgeText({tabId:tab.id,text:'!'});
    await chrome.action.setTitle({tabId:tab.id,title:'Atlas: '+String(error?.message||'ошибка автообновления').slice(0,100)});
  }finally{running.delete(tab.id)}
}

async function scanTabs(){
  const {connectors=[]}=await chrome.storage.local.get('connectors');
  const automatic=connectors.filter(c=>c.automatic&&c.enabled!==false);if(!automatic.length)return;
  for(const connector of automatic){
    const origin=new URL(connector.origin).origin;
    if(!await chrome.permissions.contains({origins:[origin+'/*']}))continue;
    const tabs=await chrome.tabs.query({url:[origin+'/*']});
    for(const tab of tabs){const match=matchConnector(tab.url||'',[connector]);if(match)syncOne(tab,match)}
  }
}

chrome.tabs.onUpdated.addListener((tabId,changeInfo,tab)=>{
  if(changeInfo.status!=='complete')return;
  chrome.storage.local.get('connectors').then(({connectors=[]})=>{const match=matchConnector(tab.url||'',connectors);if(match)syncOne(tab,match)});
});

chrome.alarms.create('atlas-connector-refresh',{periodInMinutes:15});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='atlas-connector-refresh')scanTabs()});
