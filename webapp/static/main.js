import { loadMe } from "./api.js";
import { initializeTelegram, telegramInitData } from "./telegram.js";

const elements = {
  loading: document.querySelector("#loading"),
  error: document.querySelector("#error"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry"),
  profile: document.querySelector("#profile"),
  fullName: document.querySelector("#full-name"),
  username: document.querySelector("#username"),
  premium: document.querySelector("#premium"),
  avatar: document.querySelector("#avatar"),
};

const telegram = initializeTelegram();

function show(element) {
  element.classList.remove("hidden");
}

function hide(element) {
  element.classList.add("hidden");
}

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "RC";
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function renderProfile(payload) {
  const user = payload.user;
  elements.fullName.textContent = user.full_name || user.first_name;
  elements.username.textContent = user.username ? `@${user.username}` : "Без username";
  elements.avatar.textContent = initials(user.full_name || user.first_name);
  elements.premium.classList.toggle("hidden", !user.is_premium);

  hide(elements.loading);
  hide(elements.error);
  show(elements.profile);
}

function renderError(message) {
  hide(elements.loading);
  hide(elements.profile);
  elements.errorMessage.textContent = message;
  show(elements.error);
}

async function boot() {
  hide(elements.error);
  hide(elements.profile);
  show(elements.loading);

  const initData = telegramInitData(telegram);
  if (!initData) {
    renderError("Откройте Mini App через Telegram. В обычном браузере Telegram initData отсутствует.");
    return;
  }

  try {
    const payload = await loadMe(initData);
    renderProfile(payload);
  } catch (error) {
    if (error.status === 401) {
      renderError("Telegram-сессия не прошла проверку. Закройте Mini App и откройте его снова из бота.");
      return;
    }
    renderError("Сервер временно недоступен. Повторите попытку.");
  }
}

elements.retry.addEventListener("click", boot);
boot();
