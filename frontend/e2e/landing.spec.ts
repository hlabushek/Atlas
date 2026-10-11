import {test, expect} from '@playwright/test';
import {resolve} from 'node:path';

test('Публичная страница: примеры без API и личных данных', async ({page}) => {
 const apiRequests:string[]=[];
 page.on('request', request => {if(new URL(request.url()).pathname.startsWith('/api/')) apiRequests.push(request.url());});
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('/about.html');
 await expect(page.getByRole('heading', {level:1})).toContainText('Меньше рутины.');
 await page.getByRole('tab', {name:'Задачи',exact:true}).click();
 await expect(page.getByRole('tabpanel')).toContainText('ваше подтверждение');
 await page.getByRole('tab', {name:'Память',exact:true}).click();
 await expect(page.getByRole('tabpanel')).toContainText('отредактировать или удалить');
 await page.getByRole('tab', {name:'Голос',exact:true}).click();
 await page.keyboard.press('ArrowRight');
 await expect(page.getByRole('tab', {name:'Задачи',exact:true})).toBeFocused();
 await page.keyboard.press('Home');
 await expect(page.getByRole('tab', {name:'Голос',exact:true})).toHaveAttribute('aria-selected','true');
 await page.getByText('Atlas всегда понимает речь без ошибок?').click();
 await expect(page.getByText('Нет. Точность зависит', {exact:false})).toBeVisible();
 await expect(page.getByRole('link', {name:'Перейти к настройке голоса'})).toHaveAttribute('href','/#voice');
 await page.screenshot({path:resolve('../.sessions/atlas-about-desktop.png'),fullPage:true});
 expect(apiRequests).toEqual([]);
});

test('Мобильная публичная страница и клавиатурная навигация', async ({page}) => {
 await page.setViewportSize({width:390,height:844});
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.goto('/about');
 await page.keyboard.press('Tab');
 await expect(page.getByRole('link', {name:'Перейти к содержимому'})).toBeFocused();
 await page.keyboard.press('Enter');
 await expect(page.getByRole('heading', {level:1})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:resolve('../.sessions/atlas-about-mobile.png'),fullPage:true});
});
