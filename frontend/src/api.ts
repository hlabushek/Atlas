export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {method, credentials: 'same-origin', headers: {'Content-Type':'application/json','X-Atlas-Client':'web'}, body:body===undefined?undefined:JSON.stringify(body)});
  if(!res.headers.get('content-type')?.includes('application/json'))throw new ApiError('API временно недоступен (HTTP '+res.status+')',res.status);
  const data = await res.json();
  if (!res.ok) throw new ApiError(typeof data.detail === 'string' ? data.detail : 'Ошибка запроса',res.status);
  return data;
}
export const cancellable = (status: string) => ['PENDING','PLANNING','RUNNING','WAITING_APPROVAL','WAITING_DEVICE','PAUSED'].includes(status);
