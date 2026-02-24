import { Card, Group, Stack, Text } from '@mantine/core';
import type { ChatPolicy } from '../types/app';
import PolicyEditor from './PolicyEditor';

interface SettingsPaneProps {
  globalPolicy: ChatPolicy;
  saveFlash: boolean;
  loading: boolean;
  onChange: (next: ChatPolicy) => void;
  onSave: () => void;
}

export default function SettingsPane({
  globalPolicy,
  saveFlash,
  loading,
  onChange,
  onSave
}: SettingsPaneProps) {
  return (
    <Card className="glass pane settings-pane" p="md">
      <Stack gap="md" className="scroll-area">
        <Group justify="space-between">
          <Text fw={700} size="lg">
            Global Defaults
          </Text>
          <Text size="sm" c={saveFlash ? 'green.4' : 'dimmed'}>
            {saveFlash ? '已保存' : '保存后广播 settings.updated'}
          </Text>
        </Group>
        <PolicyEditor
          title="全局默认策略"
          policy={globalPolicy}
          loading={loading}
          onChange={onChange}
          onSave={onSave}
          saveLabel="保存全局默认"
        />
      </Stack>
    </Card>
  );
}
