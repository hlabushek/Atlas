import {test, expect} from '@playwright/test';

test('composer multi-file upload, preview, removal and durable message cards',async({page},testInfo)=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();
 const marker='Вложение '+Date.now();
 await expect(page.getByRole('button',{name:'Прикрепить файлы',exact:true})).toBeEnabled();
 await page.getByLabel('Выбрать файлы',{exact:true}).setInputFiles([
   {name:'first.txt',mimeType:'text/plain',buffer:Buffer.from(marker)},
   {name:'second.md',mimeType:'text/markdown',buffer:Buffer.from('Второй документ')},
 ]);
 await expect(page.locator('.draft-files')).toContainText('Готов');
 await expect(page.locator('.draft-files .ready')).toHaveCount(2);
 await page.locator('.draft-files .file-open').first().click();await expect(page.getByRole('dialog')).toContainText(marker);
 await page.keyboard.press('Escape');await expect(page.getByRole('dialog')).not.toBeVisible();
 await page.getByRole('button',{name:'Убрать second.md',exact:true}).click();await expect(page.locator('.draft-files .file-card')).toHaveCount(1);
 await page.getByLabel('Выбрать файлы',{exact:true}).setInputFiles({name:'bad.exe',mimeType:'application/octet-stream',buffer:Buffer.from('no executable')});
 await expect(page.getByRole('button',{name:'Отправить сообщение',exact:true})).toBeDisabled();
 await expect(page.locator('.file-error')).toContainText('не поддерживается');await page.getByRole('button',{name:'Убрать bad.exe',exact:true}).click();
 await page.getByLabel('Сообщение',{exact:true}).fill('Прочитай файл');
 await page.getByRole('button',{name:'Отправить сообщение',exact:true}).click();
 await expect(page.locator('.messages article.user').last()).toContainText('first.txt');
 await expect(page.locator('.messages article.user').last()).not.toContainText(marker);
 await expect(page.locator('.messages article.assistant').last()).toContainText(marker,{timeout:30000});
 await expect(page.locator('.draft-files')).toHaveCount(0);
 await page.reload();await page.getByRole('button',{name:/Прочитай файл/}).first().click();
 await expect(page.getByRole('button',{name:'Открыть файл first.txt',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Открыть файл first.txt',exact:true}).click();await expect(page.getByRole('dialog')).toContainText(marker);await page.keyboard.press('Escape');
 await page.setViewportSize({width:320,height:568});
 await page.getByLabel('Выбрать файлы',{exact:true}).setInputFiles({name:'mobile.txt',mimeType:'text/plain',buffer:Buffer.from('Мобильный файл')});
 await expect(page.locator('.draft-files .ready')).toHaveCount(1);
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 const box=await page.getByLabel('Сообщение',{exact:true}).boundingBox();expect(box!.y+box!.height).toBeLessThanOrEqual(568);
 await page.screenshot({path:testInfo.outputPath('attachments-mobile.png'),animations:'disabled'});
});
