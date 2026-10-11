// Реальный HTTPS, без пароля владельца, микрофона, платных запросов или личных данных.
import {createRequire} from 'node:module';
import {strict as assert} from 'node:assert';
const require=createRequire(new URL('../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const browser=await chromium.launch();
try {
 const page=await browser.newPage();
 const errors=[],csp=[];
 page.on('pageerror',error=>errors.push(error.message));
 page.on('console',message=>{if(/content security policy|violates.*directive/i.test(message.text()))csp.push(message.text())});
 await page.goto('https://atlas.prism-news.xyz/',{waitUntil:'networkidle'});
 await page.getByRole('button',{name:/^Войти в Atlas/}).waitFor();
 assert.equal(await page.getByLabel('Пароль владельца').count(),1);
 assert.equal((await page.request.get('https://atlas.prism-news.xyz/api/spaces')).status(),401);
 assert.equal((await page.request.get('https://atlas.prism-news.xyz/api/maintenance/demo')).status(),401);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 await page.goto('https://atlas.prism-news.xyz/about',{waitUntil:'networkidle'});
 await page.getByRole('heading',{level:1}).waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);assert.deepEqual(csp,[]);
 await page.setViewportSize({width:390,height:844});
 await page.goto('https://atlas.prism-news.xyz/',{waitUntil:'networkidle'});
 await page.getByRole('button',{name:/^Войти в Atlas/}).waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 console.log('PASS: production HTTPS login/about, anonymous spaces blocked, no JS/CSP errors or horizontal overflow');
} finally {await browser.close()}
