/// <reference types="vite/client" />

interface TelegramUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  photo_url?: string;
}

interface TelegramWebApp {
  initData?: string;
  initDataUnsafe?: {
    user?: TelegramUser;
  };
  ready?: () => void;
  expand?: () => void;
}

declare global {
  interface Window {
    Telegram?: {
      WebApp?: TelegramWebApp;
    };
  }
}

export {};
