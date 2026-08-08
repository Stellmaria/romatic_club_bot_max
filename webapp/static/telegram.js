export function initializeTelegram() {
  const telegram = window.Telegram?.WebApp;
  if (!telegram) {
    return null;
  }

  telegram.ready();
  telegram.expand();
  return telegram;
}

export function telegramInitData(telegram) {
  return telegram?.initData?.trim() ?? "";
}
