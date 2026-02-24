import { Badge, Button, Card, Group, Stack, Text } from '@mantine/core';
import { AnimatePresence } from 'framer-motion';
import MessageBubble from './MessageBubble';
import PolicyEditor from './PolicyEditor';
import type { ChatPolicy, MessageItem } from '../types/app';

interface MessagesPaneProps {
  chatId: number | null;
  messages: MessageItem[];
  hasMore: boolean;
  loading: boolean;
  saveFlash: boolean;
  overridePolicy: ChatPolicy;
  reducedMotion: boolean;
  onLoadLatest: () => void;
  onLoadOlder: () => void;
  onExportChat: () => void;
  onClearChat: () => void;
  onOverrideChange: (next: ChatPolicy) => void;
  onSaveOverride: () => void;
  onClearOverride: () => void;
}

export default function MessagesPane({
  chatId,
  messages,
  hasMore,
  loading,
  saveFlash,
  overridePolicy,
  reducedMotion,
  onLoadLatest,
  onLoadOlder,
  onExportChat,
  onClearChat,
  onOverrideChange,
  onSaveOverride,
  onClearOverride
}: MessagesPaneProps) {
  return (
    <Card className="glass pane messages-pane" p="md">
      <Stack gap="sm" h="100%">
        <Group justify="space-between" wrap="nowrap">
          <Stack gap={0}>
            <Text fw={700}>Messages {chatId ? `#${chatId}` : ''}</Text>
            <Text size="xs" c="dimmed">
              编辑/删除渲染已启用
            </Text>
          </Stack>
          <Group gap={6}>
            <Badge color="blue" radius="xl">
              Polling
            </Badge>
            <Button size="xs" variant="light" disabled={!chatId || loading} onClick={onExportChat}>
              导出
            </Button>
            <Button size="xs" color="red" disabled={!chatId || loading} onClick={onClearChat}>
              清空
            </Button>
          </Group>
        </Group>

        <Group gap={8}>
          <Button size="xs" variant="subtle" disabled={!chatId || loading} onClick={onLoadLatest}>
            加载
          </Button>
          <Button size="xs" variant="subtle" disabled={!chatId || !hasMore || loading} onClick={onLoadOlder}>
            更多
          </Button>
          <Text size="xs" c={saveFlash ? 'green.4' : 'dimmed'}>
            {saveFlash ? '保存成功' : `共 ${messages.length} 条`}
          </Text>
        </Group>

        <div className="messages-stream scroll-area">
          {messages.length === 0 ? (
            <Stack align="center" justify="center" h="100%" gap={4} className="messages-empty">
              <Text size="sm" fw={600}>
                暂无消息
              </Text>
              <Text size="xs" c="dimmed">
                选择会话后可加载最新页，支持轮询与分页
              </Text>
            </Stack>
          ) : (
            <Stack gap="xs">
              <AnimatePresence initial={false}>
                {messages.map((item) => (
                  <MessageBubble key={item.id} item={item} reducedMotion={reducedMotion} />
                ))}
              </AnimatePresence>
            </Stack>
          )}
        </div>

        <PolicyEditor
          title="当前 Chat 覆盖设置"
          policy={overridePolicy}
          disabled={!chatId}
          loading={loading}
          onChange={onOverrideChange}
          onSave={onSaveOverride}
          onClear={onClearOverride}
          saveLabel="保存当前 Chat 覆盖"
        />
      </Stack>
    </Card>
  );
}
