import {test,expect} from '@playwright/test';
import {openPanel} from './navigation';

test('chat metadata, pin, archive, trash and restore persist without rewriting history',async({page})=>{
 test.setTimeout(60000);
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();await expect(page.getByLabel('Сообщение',{exact:true})).toBeVisible();
 const marker='Управление чатами '+Date.now();
 await page.getByLabel('Сообщение',{exact:true}).fill(marker);await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();
 await expect(page.locator('article.assistant').last()).toBeVisible({timeout:30000});
 const headers={'X-Atlas-Client':'web'};
 const original=(await (await page.request.get('/api/conversations?q='+encodeURIComponent(marker))).json())[0];
 const messages=await (await page.request.get('/api/conversations/'+original.id+'/messages')).json();
 const renamed='Мой закреплённый чат '+Date.now();
 await page.getByRole('button',{name:'Действия: '+marker,exact:true}).click();await page.getByRole('button',{name:'Переименовать и редактировать',exact:true}).click();
 await page.getByLabel('Название разговора').fill(renamed);await page.getByLabel('Описание разговора').fill('Рабочие идеи');await page.getByRole('button',{name:'Сохранить разговор',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('button',{name:'Действия: '+renamed,exact:true}).click();await page.getByRole('button',{name:'Закрепить',exact:true}).click();
 await expect(page.getByLabel('Закреплён',{exact:true}).first()).toBeVisible();
 await page.reload();await page.getByLabel('Поиск разговоров').fill('Рабочие идеи');
 await page.getByRole('button',{name:renamed,exact:false}).first().click();
 await expect(page.locator('.chat-description')).toHaveText('Рабочие идеи');
 await page.getByRole('button',{name:'Действия: '+renamed,exact:true}).click();await page.getByRole('button',{name:'В архив',exact:true}).click();
 await expect(page.locator('.inactive-chat')).toContainText('в архиве');
 await expect(page.getByLabel('Сообщение',{exact:true})).not.toBeVisible();
 expect((await page.request.post('/api/conversations/'+original.id+'/messages',{headers,data:{content:'Не выполнять',request_key:crypto.randomUUID()}})).status()).toBe(409);
 await page.getByRole('button',{name:'Архив',exact:true}).click();await page.getByRole('button',{name:'Действия: '+renamed,exact:true}).click();await page.getByRole('button',{name:'Удалить в корзину',exact:true}).click();
 await expect(page.getByRole('dialog')).toContainText('История сохранится');await page.getByRole('button',{name:'В корзину',exact:true}).click();
 await expect(page.locator('.inactive-chat')).toContainText('в корзине');await page.getByRole('button',{name:'Корзина',exact:true}).click();
 await page.getByRole('button',{name:'Действия: '+renamed,exact:true}).click();await page.getByRole('button',{name:'Восстановить',exact:true}).click();
 await expect(page.getByLabel('Сообщение',{exact:true})).toBeVisible();await page.getByRole('button',{name:'Все чаты',exact:true}).click();
 expect(await (await page.request.get('/api/conversations/'+original.id+'/messages')).json()).toEqual(messages);
 await page.getByLabel('Сообщение',{exact:true}).fill('Неотправленный черновик');await openPanel(page,'Модели');
 await page.getByLabel('Модель: Основной помощник',{exact:true}).fill('unsaved-model');await openPanel(page,'Лимиты');await openPanel(page,'Модели');
 await expect(page.getByLabel('Модель: Основной помощник',{exact:true})).toHaveValue('unsaved-model');
 await page.screenshot({path:'test-results/chat-settings-light.png',fullPage:true});
 await page.getByRole('button',{name:'Тёмная тема',exact:true}).click();await page.screenshot({path:'test-results/chat-settings-dark.png',fullPage:true});
 await page.setViewportSize({width:390,height:844});await page.screenshot({path:'test-results/chat-settings-mobile.png',fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'Разговор',exact:true}).click();await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('Неотправленный черновик');
});

test('trash reconciles a committed request whose response was lost',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();await expect(page.getByLabel('Сообщение',{exact:true})).toBeVisible();
 const title='Lost delete response '+Date.now();
 const created=await page.request.post('/api/conversations',{headers:{'X-Atlas-Client':'web'},data:{title}});expect(created.ok()).toBeTruthy();const conversation=await created.json();
 await page.getByLabel('Поиск разговоров').fill(title);await expect(page.getByRole('button',{name:'Действия: '+title,exact:true})).toBeVisible();
 await page.route('**/api/conversations/'+conversation.id,async route=>{
  if(route.request().method()==='PATCH'){await route.fetch();await route.abort();}
  else await route.continue();
 });
 await page.getByRole('button',{name:'Действия: '+title,exact:true}).click();await page.getByRole('button',{name:'Удалить в корзину',exact:true}).click();
 await page.getByRole('button',{name:'В корзину',exact:true}).click();
 await expect(page.getByRole('dialog')).not.toBeVisible();await expect(page.getByRole('button',{name:'Действия: '+title,exact:true})).toHaveCount(0);
 await expect(page.getByRole('alert')).toHaveCount(0);
 await page.unroute('**/api/conversations/'+conversation.id);
 expect((await (await page.request.get('/api/conversations?scope=trash&q='+encodeURIComponent(title))).json()).some((row:{id:string})=>row.id===conversation.id)).toBe(true);
});

test('demo cleanup shows exact scope and requires typed confirmation; stale preview cannot reset',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();
 // UI-only conflict fixture: never cleans the shared E2E dataset. Real deletion is covered in isolated backend tests.
 await page.route('**/api/maintenance/demo',r=>r.fulfill({json:{digest:'a'.repeat(64),counts:{tasks:2},owner_memories:5,unknown_tasks:10,protected_demo_tasks:1}}));
 await page.route('**/api/maintenance/demo/reset',r=>r.fulfill({status:409,json:{detail:'Данные изменились. Пересмотрите план очистки.'}}));
 await openPanel(page,'Данные');await expect(page.locator('.danger-zone')).toContainText('памяти владельца: 5');
 await expect(page.getByRole('button',{name:'Удалить подтверждённое демо',exact:true})).toBeDisabled();
 await page.getByLabel('Подтверждение очистки демо').fill('СБРОСИТЬ ДЕМО');await page.getByRole('button',{name:'Удалить подтверждённое демо',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Пересмотрите план');
});
