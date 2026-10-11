import {ReactNode} from 'react';

export function safeLink(value:string):string|null {
 try {const url=new URL(value);return ['https:','http:'].includes(url.protocol)?url.href:null}catch{return null}
}

function inline(text:string):ReactNode[]{
 const tokens=/(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
 return text.split(tokens).map((part,i)=>{
  if(part.startsWith('`')&&part.endsWith('`'))return <code key={i}>{part.slice(1,-1)}</code>;
  if(part.startsWith('**')&&part.endsWith('**'))return <strong key={i}>{part.slice(2,-2)}</strong>;
  const link=part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
  if(link){const href=safeLink(link[2]);if(href)return <a key={i} href={href} target="_blank" rel="noopener noreferrer">{link[1]}</a>}
  return part;
 });
}

// Небольшой поднабор Markdown: React экранирует текст; HTML и схемы кода запрещены.
export function MessageBody({content}:{content:string}){
 const chunks=content.split(/```[^\n]*\n([\s\S]*?)```/g);
 return <div className="message-body">{chunks.map((chunk,index)=>index%2?<pre key={index}><code>{chunk}</code></pre>:chunk.split(/\n\s*\n/).filter(Boolean).map((block,j)=>{
  const lines=block.trim().split('\n'),key=index+':'+j;
  if(lines.every(line=>/^\s*[-*]\s+/.test(line)))return <ul key={key}>{lines.map((line,k)=><li key={k}>{inline(line.replace(/^\s*[-*]\s+/,''))}</li>)}</ul>;
  if(lines.every(line=>/^\s*\d+[.)]\s+/.test(line)))return <ol key={key}>{lines.map((line,k)=><li key={k}>{inline(line.replace(/^\s*\d+[.)]\s+/,''))}</li>)}</ol>;
  return <div key={key}>{lines.map((line,k)=>/^#{1,6}\s/.test(line)?<h4 key={k}>{inline(line.replace(/^#{1,6}\s+/,''))}</h4>:<p key={k}>{inline(line)}</p>)}</div>;
 }))}</div>
}
