import {useEffect, useRef, useState} from 'react';
import {api} from './api';
import {pcm16, VoiceVad} from './voiceVad';

type Props = {onTranscript: (text: string) => void; active: boolean};

export function BrowserVoice({onTranscript, active}: Props) {
  const [ready, setReady] = useState(false), [recording, setRecording] = useState(false);
  const [status, setStatus] = useState(''), [transcript, setTranscript] = useState('');
  const [level, setLevel] = useState(0), [threshold, setThreshold] = useState(0.012);
  const cleanup = useRef<() => void>(()=>{});
  const starting = useRef(false);
  const generation = useRef(0);
  const vad = useRef(new VoiceVad());
  vad.current.threshold = threshold;
  useEffect(()=>{let live=true;api<{ready: boolean}>('/voice/audio-status').then(value=>{if(live)setReady(value.ready)}).catch(()=>{if(live)setReady(false)});return()=>{live=false}},[]);
  useEffect(()=>{if(!active){generation.current++;cleanup.current()}return()=>{generation.current++;cleanup.current()}},[active]);

  async function start() {
    if (starting.current || recording) return;
    starting.current = true;
    const token = ++generation.current;
    let stream: MediaStream | undefined, context: AudioContext | undefined, socket: WebSocket | undefined;
    let capture: AudioWorkletNode | undefined, source: MediaStreamAudioSourceNode | undefined;
    let stopped = false, waiting = false, phrase = false, frames = 0;
    let limitTimer: ReturnType<typeof setTimeout> | undefined;
    const dispose = () => {
      stopped = true;
      if (limitTimer) clearTimeout(limitTimer);
      if (capture) {capture.port.onmessage=null;capture.disconnect();}
      source?.disconnect();stream?.getTracks().forEach(track=>track.stop());
      void context?.close().catch(()=>{});
      socket?.close();vad.current.reset();setRecording(false);setLevel(0);
    };
    cleanup.current = dispose;
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error('Микрофон доступен через HTTPS или localhost.');
      stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
      if(stopped || token!==generation.current){dispose();return}
      context = new AudioContext();
      if(context.sampleRate<16000)throw new Error('Требуется частота микрофона не ниже 16 кГц.');
      await context.audioWorklet.addModule('/voice-capture.worklet.js');
      await context.resume();
      if(stopped || token!==generation.current){dispose();return}
      socket = new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/api/voice/audio');
      socket.onmessage = event => {
        if(stopped)return;
        let value: {type?: string; text?: string; message?: string};
        try{value=JSON.parse(event.data)}catch{setStatus('Некорректное событие аудиоканала.');dispose();return}
        if(value.type==='ready'){
          source=context!.createMediaStreamSource(stream!);
          capture=new AudioWorkletNode(context!,'atlas-voice-capture');
          capture.port.onmessage=(audio: MessageEvent<Float32Array>)=>{
            if(stopped || waiting || socket?.readyState!==WebSocket.OPEN)return;
            const result=vad.current.feed(audio.data);
            setLevel(Math.min(100,Math.round(result.rms*1000)));
            if(result.start){socket.send(JSON.stringify({type:'begin'}));phrase=true;frames=0;setStatus('Передаю речь…')}
            for(const frame of result.frames){
              if(socket.bufferedAmount>65536){setStatus('Сеть не успевает принимать аудио. Запись остановлена.');dispose();return}
              socket.send(pcm16(frame));frames++;
            }
            if(phrase && (result.end || frames>=1450)){
              socket.send(JSON.stringify({type:'end'}));phrase=false;waiting=true;vad.current.reset();setStatus('Распознаю речь…');
            }
          };
          source.connect(capture);capture.connect(context!.destination);
          setRecording(true);setStatus('Слушаю. Тишина остается в браузере.');
          limitTimer=setTimeout(()=>{setStatus('Две минуты записи истекли. Можно начать снова.');dispose()},120000);
        } else if(value.type==='transcript'){
          waiting=false;vad.current.reset();
          if(value.text)setTranscript(previous=>(previous+' '+value.text).trim().slice(-16000));
          setStatus(value.text?'Фраза распознана. Можно продолжать.':'Речь не распознана. Попробуйте еще раз.');
        } else if(value.type==='error') {setStatus(value.message||'Аудиоканал недоступен.');dispose();}
      };
      socket.onerror=()=>{if(!stopped){setStatus('Не удалось подключить аудиоканал.');dispose()}};
      socket.onclose=()=>{if(!stopped){setStatus('Аудиоканал закрыт.');dispose()}};
    } catch(error) {setStatus(error instanceof Error?error.message:'Не удалось открыть микрофон.');dispose();}
    finally {starting.current=false;}
  }

  return <article className="browser-voice"><h3>Микрофон браузера</h3>
    <p>Речь передается в локальный Whisper сервера Atlas. Текст можно проверить перед отправкой в чат. Аудио не сохраняется.</p>
    {!ready&&<p>Локальный Whisper для браузера не настроен. Укажите ATLAS_BROWSER_VOICE_MODEL_PATH на сервере.</p>}
    <label>Порог тишины<input aria-label="Порог тишины" type="range" min={0.005} max={0.06} step={0.001} value={threshold} onChange={e=>setThreshold(Number(e.target.value))}/>{threshold.toFixed(3)}</label>
    <progress aria-label="Уровень микрофона браузера" max={100} value={level}/>
    <div className="actions"><button disabled={!ready||!active||recording} onClick={()=>void start()}>Начать запись в браузере</button>
      <button className="secondary" disabled={!recording} onClick={()=>{generation.current++;cleanup.current();setStatus('Запись остановлена. Незавершенная фраза отброшена.')}}>Остановить запись в браузере</button></div>
    <p role="status">{status}</p><label>Распознанный текст<textarea aria-label="Распознанный текст браузера" value={transcript} maxLength={16000} onChange={e=>setTranscript(e.target.value)}/></label>
    <button disabled={!transcript.trim()} onClick={()=>onTranscript(transcript.trim())}>Вставить текст в чат</button>
  </article>;
}
