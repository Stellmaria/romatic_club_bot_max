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

export function openTelegramLink(telegram, url) {
  if (!url) return;
  if (telegram?.openTelegramLink && url.startsWith("https://t.me/")) {
    telegram.openTelegramLink(url);
    return;
  }
  if (telegram?.openLink) {
    telegram.openLink(url);
    return;
  }
  window.location.assign(url);
}
