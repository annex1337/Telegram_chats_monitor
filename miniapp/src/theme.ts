import { createTheme } from '@mantine/core';

export const theme = createTheme({
  primaryColor: 'blue',
  primaryShade: 5,
  fontFamily:
    'PingFang SC, Noto Sans CJK SC, -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica Neue, Arial, sans-serif',
  headings: {
    fontFamily:
      'PingFang SC, Noto Sans CJK SC, -apple-system, BlinkMacSystemFont, Segoe UI, Helvetica Neue, Arial, sans-serif'
  },
  colors: {
    blue: ['#e8f2ff', '#cfe4ff', '#a3c8ff', '#75adff', '#4f93ff', '#3783ff', '#2f78ef', '#2065d2', '#1757b8', '#0c479a']
  },
  radius: {
    md: '12px',
    lg: '14px'
  },
  defaultGradient: {
    from: 'blue.5',
    to: 'cyan.4',
    deg: 110
  },
  defaultRadius: 'md'
});
