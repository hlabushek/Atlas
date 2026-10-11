import {describe,it,expect} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {MessageBody,safeLink} from './MessageBody';

describe('Безопасное оформление ответов',()=>{
 it('оформляет заголовок, выделение, список и код',()=>{
  const html=renderToStaticMarkup(<MessageBody content={'## План\n\n**Важно**: `пример`\n\n- один\n- два\n\n```python\nprint(1)\n```'}/>);
  expect(html).toContain('<h4>План</h4>');expect(html).toContain('<strong>Важно</strong>');expect(html).toContain('<ul>');expect(html).toContain('<pre><code>print(1)');
 });
 it('не исполняет HTML, JavaScript и data-ссылки',()=>{
  const html=renderToStaticMarkup(<MessageBody content={'<img src=x onerror=alert(1)>\n\n[Клик](javascript:alert) [Данные](data:text/html,test)'}/>);
  expect(html).not.toContain('<img');expect(html).not.toContain('<a ');expect(html).toContain('&lt;img');
  expect(safeLink('javascript:alert(1)')).toBeNull();expect(safeLink('data:text/plain,hi')).toBeNull();
 });
 it('открывает явную веб-ссылку без доступа к исходному окну',()=>{
  const html=renderToStaticMarkup(<MessageBody content={'[Источник](https://example.com)'}/>);
  expect(html).toContain('rel="noopener noreferrer"');expect(safeLink('https://example.com')).toBe('https://example.com/');
 });
});
