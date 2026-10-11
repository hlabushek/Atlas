import React from 'react';
import {SectionId} from './types';

const groups: {label:string;items:[SectionId,string][]}[]=[
  {label:'Личное',items:[['settings','Общие'],['models','Модели'],['budget','Лимиты'],['data','Данные']]},
  {label:'Подключения',items:[['integrations','Интеграции'],['telegram','Telegram'],['devices','Устройства']]},
  {label:'Возможности',items:[['tools','Инструменты'],['skills','Навыки'],['diary','Дневник'],['initiative-settings','Инициативы']]},
  {label:'Система',items:[['diagnostics','Диагностика']]},
];
export const settingsSections=groups.flatMap(g=>g.items.map(([id])=>id));
export function SettingsNavigation({section,onSelect}:{section:SectionId;onSelect:(id:SectionId)=>void}) {
  return <div className="settings-heading"><div><small>ВАШ ATLAS</small><h1>Настройки</h1><p>Модели, подключения и данные — каждое на своём месте.</p></div><nav className="settings-categories" aria-label="Категории настроек">
    {groups.map(g=><div key={g.label}><small>{g.label}</small><div>{g.items.map(([id,label])=><button key={id} aria-pressed={section===id} className={section===id?'selected':''} onClick={()=>onSelect(id)}>{label}</button>)}</div></div>)}
  </nav><label className="mobile-category-picker">Раздел настроек<select aria-label="Раздел настроек" value={section} onChange={e=>onSelect(e.target.value as SectionId)}>{groups.map(g=><optgroup key={g.label} label={g.label}>{g.items.map(([id,label])=><option key={id} value={id}>{label}</option>)}</optgroup>)}</select></label></div>;
}
