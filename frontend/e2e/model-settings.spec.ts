import {test,expect} from '@playwright/test';
import {openPanel} from './navigation';
test('owner changes models, reloads and detects conflicting settings without exposing keys',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('button',{name:'Настройки',exact:true})).toBeVisible();
 const originalResponse=await page.request.get('/api/model-settings');expect(originalResponse.ok()).toBe(true);
 const original=await originalResponse.json();
 const headers={'X-Atlas-Client':'web'};
 try{
  await openPanel(page,'Модели');
  await page.getByLabel('Использовать выбранные модели',{exact:true}).check();
  await page.getByLabel('Семантический поиск',{exact:true}).uncheck();
  await page.getByLabel('Автоматический выбор быстрой и вдумчивой модели',{exact:true}).check();
  await page.getByLabel('Модель: Основной помощник',{exact:true}).fill('owner-custom-model');
  await page.getByRole('button',{name:'Сохранить модели',exact:true}).click();
  await expect(page.getByRole('status')).toContainText('Настройки сохранены');
  await page.reload();await openPanel(page,'Модели');
  await expect(page.getByLabel('Модель: Основной помощник',{exact:true})).toHaveValue('owner-custom-model');
  await expect(page.getByLabel('Автоматический выбор быстрой и вдумчивой модели',{exact:true})).toBeChecked();
  await page.getByRole('button',{name:/^Вдумчивый ответ/}).click();
  await expect(page.getByLabel('Модель: Сложные задачи и планирование',{exact:true})).toHaveValue('anthropic/claude-sonnet-5.5');
  await page.getByRole('button',{name:/^Быстрый ответ/}).click();
  const second=await page.context().newPage();await second.goto('/');await openPanel(second,'Модели');
  await expect(second.getByLabel('Модель: Основной помощник',{exact:true})).toHaveValue('owner-custom-model');
  await page.getByLabel('Модель: Основной помощник',{exact:true}).fill('google/gemini-3.8-flash');
  await page.getByRole('button',{name:'Сохранить модели',exact:true}).click();await expect(page.getByRole('status')).toContainText('Настройки сохранены');
  await second.getByLabel('Модель: Основной помощник',{exact:true}).fill('stale-choice');await second.getByRole('button',{name:'Сохранить модели',exact:true}).click();
  await expect(second.getByRole('alert')).toContainText('другой сессии');await second.close();
  const current=await (await page.request.get('/api/model-settings')).json();expect(current.config.main.model).toBe('google/gemini-3.8-flash');
  expect(JSON.stringify(current)).not.toContain('model_api_key');
 }finally{
  const current=await (await page.request.get('/api/model-settings')).json();
  const r=await page.request.put('/api/model-settings',{headers,data:{revision:current.revision,config:original.config}});expect(r.ok()).toBe(true);
 }
});

test('voice model shortcut survives owner login and opens model settings',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/#models');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('heading',{name:'Модели Atlas',exact:true})).toBeVisible();
 await expect(page.getByLabel('Модель: Основной помощник',{exact:true})).toBeVisible();
 await expect(page.locator('#models')).toContainText('голосовом разговоре');
 await page.getByRole('button',{name:'Разговор',exact:true}).click();
 await page.evaluate(()=>{window.location.hash='';window.location.hash='models'});
 await expect(page.getByRole('heading',{name:'Модели Atlas',exact:true})).toBeVisible();
 await page.reload();
 await expect(page.getByRole('heading',{name:'Модели Atlas',exact:true})).toBeVisible();
});
