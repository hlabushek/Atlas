import {test,expect} from '@playwright/test';

test('voice center receives events, edits policy and sends scoped controls without exposing hardware bridge',async({page})=>{
 test.skip(!process.env.ATLAS_E2E_PASSWORD,'Isolated API required');
 const policy={enabled:true,activation_mode:'wake_word',default_mode:'chat',follow_up_seconds:0,wake_words:['атлас'],ptt_key:'F8',network_mode:'atlas',store_transcripts:true,speak_sensitive:false,voice_interrupt_enabled:false,session_context_seconds:60,confirm_local_actions:true,local_grants:['local.volume_delta'],local_grant_expires_at:0,scenarios:[]};
 const station={device_id:'fixture-device',name:'Тестовый ПК',connected:true,enabled:true,revision:1,policy,state:{state:'recognizing',rms_percent:9,microphone:'Тестовый микрофон',model:'whisper-medium',route:'local',transcript:'атлас сделай громче'},events:[],controls:[]};
 const controls:string[]=[];
 await page.route('**/api/voice/center',route=>route.fulfill({json:[station]}));
 await page.route('**/api/voice/capabilities',route=>route.fulfill({json:[{id:'local.volume_delta',risk:'low',grants:['local.volume_delta'],confirmation:'never',cancellable:false,reversible:true}]}));
 await page.route('**/api/voice/events',route=>route.fulfill({contentType:'text/event-stream',body:'data: '+JSON.stringify([station])+'\n\n'}));
 await page.route('**/api/voice/devices/fixture-device/settings',async route=>{const body=route.request().postDataJSON();expect(body.revision).toBe(1);expect(body.policy.network_mode).toBe('local_only');expect(body.policy.activation_mode).toBe('wake_word');station.policy=body.policy;station.revision=2;await route.fulfill({json:{revision:2,policy:body.policy}})});
 await page.route('**/api/voice/devices/fixture-device/control',async route=>{controls.push(route.request().postDataJSON().kind);await route.fulfill({status:202,json:{id:'control-fixture',status:'PENDING'}})});
 await page.goto('/#voice');
 await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
 await page.getByRole('button',{name:'Войти в Atlas'}).click();
 await expect(page.getByRole('heading',{name:'Голос Atlas',exact:true})).toBeVisible();
 await expect(page.getByLabel('Уровень микрофона')).toHaveAttribute('value','9');
 await expect(page.getByText('Распознаёт',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Остановить озвучку',exact:true}).click();
 await page.getByRole('button',{name:'Отменить задачу',exact:true}).click();
 expect(controls).toEqual(['stop','cancel']);
 await page.locator('.voice-details>summary').click();
 await page.getByLabel('Приватность голоса').selectOption('local_only');
 await page.getByLabel('Сохранять текст голосовых запросов').uncheck();
 await page.getByRole('button',{name:'Сохранить голосовые настройки'}).click();
 await expect(page.getByRole('status').last()).toContainText('Сохранено');
 expect(await page.evaluate(()=>typeof (window as unknown as {pywebview?:unknown}).pywebview)).toBe('undefined');
});
