import {test,expect} from '@playwright/test';

async function login(page:any){
  test.skip(!process.env.ATLAS_E2E_PASSWORD,'Set ATLAS_E2E_PASSWORD');
  await page.goto('/');
  await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
  await page.getByRole('button',{name:'Войти в Atlas'}).click();
  await page.getByRole('button',{name:'Активность',exact:true}).click();
}

test('agent creates a real note only after scoped approval',async({page})=>{
  await login(page);
  const goal='E2E агент Atlas '+Date.now();
  await page.getByLabel('Цель задачи').fill(goal);
  await page.getByRole('button',{name:'Запустить задачу',exact:true}).click();
  const task=page.locator('article.task').filter({hasText:goal});
  await expect(task).toContainText('WAITING_APPROVAL',{timeout:30000});
  await task.getByRole('button',{name:'Пауза',exact:true}).click();
  await expect(task).toContainText('PAUSED');
  await task.getByRole('button',{name:'Продолжить',exact:true}).click();
  await expect(task).toContainText('WAITING_APPROVAL',{timeout:30000});
  expect((await (await page.request.get('/api/notes')).json()).filter((n:any)=>n.title===goal)).toHaveLength(0);
  await page.getByRole('button',{name:'Подтверждения',exact:true}).click();
  const approval=page.locator('.content article').filter({hasText:goal});
  await expect(approval).toContainText('notes',{timeout:10000});
  await approval.getByRole('button',{name:'Разрешить действие'}).click();
  await page.getByRole('button',{name:'Активность',exact:true}).click();
  await expect(task).toContainText('COMPLETED',{timeout:30000});
  await expect(page.locator('.agent-note').filter({hasText:goal})).toHaveCount(1);
  await task.getByRole('button',{name:'Шаги и результаты'}).click();
  await expect(task.locator('.trace')).toContainText('SUCCEEDED');
});

test('dry run produces previews without notes or approvals',async({page})=>{
  await login(page);
  const goal='E2E dry run '+Date.now();
  await page.getByLabel('Цель задачи').fill(goal);
  await page.getByLabel('Dry Run — только предварительный просмотр').check();
  await page.getByRole('button',{name:'Посмотреть план действий'}).click();
  const task=page.locator('article.task').filter({hasText:goal});
  await expect(task).toContainText('COMPLETED',{timeout:30000});
  await task.getByRole('button',{name:'Шаги и результаты'}).click();
  await expect(task.locator('.trace')).toContainText('DRY_RUN');
  expect((await (await page.request.get('/api/notes')).json()).filter((n:any)=>n.title===goal)).toHaveLength(0);
  expect((await (await page.request.get('/api/approvals')).json()).filter((a:any)=>a.call.arguments.title===goal)).toHaveLength(0);
});
