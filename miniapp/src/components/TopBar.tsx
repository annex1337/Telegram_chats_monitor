import { Avatar, Badge, Button, Card, Group, Stack, Text } from '@mantine/core';
import { IconSettings } from '@tabler/icons-react';
import type { ConnectionStatus, TelegramUser } from '../types/app';

interface TopBarProps {
  user: TelegramUser | null;
  ownerId: number | null;
  status: ConnectionStatus;
  sessionExpiresAt: number | null;
  onOpenSettings: () => void;
}

const statusColor: Record<ConnectionStatus, string> = {
  connected: 'green',
  connecting: 'yellow',
  disconnected: 'red'
};

export default function TopBar({
  user,
  ownerId,
  status,
  sessionExpiresAt,
  onOpenSettings
}: TopBarProps) {
  const initials = (user?.first_name || user?.username || 'U').slice(0, 1).toUpperCase();

  return (
    <Card className="glass topbar-card" p="sm">
      <Group justify="space-between" wrap="nowrap">
        <Group wrap="nowrap" gap="sm" className="topbar-left">
          <Avatar src={user?.photo_url || null} radius="xl" size={38}>
            {initials}
          </Avatar>
          <Stack gap={0}>
            <Text fw={700}>Telegram Bot Console</Text>
            <Text size="xs" c="dimmed">
              {user?.first_name || 'Owner'} · ID {ownerId ?? '-'}
            </Text>
          </Stack>
        </Group>

        <Group gap="sm" wrap="nowrap" className="topbar-right">
          <Badge radius="xl" color={statusColor[status]} variant="light">
            {status}
          </Badge>
          <Text size="xs" c="dimmed">
            {sessionExpiresAt
              ? `Session Exp: ${new Date(sessionExpiresAt * 1000).toLocaleTimeString('zh-CN')}`
              : '未鉴权'}
          </Text>
          <Button
            variant="light"
            radius="md"
            onClick={onOpenSettings}
            leftSection={<IconSettings size={16} />}
          >
            Settings
          </Button>
        </Group>
      </Group>
    </Card>
  );
}
