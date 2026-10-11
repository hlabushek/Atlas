import {test,expect} from '@playwright/test';

test('spaces persist real context, conversations, tasks and scoped approvals; modes preserve drafts',async({page})=>{
 test.setTimeout(90000);
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Isolated owner API required');
 await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByLabel('Сообщение',{exact:true})).toBeVisible();
 const name='Учёба '+Date.now(),fact='Моя тема проекта — звездные карты '+Date.now();
 const response=await page.request.post('/api/memory',{headers:{'X-Atlas-Client':'web'},data:{content:fact}});
 expect(response.ok()).toBe(true);
 const memory=await response.json();
 await page.reload();
 await page.getByLabel('Сообщение',{exact:true}).fill('Сохранённый черновик');
 await page.getByRole('button',{name:'Пространства',exact:true}).click();
 await page.getByRole('button',{name:'Создать пространство',exact:true}).click();
 await page.getByLabel('Название пространства').fill(name);
 await page.getByLabel('Описание пространства').fill('Подготовка к экзамену');
 await page.locator('form').getByRole('button',{name:'Создать пространство',exact:true}).click();
 await expect(page.locator('.space-title')).toContainText(name);
 await page.getByLabel('Добавить факт',{exact:true}).selectOption(memory.id);
 await page.getByRole('button',{name:'Добавить в контекст',exact:true}).click();
 await expect(page.locator('.context-card')).toContainText(fact);
 await page.getByRole('button',{name:'Новый разговор в пространстве',exact:true}).click();
 await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('Сохранённый черновик');
 await page.getByLabel('Сообщение',{exact:true}).fill('Привет');
 await page.getByLabel('Сообщение',{exact:true}).press('Enter');
 await expect(page.locator('article.assistant').last()).toContainText(fact,{timeout:30000});
 await page.getByRole('button',{name:'Пространства',exact:true}).click();
 await page.getByText('Поручить задачу с инструментами',{exact:true}).click();
 await page.getByLabel('Цель задачи').fill('Заметка '+name);
 await page.getByRole('button',{name:'Запустить задачу',exact:true}).click();
 await expect(page.locator('.space-task').filter({hasText:'Заметка '+name})).toContainText('Нужно подтверждение',{timeout:30000});
 await expect(page.locator('.approval-panel')).toContainText('notes');
 await page.locator('.approval-panel').getByRole('button',{name:'Разрешить действие',exact:true}).click();
 await expect(page.locator('.space-task').filter({hasText:'Заметка '+name})).toContainText('Готово',{timeout:30000});
 await expect(page.locator('.space-body .agent-note')).toContainText('Заметка '+name);
 await page.reload();
 await page.getByRole('button',{name:'Пространства',exact:true}).click();
 await page.getByRole('button',{name:'Открыть пространство '+name,exact:true}).click();
 await expect(page.locator('.context-card')).toContainText(fact);
 await expect(page.locator('.space-body .agent-note')).toContainText('Заметка '+name);
 await page.getByRole('button',{name:'В архив',exact:true}).click();
 await page.getByLabel('Показать архив',{exact:true}).check();
 await page.getByRole('button',{name:'Восстановить',exact:true}).click();
 await expect(page.locator('.space-title')).toContainText('ПРОСТРАНСТВО');
 await page.screenshot({path:'test-results/space-with-data.png',fullPage:true,animations:'disabled'});
});

test('responsive application keeps three modes, keyboard focus and real empty states',async({page},testInfo)=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Isolated owner API required');
 await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 for(const size of [{width:1440,height:1000},{width:390,height:844}]){
  await page.setViewportSize(size);
  for(const mode of ['Разговор','Пространства','Голос']){
   await page.getByRole('button',{name:mode,exact:true}).click();
   await expect(page.getByRole('button',{name:mode,exact:true})).toHaveAttribute('aria-pressed','true');
   await expect(page.locator('.mode-tabs [aria-pressed=true]')).toHaveCount(1);
   await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   await page.screenshot({path:testInfo.outputPath(`${mode}-${size.width}.png`),fullPage:true,animations:'disabled'});
  }
 }
 await page.getByRole('button',{name:'Разговор',exact:true}).focus();
 await page.keyboard.press('Enter');
 await expect(page.getByLabel('Сообщение',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Меню',exact:true}).click();
 await expect(page.getByRole('button',{name:'Выйти',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Закрыть меню',exact:true}).click();
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('button',{name:'Тёмная тема',exact:true}).click();
 await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
 await page.screenshot({path:testInfo.outputPath('Разговор-dark.png'),fullPage:true,animations:'disabled'});
 await page.reload();
 await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
});
