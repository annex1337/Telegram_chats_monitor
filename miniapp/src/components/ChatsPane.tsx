import { Avatar, Badge, Button, Card, Group, Stack, Text, TextInput } from '@mantine/core';
import { motion } from 'framer-motion';
import { formatRelative } from '../lib/time';
import type { ChatSummary, TelegramUser } from '../types/app';

interface ChatsPaneProps {
  user: TelegramUser | null;
  chats: ChatSummary[];
  selectedChatId: number | null;
  search: string;
  reducedMotion: boolean;
  onSearchChange: (value: string) => void;
  onRefresh: () => void;
  onSelectChat: (chatId: number) => void;
}

export default function ChatsPane({
  user,
  chats,
  selectedChatId,
  search,
  reducedMotion,
  onSearchChange,
  onRefresh,
  onSelectChat
}: ChatsPaneProps) {
  return (
    <Card className="glass pane chats-pane" p="md">
      <Stack gap="sm" className="scroll-area">
        <Stack gap={2}>
          <Text fw={700}>Chats</Text>
          <Text size="xs" c="dimmed">
            用户 {user?.username || user?.first_name || '-'} · 共 {chats.length} 个会话
          </Text>
        </Stack>

        <Group wrap="nowrap" align="end">
          <TextInput
            className="chats-search"
            placeholder="搜索 name / username / title / chat_id"
            value={search}
            onChange={(event) => onSearchChange(event.currentTarget.value)}
          />
          <Button variant="light" onClick={onRefresh}>
            刷新
          </Button>
        </Group>

        <Stack gap="xs">
          {chats.map((chat, index) => {
            const active = selectedChatId === chat.chat_id;
            const title = chat.title || chat.name || chat.username || `Chat ${chat.chat_id}`;
            const preview = chat.username
              ? `@${chat.username}`
              : chat.deleted_count > 0
                ? `删除消息 ${chat.deleted_count} 条`
                : '暂无预览';
            return (
              <motion.div
                key={chat.chat_id}
                initial={reducedMotion ? false : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reducedMotion ? 0 : 0.15, delay: reducedMotion ? 0 : index * 0.02 }}
              >
                <Group
                  className={`chat-item ${active ? 'active' : ''}`}
                  p="sm"
                  justify="space-between"
                  onClick={() => onSelectChat(chat.chat_id)}
                  style={{ cursor: 'pointer' }}
                >
                  <Group wrap="nowrap" gap="sm">
                    <Avatar src={chat.photo_url || null} radius="xl" size={36}>
                      {title.slice(0, 1).toUpperCase()}
                    </Avatar>
                    <Stack gap={1}>
                      <Text size="sm" fw={600} lineClamp={1}>
                        {title}
                      </Text>
                      <Text size="xs" c="dimmed" lineClamp={1} className="chat-preview">
                        {preview}
                      </Text>
                      <Text size="xs" c="dimmed">
                        #{chat.chat_id} · {chat.message_count} 条 · {formatRelative(chat.last_activity)}
                      </Text>
                    </Stack>
                  </Group>
                  <Stack gap={4} align="flex-end">
                    {chat.strategy_flag ? (
                      <Badge radius="xl" variant="light" color="blue">
                        {chat.strategy_flag}
                      </Badge>
                    ) : null}
                    {chat.deleted_count > 0 ? (
                      <Badge radius="xl" variant="light" color="red">
                        删 {chat.deleted_count}
                      </Badge>
                    ) : null}
                  </Stack>
                </Group>
              </motion.div>
            );
          })}
        </Stack>
      </Stack>
    </Card>
  );
}
