// Только публичное чтение: ни входа, ни платных запросов, ни микрофона.
import {chromium} from '@playwright/test';
import {resolve} from 'node:path';
import assert from 'node:assert/strict';

const url=process.env.ATLAS_PUBLIC_ABOUT_URL||'https://atlas.prism-news.xyz/about';
const browser=await chromium.launch();
try {
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
 const errors=[],apiRequests=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(message.type()==='error') errors.push(message.text());});
 page.on('request',request=>{if(new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url());});
 const response=await page.goto(url);
 assert.equal(response.status(),200);
 await page.getByRole('heading',{level:1}).waitFor();
 assert.match(await page.title(),/Atlas — голос/);
 await page.getByRole('tab',{name:'Задачи',exact:true}).click();
 assert.match(await page.getByRole('tabpanel').innerText(),/ваше подтверждение/);
 await page.getByRole('tab',{name:'Голос',exact:true}).click();
 assert.equal(await page.evaluate(()=>getComputedStyle(document.body).margin),'0px');
 await page.screenshot({path:resolve('../.sessions/atlas-about-live-desktop.png'),fullPage:true});
 await page.setViewportSize({width:390,height:844});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
 await page.screenshot({path:resolve('../.sessions/atlas-about-live-mobile.png'),fullPage:true});
 assert.deepEqual(apiRequests,[]);
 assert.deepEqual(errors,[]);
 await page.goto(new URL('/',url).href);
 await page.getByLabel('Пароль владельца').waitFor();
 await page.getByRole('link',{name:'Что умеет Atlas'}).waitFor();
 console.log('PASS: публичный HTTPS /about, desktop/mobile, CSP без ошибок, 0 API-запросов; основной интерфейс требует входа.');
}finally{await browser.close();}
