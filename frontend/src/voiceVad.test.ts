import {describe, expect, it} from 'vitest';
import {VoiceVad, pcm16} from './voiceVad';

const frame=(value=0)=>new Float32Array(320).fill(value);
describe('Фильтрация тишины перед отправкой PCM',()=>{
  it('не передает тишину и короткий всплеск',()=>{
    const vad=new VoiceVad();
    for(let i=0;i<100;i++)expect(vad.feed(frame()).frames).toHaveLength(0);
    for(let i=0;i<3;i++)expect(vad.feed(frame(0.05)).frames).toHaveLength(0);
    expect(vad.feed(frame()).frames).toHaveLength(0);
  });
  it('сохраняет начало и хвост речи, затем снова блокирует тишину',()=>{
    const vad=new VoiceVad();
    for(let i=0;i<10;i++)vad.feed(frame());
    for(let i=0;i<3;i++)vad.feed(frame(0.05));
    const start=vad.feed(frame(0.05));
    expect(start.start).toBe(true);expect(start.frames).toHaveLength(10);
    for(let i=0;i<19;i++){const result=vad.feed(frame());expect(result.frames).toHaveLength(1);expect(result.end).toBe(false)}
    expect(vad.feed(frame()).end).toBe(true);
    expect(vad.feed(frame()).frames).toHaveLength(0);
  });
  it('прерывает накопление при reset и учитывает выбранный порог',()=>{
    const vad=new VoiceVad(0.1);
    for(let i=0;i<10;i++)expect(vad.feed(frame(0.05)).start).toBe(false);
    vad.threshold=0.01;
    for(let i=0;i<4;i++)vad.feed(frame(0.05));
    vad.reset();expect(vad.feed(frame()).frames).toHaveLength(0);
  });
  it('кодирует PCM little endian с ограничением диапазона',()=>{
    const pcm=new DataView(pcm16(new Float32Array([-2,-1,0,1,2])));
    expect([0,2,4,6,8].map(offset=>pcm.getInt16(offset,true))).toEqual([-32768,-32768,0,32767,32767]);
  });
});
