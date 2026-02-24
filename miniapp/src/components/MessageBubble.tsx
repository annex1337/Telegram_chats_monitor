import { Badge, Group, Stack, Text } from '@mantine/core';
import { motion } from 'framer-motion';
import { formatTime } from '../lib/time';
import type { MessageItem } from '../types/app';

interface MessageBubbleProps {
  item: MessageItem;
  reducedMotion: boolean;
}

export default function MessageBubble({ item, reducedMotion }: MessageBubbleProps) {
  const timeLabel = item.deleted
    ? `删除于 ${formatTime(item.deleted_at || item.updated_at)}`
    : item.edited
      ? `编辑于 ${formatTime(item.updated_at)}`
      : `发送于 ${formatTime(item.created_at)}`;

  const showDiff = Boolean(item.old_content && item.old_content !== item.text);

  return (
    <motion.div
      layout
      initial={reducedMotion ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reducedMotion ? 0 : 0.16 }}
    >
      <Stack className={`message-bubble ${item.deleted ? 'deleted' : ''}`} p="sm" gap={8}>
        <Group justify="space-between" align="center">
          <Text size="xs" c="dimmed">
            #{item.message_id}
          </Text>
          <Text size="xs" c="dimmed" className="message-time">
            {timeLabel}
          </Text>
        </Group>

        {showDiff ? (
          <Stack gap={6}>
            <Stack className="diff-box" gap={4}>
              <Text size="xs" c="dimmed">
                编辑前
              </Text>
              <Text className={item.deleted ? 'message-text deleted' : 'message-text'} size="sm">
                {item.old_content}
              </Text>
            </Stack>
            <Stack className="diff-box" gap={4}>
              <Text size="xs" c="dimmed">
                编辑后
              </Text>
              <Text className={item.deleted ? 'message-text deleted' : 'message-text'} size="sm">
                {item.deleted ? '[已删除]' : item.text || '[空消息]'}
              </Text>
            </Stack>
          </Stack>
        ) : (
          <Text className={item.deleted ? 'message-text deleted' : 'message-text'} size="sm">
            {item.deleted ? '[已删除]' : item.text || '[空消息]'}
          </Text>
        )}

        <Group gap={6}>
          {item.edited ? (
            <Badge radius="xl" color="blue" variant="light">
              已编辑
            </Badge>
          ) : null}
          {item.deleted ? (
            <Badge radius="xl" color="red" variant="light">
              已删除
            </Badge>
          ) : null}
        </Group>
      </Stack>
    </motion.div>
  );
}
