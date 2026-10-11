import {test,expect} from '@playwright/test';

test('Авто и ручной режим фиксируют выбранную роль в задаче',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Нужен изолированный owner стенд');
 await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByLabel('Режим ответа')).toHaveValue('auto');
 const original=await (await page.request.get('/api/model-settings')).json();
 const headers={'X-Atlas-Client':'web'};
 try{
  expect((await page.request.put('/api/model-settings',{headers,data:{revision:original.revision,config:{...original.config,auto_chat_routing:true}}})).ok()).toBe(true);
  for(const [content,choice,role] of [['Что такое нейросеть?','auto','main'],['Подумай над проектом','auto','planner'],['Проанализируй проект кратко','main','main']]){
   await page.getByLabel('Режим ответа').selectOption(choice);
   await page.getByLabel('Сообщение',{exact:true}).fill(content);
   const response=page.waitForResponse(r=>r.request().method()==='POST'&&/\/messages$/.test(new URL(r.url()).pathname));
   await page.getByRole('button',{name:/^Отправить/}).click();
   const task=await (await response).json();
   expect(task.model_profile.role).toBe(role);
   expect(task.model_profile.requested_role).toBe(choice);
   await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('');
  }
 }finally{
  const current=await (await page.request.get('/api/model-settings')).json();
  expect((await page.request.put('/api/model-settings',{headers,data:{revision:current.revision,config:original.config}})).ok()).toBe(true);
 }
});
