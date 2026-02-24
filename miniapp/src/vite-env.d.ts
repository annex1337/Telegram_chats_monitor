/// <reference types="vite/client" />

declare global {
  interface Window {
    Telegram?: import('./types/app').TelegramGlobal;
  }
}

export {};
