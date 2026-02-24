export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected';

export interface ChatPolicy {
  record: boolean;
  notify: boolean;
  max_messages: number;
}

export interface SettingsData {
  global_policy: ChatPolicy;
  chat_overrides: Record<string, ChatPolicy>;
}

export interface ChatSummary {
  chat_id: number;
  last_activity: number;
  message_count: number;
  deleted_count: number;
  title?: string;
  username?: string;
  name?: string;
  photo_url?: string;
  strategy_flag?: string;
}

export interface MessageItem {
  id: string;
  chat_id: number;
  message_id: number;
  text: string;
  created_at: number;
  updated_at: number;
  edited: boolean;
  deleted: boolean;
  deleted_at: number | null;
  old_content?: string;
}

export interface TelegramUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
}

export interface TelegramWebApp {
  initData?: string;
  initDataUnsafe?: {
    user?: TelegramUser;
  };
  ready?: () => void;
  expand?: () => void;
}

export interface TelegramGlobal {
  WebApp?: TelegramWebApp;
}

export interface RpcOkBase {
  type: 'rpc.ok';
  op: string;
  req_id?: string | null;
}

export interface RpcErr {
  type: 'rpc.err';
  op: string;
  req_id?: string | null;
  code: string;
}

export interface ReadyEvent {
  type: 'ready';
  server_time: number;
  owner_id?: number;
  session_expires_at?: number;
}

export interface MessageUpsertEvent {
  type: 'message.upsert';
  item: MessageItem;
}

export interface MessageBatchEvent {
  type: 'message.batch';
  items: MessageItem[];
}

export interface ChatInvalidateEvent {
  type: 'chat.invalidate';
  chat_id: number;
}

export interface ChatClearedEvent {
  type: 'chat.cleared';
  chat_id: number;
}

export interface SettingsUpdatedEvent {
  type: 'settings.updated';
  settings: SettingsData;
}

export type SocketEvent =
  | RpcOkBase
  | RpcErr
  | ReadyEvent
  | MessageUpsertEvent
  | MessageBatchEvent
  | ChatInvalidateEvent
  | ChatClearedEvent
  | SettingsUpdatedEvent
  | { type: 'pong'; server_time: number };

export interface AuthResponse extends RpcOkBase {
  session_token?: string;
  session_expires_at?: number;
  owner_id?: number;
}

export interface ChatsListResponse extends RpcOkBase {
  items?: ChatSummary[];
  next?: number | null;
  server_time?: number;
}

export interface MessagesListResponse extends RpcOkBase {
  items?: MessageItem[];
  next?: string | null;
}

export interface SettingsGetResponse extends RpcOkBase {
  settings?: SettingsData;
}
