import {test,expect} from '@playwright/test';
import {openPanel} from './navigation';

test('phone uses full-width modes, accessible drawer, readable forms and preserves drafts',async({page},testInfo)=>{
 test.setTimeout(60000);test.skip(!process.env.ATLAS_E2E_PASSWORD,'Isolated owner required');
 await page.setViewportSize({width:390,height:844});await page.goto('/');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('button',{name:'Меню',exact:true})).toBeVisible();
 await expect(page.locator('.app-sidebar')).not.toBeVisible();
 const draft='Мобильный черновик';await page.getByLabel('Сообщение',{exact:true}).fill(draft);
 for(const size of [{width:320,height:568},{width:375,height:667},{width:390,height:844},{width:430,height:932}]){
  await page.setViewportSize(size);
  for(const mode of ['Разговор','Пространства','Голос']){
   await page.getByRole('button',{name:mode,exact:true}).click();
   await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
   await expect(page.locator('.app-sidebar')).not.toBeVisible();
  }
  await page.getByRole('button',{name:'Разговор',exact:true}).click();
  await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue(draft);
  const box=await page.getByLabel('Сообщение',{exact:true}).boundingBox();expect(box!.width).toBeGreaterThan(size.width/2);expect(box!.y+box!.height).toBeLessThanOrEqual(size.height);
  await page.screenshot({path:testInfo.outputPath('chat-'+size.width+'.png'),animations:'disabled'});
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Меню',exact:true}).click();
 await expect(page.locator('.app-main')).toHaveAttribute('inert','');await expect(page.getByRole('button',{name:'Закрыть меню',exact:true})).toBeFocused();
 await page.getByRole('button',{name:'Выйти',exact:true}).focus();await page.keyboard.press('Tab');await expect(page.getByRole('button',{name:'Закрыть меню',exact:true})).toBeFocused();
 await page.screenshot({path:testInfo.outputPath('drawer.png'),animations:'disabled'});
 await page.keyboard.press('Escape');await expect(page.getByRole('button',{name:'Меню',exact:true})).toBeFocused();
 await openPanel(page,'Модели');
 await expect(page.getByLabel('Раздел настроек',{exact:true})).toHaveValue('models');await expect(page.locator('.settings-categories')).not.toBeVisible();
 expect(await page.getByLabel('Модель: Основной помощник',{exact:true}).evaluate(el=>Number.parseFloat(getComputedStyle(el).fontSize))).toBeGreaterThanOrEqual(16);
 await page.screenshot({path:testInfo.outputPath('models.png'),fullPage:true,animations:'disabled'});
 for(const category of ['Лимиты','Данные','Интеграции','Устройства']){
  await openPanel(page,category);await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
 await page.getByRole('button',{name:'Разговор',exact:true}).click();await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue(draft);
 await page.getByText('Диктовка и озвучивание',{exact:true}).click();await expect(page.getByRole('button',{name:'Продиктовать',exact:true})).toBeVisible();
 await page.getByText('Диктовка и озвучивание',{exact:true}).click();await expect(page.getByRole('button',{name:'Продиктовать',exact:true})).not.toBeVisible();
});
