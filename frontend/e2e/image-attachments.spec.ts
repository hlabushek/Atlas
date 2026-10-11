import {test,expect} from '@playwright/test';

test('drop and clipboard image create real uploaded previews without submitting to a model',async({page},testInfo)=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Owner password required');
 await page.goto('/');await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('button',{name:'Прикрепить файлы',exact:true})).toBeEnabled();
 const png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSj8AAAAASUVORK5CYII=';
 const transfer=await page.evaluateHandle(encoded=>{const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));const dt=new DataTransfer();dt.items.add(new File([bytes],'drop.png',{type:'image/png'}));return dt},png);
 await page.locator('.messages').dispatchEvent('drop',{dataTransfer:transfer});
 await expect(page.locator('.draft-files .ready')).toHaveCount(1);await expect(page.locator('.draft-files img')).toBeVisible();
 await page.locator('.draft-files .file-open').click();await expect(page.locator('.file-image-preview')).toBeVisible();
 await expect.poll(()=>page.locator('.file-image-preview').evaluate(img=>(img as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
 await page.keyboard.press('Escape');await page.getByRole('button',{name:'Убрать drop.png',exact:true}).click();
 await page.getByLabel('Сообщение',{exact:true}).focus();
 // Browser clipboard event with a real image File; actual OS clipboard permissions are separate.
 await page.getByLabel('Сообщение',{exact:true}).evaluate((element,encoded)=>{
   const bytes=Uint8Array.from(atob(encoded),c=>c.charCodeAt(0));const dt=new DataTransfer();dt.items.add(new File([bytes],'clipboard.png',{type:'image/png'}));
   element.dispatchEvent(new ClipboardEvent('paste',{clipboardData:dt,bubbles:true,cancelable:true}));
 },png);
 await expect(page.locator('.draft-files .ready')).toHaveCount(1);await expect(page.locator('.draft-files')).toContainText('clipboard.png');
 await expect(page.getByRole('button',{name:'Отправить сообщение',exact:true})).toBeDisabled();
 await expect(page.locator('.file-privacy').first()).toContainText('не поддерживает изображения');
 await page.setViewportSize({width:375,height:812});await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 const composer=await page.locator('.composer').boundingBox(), input=await page.getByLabel('Сообщение',{exact:true}).boundingBox();
 expect(composer!.height).toBeLessThan(812*0.35);expect(input!.width).toBeGreaterThan(300);
 await page.screenshot({path:testInfo.outputPath('image-clipboard-mobile.png'),animations:'disabled'});
 await page.getByRole('button',{name:'О вложениях',exact:true}).click();await expect(page.getByRole('dialog')).toContainText('Ctrl+V');await expect(page.getByRole('dialog')).toContainText('не отправляет его модели');await page.keyboard.press('Escape');
 await page.getByRole('button',{name:'Убрать clipboard.png',exact:true}).click();
});
