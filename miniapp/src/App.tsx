import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Center,
  Group,
  SegmentedControl,
  Stack,
  Text,
  TextInput
} from '@mantine/core';
import { useDebouncedValue, useMediaQuery, useReducedMotion } from '@mantine/hooks';
import { motion } from 'framer-motion';
import { IconAlertTriangle } from '@tabler/icons-react';
import ChatsPane from './components/ChatsPane';
import MessagesPane from './components/MessagesPane';
import SettingsPane from './components/SettingsPane';
import TopBar from './components/TopBar';
import type {
  AuthResponse,
  ChatPolicy,
  ChatSummary,
  ConnectionStatus,
  MessageItem,
  MessagesListResponse,
  RpcErr,
  SettingsData,
  SettingsGetResponse,
  SocketEvent,
  TelegramUser
} from './types/app';

const DEFAULT_POLICY: ChatPolicy = { record: true, notify: false, max_messages: 10000 };

interface PendingRpc {
  resolve: (value: SocketEvent) => void;
  reject: (reason?: unknown) => void;
  timer: number;
}

interface AuthPayload {
  op: 'auth.init' | 'auth.resume';
  value: string;
}

type ViewMode = 'chats' | 'messages' | 'settings';

function uuid(): string {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function mergeMessages(current: MessageItem[], incoming: MessageItem[]): MessageItem[] {
  const map = new Map<string, MessageItem>();
  for (const item of current) {
    map.set(item.id, item);
  }

  for (const next of incoming) {
    const prev = map.get(next.id);
    if (!prev) {
      map.set(next.id, { ...next });
      continue;
    }

    const changedText = prev.text !== next.text;
    const oldContent = changedText
      ? prev.text
      : prev.old_content && prev.old_content !== next.text
        ? prev.old_content
        : next.old_content;

    map.set(next.id, {
      ...prev,
      ...next,
      old_content: oldContent,
      edited: prev.edited || next.edited,
      deleted: prev.deleted || next.deleted,
      deleted_at: next.deleted_at || prev.deleted_at
    });
  }

  return [...map.values()].sort((a, b) => a.message_id - b.message_id);
}

function normalizeAuthError(code: string): string {
  if (code === 'AUTH_REQUIRED') return '未在 Telegram 内打开或未登录。';
  if (code === 'AUTH_EXPIRED') return '鉴权已过期，请重新连接。';
  if (code === 'FORBIDDEN') return '当前 Telegram 账号无权限访问该控制台。';
  return `连接失败：${code}`;
}

export default function App() {
  const isDesktop = useMediaQuery('(min-width: 1100px)') ?? false;
  const reduceMotion = useReducedMotion() ?? false;
  const [view, setView] = useState<ViewMode>('chats');

  const [status, setStatus] = useState<ConnectionStatus>('disconnected');
  const [ownerId, setOwnerId] = useState<number | null>(null);
  const [sessionExpiresAt, setSessionExpiresAt] = useState<number | null>(null);
  const [sessionTokenInput, setSessionTokenInput] = useState<string>(() =>
    localStorage.getItem('tgbot_session_token') || ''
  );
  const [authError, setAuthError] = useState<string>('');

  const [search, setSearch] = useState('');
  const [debouncedSearch] = useDebouncedValue(search, 260);
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [selectedChatId, setSelectedChatId] = useState<number | null>(null);

  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  const [settingsData, setSettingsData] = useState<SettingsData | null>(null);
  const [globalPolicyDraft, setGlobalPolicyDraft] = useState<ChatPolicy>(DEFAULT_POLICY);
  const [overridePolicyDraft, setOverridePolicyDraft] = useState<ChatPolicy>(DEFAULT_POLICY);

  const [saving, setSaving] = useState(false);
  const [saveFlash, setSaveFlash] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<Map<string, PendingRpc>>(new Map());
  const reconnectTimerRef = useRef<number | null>(null);
  const reconnectCountRef = useRef(0);
  const manualCloseRef = useRef(false);
  const lastAuthRef = useRef<AuthPayload | null>(null);
  const chatsRefreshTimer = useRef<number | null>(null);

  const tgWebApp = window.Telegram?.WebApp;
  const initData = tgWebApp?.initData || '';
  const user: TelegramUser | null = tgWebApp?.initDataUnsafe?.user || null;

  const clearPending = useCallback((reason: string) => {
    for (const [, pending] of pendingRef.current) {
      window.clearTimeout(pending.timer);
      pending.reject(new Error(reason));
    }
    pendingRef.current.clear();
  }, []);

  const rpc = useCallback(
    (op: string, payload: Record<string, unknown>): Promise<SocketEvent> => {
      const ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        return Promise.reject(new Error('WebSocket not connected'));
      }
      const reqId = uuid();
      ws.send(JSON.stringify({ op, req_id: reqId, ...payload }));

      return new Promise((resolve, reject) => {
        const timer = window.setTimeout(() => {
          pendingRef.current.delete(reqId);
          reject(new Error('RPC timeout'));
        }, 12000);
        pendingRef.current.set(reqId, { resolve, reject, timer });
      });
    },
    []
  );

  const flashSaved = useCallback(() => {
    setSaveFlash(true);
    window.setTimeout(() => setSaveFlash(false), 1200);
  }, []);

  const loadChats = useCallback(async () => {
    const response = (await rpc('chats.list', { limit: 200, query: debouncedSearch || undefined })) as SocketEvent;
    if (response.type !== 'rpc.ok') {
      return;
    }
    const payload = response as { items?: ChatSummary[] };
    setChats(payload.items || []);
  }, [debouncedSearch, rpc]);

  const loadSettings = useCallback(async () => {
    const response = (await rpc('settings.get', {})) as SettingsGetResponse;
    if (response.type !== 'rpc.ok' || !response.settings) {
      return;
    }
    setSettingsData(response.settings);
    setGlobalPolicyDraft(response.settings.global_policy);
  }, [rpc]);

  const loadLatestMessages = useCallback(
    async (chatId: number) => {
      const response = (await rpc('messages.list', { chat_id: chatId, limit: 100 })) as MessagesListResponse;
      if (response.type !== 'rpc.ok') {
        return;
      }
      setMessages((prev) => mergeMessages(prev, response.items || []));
      setNextCursor(response.next || null);
    },
    [rpc]
  );

  const loadOlderMessages = useCallback(async () => {
    if (!selectedChatId || !nextCursor) {
      return;
    }
    const response = (await rpc('messages.list', {
      chat_id: selectedChatId,
      limit: 100,
      before: nextCursor
    })) as MessagesListResponse;
    if (response.type !== 'rpc.ok') {
      return;
    }
    setMessages((prev) => mergeMessages(prev, response.items || []));
    setNextCursor(response.next || null);
  }, [nextCursor, rpc, selectedChatId]);

  const refreshChatsDebounced = useCallback(() => {
    if (chatsRefreshTimer.current) {
      window.clearTimeout(chatsRefreshTimer.current);
    }
    chatsRefreshTimer.current = window.setTimeout(() => {
      void loadChats();
    }, 260);
  }, [loadChats]);

  const closeSocket = useCallback(
    (code: number) => {
      manualCloseRef.current = true;
      if (reconnectTimerRef.current) {
        window.clearTimeout(reconnectTimerRef.current);
        reconnectTimerRef.current = null;
      }
      clearPending('Connection reset');
      wsRef.current?.close(code);
      wsRef.current = null;
      setStatus('disconnected');
    },
    [clearPending]
  );

  const connect = useCallback(
    (auth: AuthPayload) => {
      manualCloseRef.current = false;
      setAuthError('');
      setStatus('connecting');
      clearPending('Connection reset');
      wsRef.current?.close(1000);

      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      const ws = new WebSocket(`${protocol}://${window.location.host}/ws`);
      wsRef.current = ws;
      lastAuthRef.current = auth;

      ws.onopen = async () => {
        try {
          const authPayload =
            auth.op === 'auth.init'
              ? { init_data: auth.value }
              : { session_token: auth.value };
          const response = (await rpc(auth.op, authPayload)) as AuthResponse;
          if (response.type !== 'rpc.ok') {
            return;
          }
          if (response.session_token) {
            localStorage.setItem('tgbot_session_token', response.session_token);
            setSessionTokenInput(response.session_token);
          }
          if (response.owner_id) {
            setOwnerId(response.owner_id);
          }
          if (response.session_expires_at) {
            setSessionExpiresAt(response.session_expires_at);
          }
          setStatus('connected');
          reconnectCountRef.current = 0;
          await Promise.all([loadChats(), loadSettings()]);
          if (selectedChatId) {
            await loadLatestMessages(selectedChatId);
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Auth failed';
          setAuthError(message);
          closeSocket(4001);
        }
      };

      ws.onmessage = (event) => {
        try {
          const payload = JSON.parse(String(event.data)) as SocketEvent;

          if ((payload.type === 'rpc.ok' || payload.type === 'rpc.err') && payload.req_id) {
            const pending = pendingRef.current.get(payload.req_id);
            if (pending) {
              window.clearTimeout(pending.timer);
              pendingRef.current.delete(payload.req_id);
              if (payload.type === 'rpc.ok') {
                pending.resolve(payload);
              } else {
                pending.reject(new Error((payload as RpcErr).code));
              }
            }
            return;
          }

          if (payload.type === 'ready') {
            if (payload.owner_id) setOwnerId(payload.owner_id);
            if (payload.session_expires_at) setSessionExpiresAt(payload.session_expires_at);
            return;
          }

          if (payload.type === 'message.upsert') {
            if (payload.item.chat_id === selectedChatId) {
              setMessages((prev) => mergeMessages(prev, [payload.item]));
            }
            refreshChatsDebounced();
            return;
          }

          if (payload.type === 'message.batch') {
            if (selectedChatId) {
              const scoped = payload.items.filter((item) => item.chat_id === selectedChatId);
              if (scoped.length > 0) {
                setMessages((prev) => mergeMessages(prev, scoped));
              }
            }
            refreshChatsDebounced();
            return;
          }

          if (payload.type === 'chat.invalidate') {
            refreshChatsDebounced();
            if (selectedChatId && payload.chat_id === selectedChatId) {
              void loadLatestMessages(selectedChatId);
            }
            return;
          }

          if (payload.type === 'chat.cleared') {
            if (selectedChatId === payload.chat_id) {
              setMessages([]);
              setNextCursor(null);
            }
            refreshChatsDebounced();
            return;
          }

          if (payload.type === 'settings.updated') {
            setSettingsData(payload.settings);
            setGlobalPolicyDraft(payload.settings.global_policy);
            flashSaved();
          }
        } catch {
          // Ignore broken frames.
        }
      };

      ws.onclose = () => {
        clearPending('Socket closed');
        setStatus('disconnected');

        if (manualCloseRef.current || !lastAuthRef.current) {
          return;
        }

        const delay = Math.min(3000, 500 * 2 ** reconnectCountRef.current);
        reconnectCountRef.current += 1;
        reconnectTimerRef.current = window.setTimeout(() => {
          reconnectTimerRef.current = null;
          if (lastAuthRef.current) {
            connect(lastAuthRef.current);
          }
        }, delay);
      };

      ws.onerror = () => {
        setStatus('disconnected');
      };
    },
    [clearPending, closeSocket, flashSaved, loadChats, loadLatestMessages, loadSettings, refreshChatsDebounced, rpc, selectedChatId]
  );

  useEffect(() => {
    tgWebApp?.ready?.();
    tgWebApp?.expand?.();
    return () => {
      closeSocket(1000);
      if (chatsRefreshTimer.current) {
        window.clearTimeout(chatsRefreshTimer.current);
      }
    };
  }, [closeSocket, tgWebApp]);

  useEffect(() => {
    if (status !== 'connected' || !selectedChatId) {
      return;
    }
    const timer = window.setInterval(() => {
      void loadLatestMessages(selectedChatId);
    }, 4000);
    return () => window.clearInterval(timer);
  }, [loadLatestMessages, selectedChatId, status]);

  useEffect(() => {
    const override =
      selectedChatId && settingsData
        ? settingsData.chat_overrides[String(selectedChatId)] || settingsData.global_policy
        : settingsData?.global_policy || DEFAULT_POLICY;
    setOverridePolicyDraft(override);
  }, [selectedChatId, settingsData]);

  useEffect(() => {
    if (status !== 'connected') {
      return;
    }
    void loadChats();
  }, [debouncedSearch, loadChats, status]);

  const filteredChats = useMemo(() => {
    if (!debouncedSearch.trim()) {
      return chats;
    }
    const q = debouncedSearch.trim().toLowerCase();
    return chats.filter((chat) => {
      const fields = [chat.title, chat.name, chat.username, String(chat.chat_id)].filter(Boolean);
      return fields.some((item) => String(item).toLowerCase().includes(q));
    });
  }, [chats, debouncedSearch]);

  const canAuthByTelegram = Boolean(initData);

  const connectByTelegram = () => {
    if (!initData) {
      setAuthError('当前环境未提供 Telegram initData。请在 Telegram 内打开，或使用 session token。');
      return;
    }
    connect({ op: 'auth.init', value: initData });
  };

  const connectByToken = () => {
    const token = sessionTokenInput.trim();
    if (!token) {
      setAuthError('session token 不能为空');
      return;
    }
    connect({ op: 'auth.resume', value: token });
  };

  const openChat = async (chatId: number) => {
    setSelectedChatId(chatId);
    setView('messages');
    setMessages([]);
    setNextCursor(null);
    await loadLatestMessages(chatId);
  };

  const saveGlobalPolicy = async () => {
    setSaving(true);
    try {
      const response = (await rpc('settings.update', {
        patch: { global_policy: globalPolicyDraft }
      })) as SettingsGetResponse;
      if (response.type === 'rpc.ok' && response.settings) {
        setSettingsData(response.settings);
        flashSaved();
      }
    } finally {
      setSaving(false);
    }
  };

  const saveOverridePolicy = async () => {
    if (!selectedChatId) return;
    setSaving(true);
    try {
      const response = (await rpc('settings.update', {
        patch: {
          chat_id: selectedChatId,
          override: overridePolicyDraft
        }
      })) as SettingsGetResponse;
      if (response.type === 'rpc.ok' && response.settings) {
        setSettingsData(response.settings);
        flashSaved();
      }
    } finally {
      setSaving(false);
    }
  };

  const clearOverridePolicy = async () => {
    if (!selectedChatId) return;
    setSaving(true);
    try {
      const response = (await rpc('settings.update', {
        patch: {
          chat_id: selectedChatId,
          clear_override: true
        }
      })) as SettingsGetResponse;
      if (response.type === 'rpc.ok' && response.settings) {
        setSettingsData(response.settings);
        setOverridePolicyDraft(response.settings.global_policy);
        flashSaved();
      }
    } finally {
      setSaving(false);
    }
  };

  const clearChat = async () => {
    if (!selectedChatId) return;
    setSaving(true);
    try {
      await rpc('chat.clear', { chat_id: selectedChatId });
      setMessages([]);
      setNextCursor(null);
      await loadChats();
    } finally {
      setSaving(false);
    }
  };

  const exportChat = async () => {
    if (!selectedChatId) return;
    setSaving(true);
    try {
      await rpc('export.chat', { chat_id: selectedChatId });
      flashSaved();
    } finally {
      setSaving(false);
    }
  };

  const transition = reduceMotion
    ? { duration: 0 }
    : { duration: 0.22, ease: [0.2, 0.8, 0.2, 1] as const };

  if (status !== 'connected') {
    return (
      <div className="app-root">
        <TopBar
          user={user}
          ownerId={ownerId}
          status={status}
          sessionExpiresAt={sessionExpiresAt}
          onOpenSettings={() => setView('settings')}
        />

        <Center className="page-body">
          <motion.div initial={reduceMotion ? false : { opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={transition}>
            <Card className="glass auth-card" p="lg" maw={560}>
              <Stack gap="sm">
                <Text fw={700} size="lg">
                  鉴权
                </Text>
                <Text size="sm" c="dimmed">
                  支持 Telegram WebApp initData 与 session token 恢复。若出现 401，请在 Telegram 内重新打开 Mini App。
                </Text>
                <Group>
                  <Button disabled={!canAuthByTelegram || status === 'connecting'} loading={status === 'connecting'} onClick={connectByTelegram}>
                    Telegram 鉴权
                  </Button>
                </Group>
                <TextInput
                  label="Session Token"
                  placeholder="粘贴短期 token"
                  value={sessionTokenInput}
                  onChange={(event) => setSessionTokenInput(event.currentTarget.value)}
                />
                <Group>
                  <Button variant="light" disabled={status === 'connecting'} onClick={connectByToken}>
                    Token 恢复
                  </Button>
                </Group>
                {authError ? (
                  <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                    {normalizeAuthError(authError.replace('Error: ', ''))}
                  </Alert>
                ) : null}
              </Stack>
            </Card>
          </motion.div>
        </Center>
      </div>
    );
  }

  const showDesktopSettings = isDesktop && view === 'settings';

  return (
    <div className="app-root">
      <TopBar
        user={user}
        ownerId={ownerId}
        status={status}
        sessionExpiresAt={sessionExpiresAt}
        onOpenSettings={() => setView('settings')}
      />

      <div className="page-body">
        {showDesktopSettings ? (
          <motion.div initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={transition} className="mobile-layout">
            <SettingsPane
              globalPolicy={globalPolicyDraft}
              saveFlash={saveFlash}
              loading={saving}
              onChange={setGlobalPolicyDraft}
              onSave={() => void saveGlobalPolicy()}
            />
          </motion.div>
        ) : isDesktop ? (
          <motion.div initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={transition} className="desktop-layout">
            <ChatsPane
              user={user}
              chats={filteredChats}
              selectedChatId={selectedChatId}
              search={search}
              reducedMotion={reduceMotion}
              onSearchChange={setSearch}
              onRefresh={() => void loadChats()}
              onSelectChat={(chatId) => void openChat(chatId)}
            />
            <MessagesPane
              chatId={selectedChatId}
              messages={messages}
              hasMore={Boolean(nextCursor)}
              loading={saving}
              saveFlash={saveFlash}
              overridePolicy={overridePolicyDraft}
              reducedMotion={reduceMotion}
              onLoadLatest={() => (selectedChatId ? void loadLatestMessages(selectedChatId) : undefined)}
              onLoadOlder={() => void loadOlderMessages()}
              onExportChat={() => void exportChat()}
              onClearChat={() => void clearChat()}
              onOverrideChange={setOverridePolicyDraft}
              onSaveOverride={() => void saveOverridePolicy()}
              onClearOverride={() => void clearOverridePolicy()}
            />
          </motion.div>
        ) : (
          <motion.div initial={reduceMotion ? false : { opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={transition} className="mobile-layout">
            {view === 'chats' ? (
              <ChatsPane
                user={user}
                chats={filteredChats}
                selectedChatId={selectedChatId}
                search={search}
                reducedMotion={reduceMotion}
                onSearchChange={setSearch}
                onRefresh={() => void loadChats()}
                onSelectChat={(chatId) => void openChat(chatId)}
              />
            ) : null}
            {view === 'messages' ? (
              <MessagesPane
                chatId={selectedChatId}
                messages={messages}
                hasMore={Boolean(nextCursor)}
                loading={saving}
                saveFlash={saveFlash}
                overridePolicy={overridePolicyDraft}
                reducedMotion={reduceMotion}
                onLoadLatest={() => (selectedChatId ? void loadLatestMessages(selectedChatId) : undefined)}
                onLoadOlder={() => void loadOlderMessages()}
                onExportChat={() => void exportChat()}
                onClearChat={() => void clearChat()}
                onOverrideChange={setOverridePolicyDraft}
                onSaveOverride={() => void saveOverridePolicy()}
                onClearOverride={() => void clearOverridePolicy()}
              />
            ) : null}
            {view === 'settings' ? (
              <SettingsPane
                globalPolicy={globalPolicyDraft}
                saveFlash={saveFlash}
                loading={saving}
                onChange={setGlobalPolicyDraft}
                onSave={() => void saveGlobalPolicy()}
              />
            ) : null}
            <Card className="glass mobile-tabs" p={6}>
              <SegmentedControl
                fullWidth
                value={view}
                onChange={(value) => setView(value as ViewMode)}
                data={[
                  { label: 'Chats', value: 'chats' },
                  { label: 'Messages', value: 'messages' },
                  { label: 'Settings', value: 'settings' }
                ]}
              />
            </Card>
          </motion.div>
        )}
      </div>
    </div>
  );
}
