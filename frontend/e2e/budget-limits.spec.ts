import {test,expect} from '@playwright/test';
import {openPanel} from './navigation';

test('owner can save daily and per-task budget limits',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('button',{name:'Настройки',exact:true})).toBeVisible();
 const headers={'X-Atlas-Client':'web'};
 const originalResponse=await page.request.get('/api/budget');expect(originalResponse.ok()).toBe(true);
 const original=(await originalResponse.json()).limits;
 try{
  await openPanel(page,'Лимиты');
  await page.getByLabel('Лимит токенов в день').fill('1234');
  await page.getByLabel('Лимит микроUSD в день').fill('456');
  await page.getByLabel('Лимит микроUSD на задачу').fill('78');
  const saved=page.waitForResponse(r=>r.url().endsWith('/api/budget')&&r.request().method()==='PUT');
  await page.getByRole('button',{name:'Сохранить лимиты'}).click();
  expect((await saved).ok()).toBe(true);
  const limits=(await (await page.request.get('/api/budget')).json()).limits;
  expect(limits.daily_token_limit).toBe(1234);
  expect(limits.daily_cost_limit).toBe(456);
  expect(limits.task_cost_limit).toBe(78);
 }finally{
  const latest=(await (await page.request.get('/api/budget')).json()).limits;
  const restored=await page.request.put('/api/budget',{headers,data:{...latest,...original}});
  expect(restored.ok()).toBe(true);
 }
});
