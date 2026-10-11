import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { Message, Task, TaskStatusSnapshot, ToolProgressEvent } from "./types";

interface RealtimeOptions {
  enabled: boolean;
  conversationId: string;
  onTasks: (update: (current: Task[]) => Task[]) => void;
  onMessages: (messages: Message[]) => void;
}

export function useAtlasRealtime({
  enabled,
  conversationId,
  onTasks,
  onMessages,
}: RealtimeOptions): boolean {
  const [connected, setConnected] = useState(false);
  const statuses = useRef(new Map<string, string>());

  useEffect(() => {
    if (!enabled) {
      setConnected(false);
      return;
    }
    let stopped = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const progress = new Map<string, ToolProgressEvent[]>();

    const connect = () => {
      socket = new WebSocket(
        (location.protocol === "https:" ? "wss://" : "ws://") +
          location.host +
          "/api/events",
      );
      socket.onopen = () => setConnected(true);
      socket.onmessage = (event) => {
        if (stopped) return;
        let payload: { type?: string; tasks?: TaskStatusSnapshot[]; events?: ToolProgressEvent[] };
        try {
          payload = JSON.parse(event.data) as {
            type?: string;
            tasks?: TaskStatusSnapshot[];
            events?: ToolProgressEvent[];
          };
        } catch {
          return;
        }
        if (payload.type === 'tool_events' && Array.isArray(payload.events)) {
          progress.clear();
          for (const item of payload.events) {
            if (typeof item.task_id !== 'string' || typeof item.id !== 'number') continue;
            progress.set(item.task_id, [...(progress.get(item.task_id) || []), item].slice(-64));
          }
          onTasks(current => current.map(task => ({...task, tool_events: progress.get(task.id) || []})));
          return;
        }
        if (payload.type !== "tasks" || !payload.tasks) return;
        const snapshot = payload.tasks;
        let activeConversationChanged = false;
        let hasUnknownTask = false;
        for (const item of snapshot) {
          if (!statuses.current.has(item.id)) hasUnknownTask = true;
          if (
            item.conversation_id === conversationId &&
            statuses.current.get(item.id) !== item.status
          ) {
            activeConversationChanged = true;
          }
          statuses.current.set(item.id, item.status);
        }
        onTasks((current) => {
          const byId = new Map(snapshot.map((item) => [item.id, item]));
          return current.map((task) => {
            const update = byId.get(task.id);
            return update ? { ...task, ...update, tool_events: progress.get(task.id) || [] } : task;
          });
        });
        if (hasUnknownTask) {
          api<Task[]>("/tasks?limit=100")
            .then((tasks) => {if(!stopped)onTasks(() => tasks.map(task=>({...task,tool_events:progress.get(task.id)||[]})))})
            .catch(() => {});
        }
        if (activeConversationChanged && conversationId) {
          api<Message[]>(
            "/conversations/" + conversationId + "/messages?limit=100",
          )
            .then(rows=>{if(!stopped)onMessages(rows)})
            .catch(() => {});
        }
      };
      socket.onclose = () => {
        setConnected(false);
        if (!stopped) timer = setTimeout(connect, 3000);
      };
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [conversationId, enabled, onMessages, onTasks]);

  return connected;
}
