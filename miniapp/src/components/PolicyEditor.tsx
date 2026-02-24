import { Button, NumberInput, Stack, Switch, Text } from '@mantine/core';
import type { ChatPolicy } from '../types/app';

interface PolicyEditorProps {
  title: string;
  policy: ChatPolicy;
  disabled?: boolean;
  loading?: boolean;
  onChange: (next: ChatPolicy) => void;
  onSave: () => void;
  onClear?: () => void;
  saveLabel: string;
}

export default function PolicyEditor({
  title,
  policy,
  disabled,
  loading,
  onChange,
  onSave,
  onClear,
  saveLabel
}: PolicyEditorProps) {
  return (
    <Stack gap="sm">
      <Text fw={700}>{title}</Text>
      <Switch
        label="record"
        checked={policy.record}
        disabled={disabled}
        onChange={(event) => onChange({ ...policy, record: event.currentTarget.checked })}
      />
      <Switch
        label="notify"
        checked={policy.notify}
        disabled={disabled}
        onChange={(event) => onChange({ ...policy, notify: event.currentTarget.checked })}
      />
      <NumberInput
        label="max_messages"
        min={100}
        max={200000}
        step={100}
        disabled={disabled}
        value={policy.max_messages}
        onChange={(value) => onChange({ ...policy, max_messages: Number(value || 10000) })}
      />
      <Button loading={loading} disabled={disabled} onClick={onSave}>
        {saveLabel}
      </Button>
      {onClear ? (
        <Button loading={loading} disabled={disabled} variant="light" color="gray" onClick={onClear}>
          清除当前 Chat 覆盖
        </Button>
      ) : null}
    </Stack>
  );
}
