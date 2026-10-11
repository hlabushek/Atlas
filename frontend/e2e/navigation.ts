import {Page, expect} from '@playwright/test';

export async function openPanel(page:Page, name:string){
 const button=page.getByRole('button',{name,exact:true});
 const picker=page.getByLabel('Раздел настроек',{exact:true});
 const options:Record<string,string>={Общие:'settings',Модели:'models',Лимиты:'budget',Данные:'data',Интеграции:'integrations',Telegram:'telegram',Устройства:'devices',Инструменты:'tools',Навыки:'skills',Дневник:'diary',Инициативы:'initiative-settings',Диагностика:'diagnostics'};
 if(options[name]&&await picker.isVisible()){await picker.selectOption(options[name]);return}
 if(!await button.isVisible()){
  const menu=page.getByRole('button',{name:'Меню',exact:true});
  if(await menu.isVisible())await menu.click();
  if(!await button.isVisible())await page.getByRole('button',{name:'Настройки',exact:true}).click();
 }
 if(options[name]&&await picker.isVisible()){await picker.selectOption(options[name]);return}
 await expect(button).toBeVisible();
 await button.click();
}
