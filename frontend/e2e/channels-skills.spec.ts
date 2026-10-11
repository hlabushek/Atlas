import {test,expect} from '@playwright/test';
import {openPanel} from './navigation';

test('initiatives are contextual activity cards, not generated chats',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Set ATLAS_E2E_PASSWORD');
 await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();

 await openPanel(page,'Инициативы');
 await expect(page.getByLabel('Разрешить автоматические инициативы')).not.toBeChecked();
 await expect(page.getByLabel('Разрешить передавать последние реплики текущего разговора для оценки контекста')).not.toBeChecked();
 await expect(page.locator('.content')).toContainText('модели структурированных решений RouterAI D1');

 await openPanel(page,'Модели');
 await page.getByRole('button',{name:/Решения инициатив/}).click();
 await expect(page.getByLabel('Модель: Решения об инициативах')).toHaveValue('liquid/d1');

 const before=await (await page.request.get('/api/conversations')).json();
 const due=new Date(Date.now()+5*60_000).toISOString();
 const response=await page.request.post('/api/initiatives',{headers:{'X-Atlas-Client':'web'},data:{message:'Проверить ленту инициатив',due_at:due}});
 expect(response.ok()).toBe(true);
 const card=await response.json();
 expect(card.conversation_id).toBeNull();
 await openPanel(page,'Активность');
 const item=page.locator('.initiative-card').filter({hasText:'Проверить ленту инициатив'});
 await expect(item).toBeVisible();
 await item.getByRole('button',{name:'Готово'}).click();
 await expect(item).toHaveCount(0);
 const after=await (await page.request.get('/api/conversations')).json();
 expect(after).toHaveLength(before.length);

 await openPanel(page,'Навыки');
 await expect(page.getByRole('button',{name:'Установить демо-дневник'})).toHaveCount(0);
 await expect(page.getByText('Подключённых расширений нет.',{exact:true})).toBeVisible();
 await openPanel(page,'Дневник');
 await expect(page.getByRole('heading',{name:'Дневник и учебные задачи'})).toBeVisible();
 await expect(page.getByLabel('Дата дневника')).toBeVisible();
 await page.getByLabel('Дата дневника').fill('2039-10-05');
 await expect(page.getByRole('heading',{name:/Расписание на 5 октября 2039/})).toBeVisible();
 await expect(page.getByText('Расписание этого дня ещё не загружено.')).toBeVisible();
 await expect(page.getByRole('button',{name:'Синхронизировать дневник'})).toHaveCount(0);
 const diary=await (await page.request.get('/api/diary')).json();
 expect(Array.isArray(diary.assignments)).toBe(true);
 expect(Array.isArray(diary.tasks)).toBe(true);
});
