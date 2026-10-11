import {expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ToolProgress} from './ToolProgress';
import {ToolProgressEvent} from './types';

it('обновляет состояние вызова и экранирует имя инструмента',()=>{
  const event:ToolProgressEvent={id:1,task_id:'t',call_id:'c',type:'tool_call',tool:'<script>',status:'PREPARED',step:1,total:2};
  const html=renderToStaticMarkup(<ToolProgress events={[event,{...event,id:2,type:'tool_progress',status:'STARTED'}]}/>);
  expect(html).toContain('Выполняется');expect(html).not.toContain('Подготовлен');
  expect(html).toContain('&lt;script&gt;');expect(html).not.toContain('<script>');
  expect(html).toContain('aria-live="polite"');
});
