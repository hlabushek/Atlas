import {useEffect,useRef,useState} from 'react';

export function useMobileDrawer(enabled:boolean) {
  const [mobile,setMobile]=useState(()=>matchMedia('(max-width:760px)').matches),[open,setOpen]=useState(false);
  const sidebar=useRef<HTMLElement>(null),trigger=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    const query=matchMedia('(max-width:760px)');const change=()=>{setMobile(query.matches);if(!query.matches)setOpen(false)};
    query.addEventListener('change',change);return()=>query.removeEventListener('change',change);
  },[]);
  useEffect(()=>{
    if(!mobile||!open||!enabled)return;
    const overflow=document.body.style.overflow;document.body.style.overflow='hidden';
    const controls=()=>Array.from(sidebar.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select,textarea,a[href]')||[]).filter(el=>el.getClientRects().length>0&&!el.closest('dialog'));
    controls()[0]?.focus();
    const key=(e:KeyboardEvent)=>{
      if(document.querySelector('dialog[open]'))return;
      if(e.key==='Escape'){e.preventDefault();setOpen(false)}
      if(e.key==='Tab'){
        const items=controls(),first=items[0],last=items.at(-1);
        if(e.shiftKey&&(document.activeElement===first||!sidebar.current?.contains(document.activeElement))){e.preventDefault();last?.focus()}
        else if(!e.shiftKey&&(document.activeElement===last||!sidebar.current?.contains(document.activeElement))){e.preventDefault();first?.focus()}
      }
    };
    document.addEventListener('keydown',key);
    return()=>{document.body.style.overflow=overflow;document.removeEventListener('keydown',key);trigger.current?.focus()};
  },[mobile,open,enabled]);
  return {mobile,open,setOpen,sidebar,trigger};
}
