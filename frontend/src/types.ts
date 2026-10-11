export const sections = [
  ["chat", "Диалоги"],
  ["spaces", "Пространства"],
  ["activity", "Активность"],
  ["memory", "Память"],
  ["approvals", "Подтверждения"],
  ["tools", "Инструменты"],
  ["telegram", "Telegram"],
  ["skills", "Навыки"],
  ["diary", "Дневник"],
  ["devices", "Устройства"],
  ["voice", "Голос"],
  ["integrations", "Интеграции"],
  ["settings", "Настройки"],
  ["initiative-settings", "Настройки инициатив"],
  ["models", "Модели"],
  ["data", "Данные"],
  ["budget", "Лимиты"],
  ["diagnostics", "Диагностика"],
] as const;

export type SectionId = (typeof sections)[number][0];
export type TaskStatus =
  | "PENDING"
  | "PLANNING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "WAITING_DEVICE"
  | "PAUSED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface AtlasConfig {
  provider: string;
  model: string;
  model_ready?: boolean;
  max_output_tokens?: number;
  stage?: string;
  memory_search?: string;
  external_tools?: boolean;
  builtin_tools?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  description: string;
  pinned: boolean;
  archived: boolean;
  trashed_at: string | null;
  revision: number;
  updated_at: string | null;
}

export interface Message {
  attachments?: import('./Attachments').FileInfo[] | null;
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | string;
  content: string;
  created_at: string;
  initiative_id?: string | null;
  task_id?: string | null;
}

export interface PlanStep {
  tool: string;
  arguments: Record<string, unknown>;
}

export interface Task {
  tool_events?: ToolProgressEvent[];
  model_profile?: {model: string; role?: string; routing_reason?: string} | null;
  id: string;
  conversation_id: string;
  kind: "chat" | "agent" | string;
  status: TaskStatus | string;
  current_step: number;
  dry_run: boolean;
  max_steps: number;
  request_key: string;
  goal: string;
  plan: Array<PlanStep | string>;
  result?: string | null;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

export interface TaskStatusSnapshot {
  current_step?: number;
  id: string;
  conversation_id: string;
  status: TaskStatus | string;
  error?: string | null;
}

export interface ToolProgressEvent {
  id: number;
  task_id: string;
  call_id: string;
  type: 'tool_call' | 'tool_progress';
  tool: string;
  status: string;
  step: number;
  total: number;
}

export interface Memory {
  id: string;
  category: string;
  content: string;
  source: string;
  confidence: number;
  access: string;
  created_at: string;
  confirmed_at: string;
}

export interface Audit {
  id: string;
  task_id?: string | null;
  event: string;
  details?: Record<string, unknown> | null;
  created_at: string;
}
