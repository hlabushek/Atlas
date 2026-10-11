import { ToolProgressEvent } from './types';

const labels: Record<string, string> = {
  PREPARED:'Подготовлен', STARTED:'Выполняется', SUCCEEDED:'Результат проверен',
  DRY_RUN:'Предпросмотр', WAITING_APPROVAL:'Ожидает подтверждения',
  WAITING_DEVICE:'Ожидает устройство', BLOCKED:'Доступ отозван',
  FAILED:'Ошибка', UNKNOWN:'Результат неизвестен', RETRYING:'Ожидает повторной попытки',
};

export function ToolProgress({events}: {events: ToolProgressEvent[]}) {
  const calls = new Map<string, ToolProgressEvent>();
  for (const event of events) calls.set(event.call_id, event);
  if (!calls.size) return null;
  return <div className="tool-progress" role="status" aria-live="polite" aria-label="Прогресс инструментов">
    {[...calls.values()].map(event=><p key={event.call_id}>
      Шаг {event.step}/{event.total} · {event.tool} · {labels[event.status] || 'Обновление состояния'}
    </p>)}
  </div>;
}
