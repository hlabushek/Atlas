import {test,expect,Page} from '@playwright/test';
import {openPanel} from './navigation';

async function login(page:Page,path='/') {
  await page.goto(path);
  await page.getByLabel('Пароль владельца').fill(process.env.ATLAS_E2E_PASSWORD!);
  await page.getByRole('button',{name:'Войти в Atlas'}).click();
  await expect(page.getByLabel('Сообщение',{exact:true})).toBeAttached();
}

test('Дневник ищет импортированные задания через реальный FTS5 API',async({page})=>{
  test.skip(!process.env.ATLAS_E2E_PASSWORD,'Нужен изолированный API');
  await login(page);
  const unique='Инерция'+Date.now();
  const response=await page.request.post('/api/diary/import/school-mos',{headers:{'X-Atlas-Client':'web'},data:{homework:[{
    external_id:unique,subject:'Физика',title:unique+' проверить закон',due_at:new Date(Date.now()+86400000).toISOString(),
  }]}});
  expect(response.ok()).toBe(true);
  await openPanel(page,'Дневник');
  await page.getByLabel('Поиск заданий').fill(unique);
  await page.getByRole('button',{name:'Найти задания',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Найденные задания',exact:true})).toBeVisible();
  await expect(page.locator('.assignment')).toHaveCount(1);
  await expect(page.locator('.assignment')).toContainText(unique);
});

test('Чат получает прогресс настоящей локальной agent-задачи через WebSocket',async({page})=>{
  test.skip(!process.env.ATLAS_E2E_PASSWORD,'Нужен изолированный API и worker');
  await login(page);
  const created=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/conversations');
  await page.getByRole('button',{name:'＋ Новый разговор',exact:true}).click();
  const cid=(await (await created).json()).id;
  const response=await page.request.post('/api/tasks',{headers:{'X-Atlas-Client':'web'},data:{
    conversation_id:cid,request_key:crypto.randomUUID(),goal:'Проверить память',
    plan:[{tool:'memory.search',arguments:{query:'Atlas'}}],
  }});
  expect(response.ok()).toBe(true);
  await expect(page.getByLabel('Прогресс инструментов')).toContainText('memory.search');
  await expect(page.getByLabel('Прогресс инструментов')).toContainText('Результат проверен');
});

test('Настоящий AudioWorklet фильтрует синтетическую тишину до отправки PCM',async({page},testInfo)=>{
  test.skip(!process.env.ATLAS_E2E_PASSWORD,'Нужен изолированный интерфейс');
  await page.route('**/api/voice/audio-status',route=>route.fulfill({json:{ready:true}}));
  await page.addInitScript(()=>{
    const state=window as unknown as {testVoiceGain:GainNode;testVoiceContext:AudioContext;testVoiceTrack:MediaStreamTrack};
    Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>{
      const context=new AudioContext();
      const oscillator=context.createOscillator(),gain=context.createGain(),output=context.createMediaStreamDestination();
      oscillator.frequency.value=300;gain.gain.value=0;
      oscillator.connect(gain);gain.connect(output);oscillator.start();await context.resume();
      state.testVoiceGain=gain;state.testVoiceContext=context;state.testVoiceTrack=output.stream.getAudioTracks()[0];
      return output.stream;
    }});
  });
  let packets=0,ends=0;
  await page.routeWebSocket('**/api/voice/audio',socket=>{
    socket.send(JSON.stringify({type:'ready'}));
    socket.onMessage(message=>{
      if(typeof message!=='string'){packets++;return}
      if(JSON.parse(message).type==='end'){
        ends++;socket.send(JSON.stringify({type:'transcript',text:'Проверочная синтетическая фраза'}));
      }
    });
  });
  await login(page);
  await openPanel(page,'Голос');
  await page.getByRole('button',{name:'Начать запись в браузере'}).click();
  await expect(page.getByText('Слушаю. Тишина остается в браузере.')).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('voice-center-vad.png'),fullPage:true});
  await page.waitForTimeout(400);
  expect(packets).toBe(0);
  await page.evaluate(()=>{(window as unknown as {testVoiceGain:GainNode}).testVoiceGain.gain.value=0.1});
  await expect.poll(()=>packets).toBeGreaterThan(12);
  await page.evaluate(()=>{(window as unknown as {testVoiceGain:GainNode}).testVoiceGain.gain.value=0});
  await expect(page.getByLabel('Распознанный текст браузера')).toHaveValue('Проверочная синтетическая фраза');
  expect(ends).toBe(1);
  const afterSpeech=packets;
  await page.waitForTimeout(400);
  expect(packets).toBe(afterSpeech);
  await page.getByRole('button',{name:'Вставить текст в чат'}).click();
  await expect(page.getByLabel('Сообщение',{exact:true})).toHaveValue('Проверочная синтетическая фраза');
  await expect.poll(()=>page.evaluate(()=>(window as unknown as {testVoiceTrack:MediaStreamTrack}).testVoiceTrack.readyState)).toBe('ended');
  await page.evaluate(()=>(window as unknown as {testVoiceContext:AudioContext}).testVoiceContext.close());
});
