<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

type ConnectionStatus = 'disconnected' | 'connecting' | 'connected';
type ViewMode = 'chats' | 'messages' | 'settings';

interface ChatPolicy {
  record: boolean;
  notify: boolean;
  max_messages: number;
}

interface SettingsData {
  global_policy: ChatPolicy;
  chat_overrides: Record<string, ChatPolicy>;
}

interface ChatSummary {
  peer_id?: number;
  chat_id: number;
  last_activity: number;
  message_count: number;
  deleted_count: number;
  title?: string;
  username?: string;
  name?: string;
  photo_url?: string;
  strategy_flag?: string;
  override?: boolean;
  policy?: ChatPolicy;
}

interface MessageItem {
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
  sender_id?: number | null;
  sender_username?: string | null;
  sender_name?: string | null;
  peer_username?: string | null;
  peer_name?: string | null;
  peer_id?: number;
}

interface RpcOk {
  type: 'rpc.ok';
  op: string;
  req_id?: string;
  [k: string]: unknown;
}

const DEFAULT_POLICY: ChatPolicy = { record: true, notify: false, max_messages: 10000 };

const status = ref<ConnectionStatus>('disconnected');
const view = ref<ViewMode>('chats');
const ownerId = ref<number | null>(null);
const sessionExpiresAt = ref<number | null>(null);
const authError = ref('');
const saving = ref(false);
const saveFlash = ref(false);
const showOpsMenu = ref(false);

const chats = ref<ChatSummary[]>([]);
const selectedChatId = ref<number | null>(null);
const messages = ref<MessageItem[]>([]);
const nextCursor = ref<string | null>(null);
const pinnedChatIds = ref<number[]>([]);

const settingsData = ref<SettingsData | null>(null);
const globalPolicy = ref<ChatPolicy>({ ...DEFAULT_POLICY });
const overridePolicy = ref<ChatPolicy>({ ...DEFAULT_POLICY });

const sessionToken = ref(localStorage.getItem('tgbot_session_token') || '');

const isDesktop = ref(window.matchMedia('(min-width: 1100px)').matches);
const reduceMotion = ref(window.matchMedia('(prefers-reduced-motion: reduce)').matches);

const tgWebApp = window.Telegram?.WebApp;
const tgUser = computed(() => tgWebApp?.initDataUnsafe?.user ?? null);
const tgInitData = computed(() => tgWebApp?.initData || '');

const ws = ref<WebSocket | null>(null);
let wsEpoch = 0;
let reconnectTimer: number | null = null;
let reconnectCount = 0;
let pollingTimer: number | null = null;
let chatsRefreshTimer: number | null = null;
let flashTimer: number | null = null;
let manualClose = false;
let lastAuth: { op: 'auth.init' | 'auth.resume'; value: string } | null = null;

const pending = new Map<string, { timer: number; resolve: (v: RpcOk) => void; reject: (e: Error) => void }>();

function uuid(): string {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function formatTime(ts: number | null | undefined): string {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString('zh-CN', {
    hour12: false,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function normalizeAuthError(code: string): string {
  if (code === 'AUTH_REQUIRED') return '未在 Telegram 内打开或未登录。';
  if (code === 'AUTH_EXPIRED') return '鉴权已过期，请重新连接。';
  if (code === 'FORBIDDEN') return '当前 Telegram 账号无权限访问该控制台。';
  return `连接失败：${code}`;
}

function canResumeCode(code: string): boolean {
  return code === 'AUTH_EXPIRED' || code === 'AUTH_REQUIRED' || code === 'AUTH_INVALID';
}

function senderLabel(item: MessageItem): string {
  if (item.sender_username) return `@${item.sender_username}`;
  if (item.sender_name) return item.sender_name;
  return "unknown";
}

function chatDisplayName(chat: ChatSummary): string {
  return chat.title || chat.name || (chat.username ? `@${chat.username}` : '') || `Chat ${chat.chat_id}`;
}

function chatDisplayUsername(chat: ChatSummary): string {
  return chat.username ? `@${chat.username}` : (chat.name || 'no-username');
}

function chatHeaderIdentity(chat: ChatSummary): string {
  const uname = chat.username ? `@${chat.username}` : 'no-username';
  const display = chat.name || chat.title || `peer ${chat.chat_id}`;
  return `${uname} · ${display}`;
}

function isOwnMessage(item: MessageItem): boolean {
  if (ownerId.value && item.sender_id && Number(item.sender_id) === Number(ownerId.value)) {
    return true;
  }
  const myId = tgUser.value?.id;
  if (myId && item.sender_id && Number(item.sender_id) === Number(myId)) {
    return true;
  }
  if (tgUser.value?.username && item.sender_username) {
    return tgUser.value.username.toLowerCase() === item.sender_username.toLowerCase();
  }
  return false;
}

function mergeMessages(current: MessageItem[], incoming: MessageItem[]): MessageItem[] {
  const map = new Map<string, MessageItem>();
  for (const item of current) map.set(item.id, item);

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
      deleted_at: next.deleted_at || prev.deleted_at,
      sender_id: next.sender_id ?? prev.sender_id ?? null,
      sender_username: next.sender_username || prev.sender_username || null,
      sender_name: next.sender_name || prev.sender_name || null,
      peer_username: next.peer_username || prev.peer_username || null,
      peer_name: next.peer_name || prev.peer_name || null
    });
  }

  return [...map.values()].sort((a, b) => a.message_id - b.message_id);
}

function clearPending(reason: string): void {
  for (const item of pending.values()) {
    window.clearTimeout(item.timer);
    item.reject(new Error(reason));
  }
  pending.clear();
}

function rpc(op: string, payload: Record<string, unknown>): Promise<RpcOk> {
  if (!ws.value || ws.value.readyState !== WebSocket.OPEN) {
    return Promise.reject(new Error('WebSocket not connected'));
  }

  const reqId = uuid();
  ws.value.send(JSON.stringify({ op, req_id: reqId, ...payload }));

  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      pending.delete(reqId);
      reject(new Error('RPC timeout'));
    }, 12000);
    pending.set(reqId, { timer, resolve, reject });
  });
}

async function loadChats(): Promise<void> {
  const response = await rpc('chats.list', { limit: 200 });
  chats.value = ((response.items as ChatSummary[] | undefined) || []).slice();
}

async function loadSettings(): Promise<void> {
  const response = await rpc('settings.get', {});
  const settings = response.settings as SettingsData | undefined;
  if (!settings) return;
  settingsData.value = settings;
  globalPolicy.value = { ...settings.global_policy };
}

async function loadLatestMessages(chatId: number): Promise<void> {
  const response = await rpc('messages.list', { chat_id: chatId, limit: 100 });
  messages.value = mergeMessages(messages.value, (response.items as MessageItem[] | undefined) || []);
  nextCursor.value = (response.next as string | null | undefined) ?? null;
}

async function loadOlderMessages(): Promise<void> {
  if (!selectedChatId.value || !nextCursor.value) return;
  const response = await rpc('messages.list', {
    chat_id: selectedChatId.value,
    limit: 100,
    before: nextCursor.value
  });
  messages.value = mergeMessages(messages.value, (response.items as MessageItem[] | undefined) || []);
  nextCursor.value = (response.next as string | null | undefined) ?? null;
}

function triggerSavedFlash(): void {
  saveFlash.value = true;
  if (flashTimer) window.clearTimeout(flashTimer);
  flashTimer = window.setTimeout(() => {
    saveFlash.value = false;
  }, 1200);
}

function scheduleChatsRefresh(): void {
  if (chatsRefreshTimer) window.clearTimeout(chatsRefreshTimer);
  chatsRefreshTimer = window.setTimeout(() => {
    void loadChats();
  }, 260);
}

function closeSocket(code = 1000): void {
  manualClose = true;
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
  clearPending('Connection reset');
  ws.value?.close(code);
  ws.value = null;
  status.value = 'disconnected';
}

function handleSocketFrame(frame: unknown): void {
  if (!frame || typeof frame !== 'object') return;
  const payload = frame as Record<string, unknown>;
  const type = String(payload.type || '');

  if ((type === 'rpc.ok' || type === 'rpc.err') && typeof payload.req_id === 'string') {
    const p = pending.get(payload.req_id);
    if (!p) return;
    window.clearTimeout(p.timer);
    pending.delete(payload.req_id);
    if (type === 'rpc.ok') {
      p.resolve(payload as unknown as RpcOk);
    } else {
      p.reject(new Error(String(payload.code || 'RPC_ERROR')));
    }
    return;
  }

  if (type === 'ready') {
    if (typeof payload.owner_id === 'number') ownerId.value = payload.owner_id;
    if (typeof payload.session_expires_at === 'number') sessionExpiresAt.value = payload.session_expires_at;
    return;
  }

  if (type === 'message.upsert') {
    const item = payload.item as MessageItem | undefined;
    if (!item) return;
    if (selectedChatId.value && item.chat_id === selectedChatId.value) {
      messages.value = mergeMessages(messages.value, [item]);
    }
    scheduleChatsRefresh();
    return;
  }

  if (type === 'message.batch') {
    const batch = (payload.items as MessageItem[] | undefined) || [];
    if (selectedChatId.value) {
      const scoped = batch.filter((x) => x.chat_id === selectedChatId.value);
      if (scoped.length > 0) {
        messages.value = mergeMessages(messages.value, scoped);
      }
    }
    scheduleChatsRefresh();
    return;
  }

  if (type === 'chat.invalidate') {
    scheduleChatsRefresh();
    if (selectedChatId.value && payload.chat_id === selectedChatId.value) {
      void loadLatestMessages(selectedChatId.value);
    }
    return;
  }

  if (type === 'chat.cleared') {
    if (selectedChatId.value && payload.chat_id === selectedChatId.value) {
      messages.value = [];
      nextCursor.value = null;
    }
    scheduleChatsRefresh();
    return;
  }

  if (type === 'settings.updated' && payload.settings) {
    settingsData.value = payload.settings as SettingsData;
    globalPolicy.value = { ...(payload.settings as SettingsData).global_policy };
    triggerSavedFlash();
  }
}

function connect(auth: { op: 'auth.init' | 'auth.resume'; value: string }): void {
  manualClose = false;
  status.value = 'connecting';
  authError.value = '';
  clearPending('Connection reset');
  const prev = ws.value;
  wsEpoch += 1;
  const epoch = wsEpoch;
  if (prev) {
    try {
      prev.close(1000);
    } catch {
      // ignore
    }
  }

  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  ws.value = socket;
  lastAuth = auth;

  socket.onopen = async () => {
    if (epoch !== wsEpoch || ws.value !== socket) return;
    try {
      const authPayload = auth.op === 'auth.init' ? { init_data: auth.value } : { session_token: auth.value };
      const response = await rpc(auth.op, authPayload);

      const issuedSessionToken = response.session_token as string | undefined;
      if (issuedSessionToken) {
        sessionToken.value = issuedSessionToken;
        localStorage.setItem('tgbot_session_token', issuedSessionToken);
      }

      if (typeof response.owner_id === 'number') ownerId.value = response.owner_id;
      if (typeof response.session_expires_at === 'number') sessionExpiresAt.value = response.session_expires_at;

      reconnectCount = 0;
      status.value = 'connected';

      await Promise.all([loadChats(), loadSettings()]);
      if (selectedChatId.value) {
        await loadLatestMessages(selectedChatId.value);
      }
    } catch (e) {
      const code = e instanceof Error ? e.message : 'UNKNOWN';
      if (auth.op === 'auth.init' && canResumeCode(code) && sessionToken.value) {
        connect({ op: 'auth.resume', value: sessionToken.value });
        return;
      }
      authError.value = normalizeAuthError(code);
      closeSocket(4001);
    }
  };

  socket.onmessage = (event) => {
    if (epoch !== wsEpoch || ws.value !== socket) return;
    try {
      const frame = JSON.parse(String(event.data));
      handleSocketFrame(frame);
    } catch {
      // ignore bad frame
    }
  };

  socket.onclose = () => {
    if (epoch !== wsEpoch || ws.value !== socket) return;
    status.value = 'disconnected';
    clearPending('Socket closed');
    if (manualClose || !lastAuth) return;

    const delay = Math.min(3000, 500 * 2 ** reconnectCount);
    reconnectCount += 1;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      if (lastAuth) connect(lastAuth);
    }, delay);
  };

  socket.onerror = () => {
    if (epoch !== wsEpoch || ws.value !== socket) return;
    status.value = 'disconnected';
  };
}

function connectByTelegram(): void {
  if (!tgInitData.value) {
    authError.value = '当前环境没有 Telegram initData，请在 Telegram 内打开。';
    return;
  }
  connect({ op: 'auth.init', value: tgInitData.value });
}

async function openChat(chatId: number): Promise<void> {
  selectedChatId.value = chatId;
  view.value = 'messages';
  showOpsMenu.value = false;
  messages.value = [];
  nextCursor.value = null;
  await loadLatestMessages(chatId);
}

async function saveGlobalPolicy(): Promise<void> {
  saving.value = true;
  try {
    const response = await rpc('settings.update', { patch: { global_policy: globalPolicy.value } });
    const settings = response.settings as SettingsData | undefined;
    if (settings) {
      settingsData.value = settings;
      triggerSavedFlash();
    }
  } finally {
    saving.value = false;
  }
}

async function saveOverridePolicy(): Promise<void> {
  if (!selectedChatId.value) return;
  saving.value = true;
  try {
    const response = await rpc('settings.update', {
      patch: {
        peer_id: selectedChatId.value,
        override: overridePolicy.value
      }
    });
    const settings = response.settings as SettingsData | undefined;
    if (settings) {
      settingsData.value = settings;
      triggerSavedFlash();
    }
  } finally {
    saving.value = false;
  }
}

async function clearChat(): Promise<void> {
  if (!selectedChatId.value) return;
  saving.value = true;
  try {
    await rpc('chat.clear', { chat_id: selectedChatId.value });
    messages.value = [];
    nextCursor.value = null;
    await loadChats();
  } finally {
    saving.value = false;
  }
}

async function exportChat(): Promise<void> {
  if (!selectedChatId.value) return;
  saving.value = true;
  try {
    await rpc('export.chat', { chat_id: selectedChatId.value });
    triggerSavedFlash();
  } finally {
    saving.value = false;
  }
}

const orderedChats = computed(() => {
  return [...chats.value].sort((a, b) => {
    const aPinned = pinnedChatIds.value.includes(a.chat_id);
    const bPinned = pinnedChatIds.value.includes(b.chat_id);
    if (aPinned !== bPinned) return aPinned ? -1 : 1;
    return b.last_activity - a.last_activity;
  });
});

const selectedChat = computed(() => {
  if (!selectedChatId.value) return null;
  return chats.value.find((chat) => chat.chat_id === selectedChatId.value) || null;
});

watch([selectedChatId, settingsData], () => {
  if (!settingsData.value) return;
  const override =
    selectedChatId.value && settingsData.value.chat_overrides[String(selectedChatId.value)]
      ? settingsData.value.chat_overrides[String(selectedChatId.value)]
      : settingsData.value.global_policy;
  overridePolicy.value = { ...override };
});

watch([view, selectedChatId], () => {
  showOpsMenu.value = false;
});

function isPinned(chatId: number | null): boolean {
  if (!chatId) return false;
  return pinnedChatIds.value.includes(chatId);
}

function togglePinCurrent(): void {
  if (!selectedChatId.value) return;
  if (isPinned(selectedChatId.value)) {
    pinnedChatIds.value = pinnedChatIds.value.filter((id) => id !== selectedChatId.value);
  } else {
    pinnedChatIds.value = [selectedChatId.value, ...pinnedChatIds.value];
  }
  localStorage.setItem('tgbot_pinned_chat_ids', JSON.stringify(pinnedChatIds.value));
  showOpsMenu.value = false;
}

watch([status, selectedChatId], () => {
  if (pollingTimer) {
    window.clearInterval(pollingTimer);
    pollingTimer = null;
  }
  if (status.value !== 'connected' || !selectedChatId.value) return;
  pollingTimer = window.setInterval(() => {
    if (selectedChatId.value) {
      void loadLatestMessages(selectedChatId.value);
    }
  }, 4000);
});

const onResize = () => {
  isDesktop.value = window.matchMedia('(min-width: 1100px)').matches;
  reduceMotion.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
};

onMounted(() => {
  window.addEventListener('resize', onResize);
  tgWebApp?.ready?.();
  tgWebApp?.expand?.();
  try {
    const raw = localStorage.getItem('tgbot_pinned_chat_ids');
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (Array.isArray(parsed)) {
      pinnedChatIds.value = parsed.map((x) => Number(x)).filter((x) => Number.isInteger(x) && x > 0);
    }
  } catch {
    pinnedChatIds.value = [];
  }
  if (tgInitData.value) {
    connect({ op: 'auth.init', value: tgInitData.value });
  } else {
    authError.value = '请通过 Telegram 链接打开此页面';
  }
});

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize);
  closeSocket(1000);
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  if (pollingTimer) window.clearInterval(pollingTimer);
  if (chatsRefreshTimer) window.clearTimeout(chatsRefreshTimer);
  if (flashTimer) window.clearTimeout(flashTimer);
});
</script>

<template>
  <div class="app-root">
    <main class="main-pane">
      <section v-if="status !== 'connected'" class="auth-shell">
        <div class="auth-card glass" :class="{ 'reduce-motion': reduceMotion }">
          <h2>鉴权连接</h2>
          <p>仅支持 Telegram 链接鉴权。页面会自动连接并在断线后自动恢复。</p>

          <div class="btn-row">
            <button class="btn btn-primary" :disabled="!tgInitData || status === 'connecting'" @click="connectByTelegram">
              重新连接 Telegram
            </button>
          </div>

          <div v-if="authError" class="alert">{{ authError }}</div>
        </div>
      </section>

      <section v-else-if="isDesktop && view === 'settings'" class="single-settings">
        <div class="pane glass settings-pane">
          <div class="pane-head">
            <h3>Me</h3>
            <span class="save-state" :class="{ ok: saveFlash }">{{ saveFlash ? '已保存' : '保存后广播 settings.updated' }}</span>
          </div>
          <div class="me-profile-card">
            <img v-if="tgUser?.photo_url" :src="tgUser.photo_url" class="avatar me-avatar" alt="me-avatar" />
            <div v-else class="avatar me-avatar">{{ (tgUser?.first_name || tgUser?.username || 'U').slice(0, 1) }}</div>
            <div class="me-profile-text">
              <div class="me-profile-name">{{ tgUser?.first_name || tgUser?.username || 'Telegram User' }}</div>
              <div class="me-profile-meta">
                <span>{{ tgUser?.username ? '@' + tgUser.username : 'no-username' }}</span>
                <span>ID {{ ownerId || '-' }}</span>
              </div>
            </div>
            <span class="pill" :class="status">{{ status }}</span>
          </div>
          <div class="policy-box me-status-card">
            <h4>我的状态</h4>
            <div class="policy-grid">
              <div class="muted">用户: {{ tgUser?.username ? '@' + tgUser.username : tgUser?.first_name || 'unknown' }}</div>
              <div class="muted">连接: {{ status }}</div>
              <div class="muted">Owner ID: {{ ownerId || '-' }}</div>
              <div class="muted">Session: {{ sessionExpiresAt ? formatTime(sessionExpiresAt) : '未鉴权' }}</div>
            </div>
          </div>
          <div class="policy-box me-policy-card">
            <h4>全局默认设置</h4>
            <div class="policy-grid">
              <label><input type="checkbox" v-model="globalPolicy.record" /> record</label>
              <label><input type="checkbox" v-model="globalPolicy.notify" /> notify</label>
              <label class="field-inline">max_messages <input type="number" min="100" max="200000" step="100" v-model.number="globalPolicy.max_messages" /></label>
              <button class="btn btn-primary me-save-btn" :disabled="saving" @click="saveGlobalPolicy">保存全局默认</button>
            </div>
          </div>
        </div>
      </section>

      <section v-else-if="isDesktop" class="desktop-layout">
        <div class="pane glass chats-pane">
          <div class="pane-head compact"><h3>DMs</h3></div>

          <transition-group name="chat" tag="div" class="chat-list">
            <button
              v-for="(chat, index) in orderedChats"
              :key="chat.chat_id"
              class="chat-item"
              :class="{ active: selectedChatId === chat.chat_id }"
              :style="{ '--stagger': `${Math.min(index, 12) * 28}ms` }"
              @click="openChat(chat.chat_id)"
            >
              <div class="chat-main">
                <div class="avatar mini" :style="chat.photo_url ? `background-image:url('${chat.photo_url}')` : ''">
                  <span v-if="!chat.photo_url">{{ chatDisplayName(chat).slice(0, 1) }}</span>
                </div>
                <div class="chat-text">
                  <div class="chat-title">{{ chatDisplayName(chat) }}</div>
                  <div class="chat-meta-row">
                    <span class="chat-preview">{{ chatDisplayUsername(chat) }}</span>
                    <span class="chat-peer">peer {{ chat.peer_id || chat.chat_id }}</span>
                  </div>
                  <div class="chat-setting-row">
                    <span class="setting-chip" :class="{ active: !!chat.policy?.record }">R {{ chat.policy?.record ? 'on' : 'off' }}</span>
                    <span class="setting-chip" :class="{ active: !!chat.policy?.notify }">N {{ chat.policy?.notify ? 'on' : 'off' }}</span>
                    <span class="setting-chip override">{{ chat.override ? 'override' : 'global' }}</span>
                  </div>
                </div>
              </div>
            </button>
          </transition-group>
        </div>

        <div class="pane glass messages-pane">
          <div class="pane-head">
            <h3>Msgs {{ selectedChat ? chatHeaderIdentity(selectedChat) : '' }}</h3>
            <div class="head-actions">
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="showOpsMenu = !showOpsMenu">聊天设置</button>
            </div>
          </div>
          <transition name="chat">
            <div v-if="showOpsMenu" class="ops-menu">
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="exportChat">导出聊天记录 (Bot TXT)</button>
              <button class="btn btn-danger" :disabled="!selectedChatId || saving" @click="clearChat">删除聊天记录</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="togglePinCurrent">{{ isPinned(selectedChatId) ? '取消置顶' : '置顶聊天记录' }}</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="selectedChatId && loadLatestMessages(selectedChatId)">加载最新消息</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || !nextCursor || saving" @click="loadOlderMessages">加载更旧消息</button>
              <label class="ops-switch"><input type="checkbox" v-model="overridePolicy.record" :disabled="!selectedChatId || saving" /> record</label>
              <label class="ops-switch"><input type="checkbox" v-model="overridePolicy.notify" :disabled="!selectedChatId || saving" /> notify</label>
              <button class="btn btn-primary me-save-btn" :disabled="!selectedChatId || saving" @click="saveOverridePolicy">保存聊天设置</button>
            </div>
          </transition>

          <div class="messages-stream">
            <div v-if="messages.length === 0" class="messages-empty">
              <strong>暂无消息</strong>
              <span>选择会话后可加载最新页，支持轮询与分页</span>
            </div>

            <transition-group v-else name="msg" tag="div" class="msg-list">
              <article
                v-for="item in messages"
                :key="item.id"
                class="message-bubble"
                :class="{ deleted: item.deleted, self: isOwnMessage(item) }"
              >
                <div class="msg-head">
                  <span>#{{ item.message_id }} · {{ senderLabel(item) }}</span>
                  <span class="time">
                    {{
                      item.deleted
                        ? '删除于 ' + formatTime(item.deleted_at || item.updated_at)
                        : item.edited
                          ? '编辑于 ' + formatTime(item.updated_at)
                          : '发送于 ' + formatTime(item.created_at)
                    }}
                  </span>
                </div>

                <div v-if="item.old_content && item.old_content !== item.text" class="diff-wrap">
                  <section class="diff-box">
                    <h5>编辑前</h5>
                    <p class="msg-text" :class="{ deleted: item.deleted }">{{ item.old_content }}</p>
                  </section>
                  <section class="diff-box">
                    <h5>编辑后</h5>
                    <p class="msg-text" :class="{ deleted: item.deleted }">{{ item.text || '[空消息]' }}</p>
                  </section>
                </div>
                <p v-else class="msg-text" :class="{ deleted: item.deleted }">{{ item.text || '[空消息]' }}</p>

                <div class="msg-tags">
                  <span v-if="item.edited" class="tag blue">已编辑</span>
                  <span v-if="item.deleted" class="tag red">已删除</span>
                </div>
              </article>
            </transition-group>
          </div>
        </div>
      </section>

      <section v-else class="mobile-layout">
        <div v-if="view === 'chats'" class="pane glass chats-pane mobile-pane">
          <div class="pane-head compact"><h3>DMs</h3></div>
          <div class="chat-list">
            <button
              v-for="(chat, index) in orderedChats"
              :key="chat.chat_id"
              class="chat-item"
              :class="{ active: selectedChatId === chat.chat_id }"
              :style="{ '--stagger': `${Math.min(index, 10) * 26}ms` }"
              @click="openChat(chat.chat_id)"
            >
              <div class="chat-main">
                <div class="avatar mini"><span>{{ chatDisplayName(chat).slice(0, 1) }}</span></div>
                <div class="chat-text">
                  <div class="chat-title">{{ chatDisplayName(chat) }}</div>
                  <div class="chat-meta-row">
                    <span class="chat-preview">{{ chatDisplayUsername(chat) }}</span>
                    <span class="chat-peer">peer {{ chat.peer_id || chat.chat_id }}</span>
                  </div>
                  <div class="chat-setting-row">
                    <span class="setting-chip" :class="{ active: !!chat.policy?.record }">R {{ chat.policy?.record ? 'on' : 'off' }}</span>
                    <span class="setting-chip" :class="{ active: !!chat.policy?.notify }">N {{ chat.policy?.notify ? 'on' : 'off' }}</span>
                    <span class="setting-chip override">{{ chat.override ? 'override' : 'global' }}</span>
                  </div>
                </div>
              </div>
            </button>
          </div>
        </div>

        <div v-if="view === 'messages'" class="pane glass messages-pane mobile-pane">
          <div class="pane-head compact"><h3>Msgs {{ selectedChat ? chatHeaderIdentity(selectedChat) : '' }}</h3></div>
          <div class="sub-actions">
            <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="showOpsMenu = !showOpsMenu">聊天设置</button>
          </div>
          <transition name="chat">
            <div v-if="showOpsMenu" class="ops-menu">
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="exportChat">导出聊天记录 (Bot TXT)</button>
              <button class="btn btn-danger" :disabled="!selectedChatId || saving" @click="clearChat">删除聊天记录</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="togglePinCurrent">{{ isPinned(selectedChatId) ? '取消置顶' : '置顶聊天记录' }}</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || saving" @click="selectedChatId && loadLatestMessages(selectedChatId)">加载最新消息</button>
              <button class="btn btn-soft" :disabled="!selectedChatId || !nextCursor || saving" @click="loadOlderMessages">加载更旧消息</button>
              <label class="ops-switch"><input type="checkbox" v-model="overridePolicy.record" :disabled="!selectedChatId || saving" /> record</label>
              <label class="ops-switch"><input type="checkbox" v-model="overridePolicy.notify" :disabled="!selectedChatId || saving" /> notify</label>
              <button class="btn btn-primary me-save-btn" :disabled="!selectedChatId || saving" @click="saveOverridePolicy">保存聊天设置</button>
            </div>
          </transition>
          <div class="messages-stream">
            <article
              v-for="item in messages"
              :key="item.id"
              class="message-bubble"
              :class="{ deleted: item.deleted, self: isOwnMessage(item) }"
            >
              <div class="msg-head">
                <span>#{{ item.message_id }} · {{ senderLabel(item) }}</span>
                <span class="time">
                  {{
                    item.deleted
                      ? '删除于 ' + formatTime(item.deleted_at || item.updated_at)
                      : item.edited
                        ? '编辑于 ' + formatTime(item.updated_at)
                        : '发送于 ' + formatTime(item.created_at)
                  }}
                </span>
              </div>
              <div v-if="item.old_content && item.old_content !== item.text" class="diff-wrap">
                <section class="diff-box">
                  <h5>编辑前</h5>
                  <p class="msg-text" :class="{ deleted: item.deleted }">{{ item.old_content }}</p>
                </section>
                <section class="diff-box">
                  <h5>编辑后</h5>
                  <p class="msg-text" :class="{ deleted: item.deleted }">{{ item.text || '[空消息]' }}</p>
                </section>
              </div>
              <p v-else class="msg-text" :class="{ deleted: item.deleted }">{{ item.text || '[空消息]' }}</p>
              <div class="msg-tags">
                <span v-if="item.edited" class="tag blue">已编辑</span>
                <span v-if="item.deleted" class="tag red">已删除</span>
              </div>
            </article>
          </div>
        </div>

        <div v-if="view === 'settings'" class="pane glass settings-pane mobile-pane">
          <div class="pane-head compact"><h3>Me</h3></div>
          <div class="me-profile-card">
            <img v-if="tgUser?.photo_url" :src="tgUser.photo_url" class="avatar me-avatar" alt="me-avatar" />
            <div v-else class="avatar me-avatar">{{ (tgUser?.first_name || tgUser?.username || 'U').slice(0, 1) }}</div>
            <div class="me-profile-text">
              <div class="me-profile-name">{{ tgUser?.first_name || tgUser?.username || 'Telegram User' }}</div>
              <div class="me-profile-meta">
                <span>{{ tgUser?.username ? '@' + tgUser.username : 'no-username' }}</span>
                <span>ID {{ ownerId || '-' }}</span>
              </div>
            </div>
            <span class="pill" :class="status">{{ status }}</span>
          </div>
          <div class="policy-box me-status-card">
            <h4>我的状态</h4>
            <div class="policy-grid">
              <div class="muted">用户: {{ tgUser?.username ? '@' + tgUser.username : tgUser?.first_name || 'unknown' }}</div>
              <div class="muted">连接: {{ status }}</div>
              <div class="muted">Owner ID: {{ ownerId || '-' }}</div>
              <div class="muted">Session: {{ sessionExpiresAt ? formatTime(sessionExpiresAt) : '未鉴权' }}</div>
            </div>
          </div>
          <div class="policy-box me-policy-card">
            <h4>全局默认设置</h4>
            <div class="policy-grid">
              <label><input type="checkbox" v-model="globalPolicy.record" /> record</label>
              <label><input type="checkbox" v-model="globalPolicy.notify" /> notify</label>
              <label class="field-inline">max_messages <input type="number" min="100" max="200000" step="100" v-model.number="globalPolicy.max_messages" /></label>
              <button class="btn btn-primary me-save-btn" :disabled="saving" @click="saveGlobalPolicy">保存全局默认</button>
            </div>
          </div>
        </div>

        <nav class="mobile-tabs glass">
          <button class="tab" :class="{ active: view === 'chats' }" @click="view = 'chats'">DMs</button>
          <button class="tab" :class="{ active: view === 'messages' }" @click="view = 'messages'">Msgs</button>
          <button class="tab" :class="{ active: view === 'settings' }" @click="view = 'settings'">Me</button>
        </nav>
      </section>

      <nav v-if="isDesktop" class="desktop-tabs glass">
        <button class="tab" :class="{ active: view === 'chats' }" @click="view = 'chats'">DMs</button>
        <button class="tab" :class="{ active: view === 'messages' }" @click="view = 'messages'">Msgs</button>
        <button class="tab" :class="{ active: view === 'settings' }" @click="view = 'settings'">Me</button>
      </nav>
    </main>
  </div>
</template>
