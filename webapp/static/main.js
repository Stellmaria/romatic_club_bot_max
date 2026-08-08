import {
  loadAuctionHome,
  loadCardImage,
  loadFreeSlots,
  loadMe,
} from "./api.js";
import {
  closeTelegramApp,
  initializeTelegram,
  openTelegramLink,
  telegramColorScheme,
  telegramInitData,
} from "./telegram.js";

const THEME_STORAGE_KEY = "card-house-theme";
const VIEW_NAMES = ["auction", "my-lots", "submit", "subscriptions", "profile"];

const elements = {
  loading: document.querySelector("#loading"),
  loadingTitle: document.querySelector("#loading-title"),
  loadingMessage: document.querySelector("#loading-message"),
  error: document.querySelector("#error"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry"),
  content: document.querySelector("#content"),
  avatar: document.querySelector("#avatar"),
  userName: document.querySelector("#user-name"),
  profileShortcut: document.querySelector("#profile-shortcut"),
  notificationsButton: document.querySelector("#notifications-button"),
  themeToggle: document.querySelector("#theme-toggle"),
  themeIcon: document.querySelector("#theme-icon"),
  profileThemeToggle: document.querySelector("#profile-theme-toggle"),
  profileAvatar: document.querySelector("#profile-avatar"),
  profileTitle: document.querySelector("#profile-title"),
  profileUsername: document.querySelector("#profile-username"),
  profileLuxury: document.querySelector("#profile-luxury"),
  luxuryStatus: document.querySelector("#luxury-status"),
  dayTitle: document.querySelector("#day-title"),
  dayDate: document.querySelector("#day-date"),
  freeSlots: document.querySelector("#free-slots"),
  freeSlotsPanel: document.querySelector("#free-slots-panel"),
  freeSlotsClose: document.querySelector("#free-slots-close"),
  freeSlotsDate: document.querySelector("#free-slots-date"),
  freeSlotsList: document.querySelector("#free-slots-list"),
  todayTab: document.querySelector("#today-tab"),
  calendarTab: document.querySelector("#calendar-tab"),
  calendarLock: document.querySelector("#calendar-lock"),
  calendarPanel: document.querySelector("#calendar-panel"),
  calendarDate: document.querySelector("#calendar-date"),
  calendarLoad: document.querySelector("#calendar-load"),
  activeAuction: document.querySelector("#active-auction"),
  noActive: document.querySelector("#no-active"),
  activeImage: document.querySelector("#active-image"),
  activeImagePlaceholder: document.querySelector("#active-image-placeholder"),
  activeLotNumber: document.querySelector("#active-lot-number"),
  activeName: document.querySelector("#active-name"),
  activeRarity: document.querySelector("#active-rarity"),
  activeSeller: document.querySelector("#active-seller"),
  activePrice: document.querySelector("#active-price"),
  activeStartPrice: document.querySelector("#active-start-price"),
  activeEndTime: document.querySelector("#active-end-time"),
  activeCountdown: document.querySelector("#active-countdown"),
  activeDeck: document.querySelector("#active-deck"),
  activeCardMeta: document.querySelector("#active-card-meta"),
  activeStory: document.querySelector("#active-story"),
  openTelegram: document.querySelector("#open-telegram"),
  upcomingTitle: document.querySelector("#upcoming-title"),
  upcomingCount: document.querySelector("#upcoming-count"),
  upcomingList: document.querySelector("#upcoming-list"),
  upcomingEmpty: document.querySelector("#upcoming-empty"),
  rulesButton: document.querySelector("#rules-button"),
  rulesPanel: document.querySelector("#rules-panel"),
  navItems: Array.from(document.querySelectorAll(".nav-item[data-view]")),
  returnButtons: Array.from(document.querySelectorAll(".telegram-return")),
  views: Object.fromEntries(
    VIEW_NAMES.map((name) => [name, document.querySelector(`#${name}-view`)]),
  ),
  toast: document.querySelector("#toast"),
};

const telegram = initializeTelegram();
const imageCache = new Map();
let initData = "";
let profile = null;
let currentHome = null;
let selectedDate = "";
let currentView = "auction";
let pollTimer = null;
let countdownTimer = null;
let pollBusy = false;
let toastTimer = null;

function show(element) {
  element?.classList.remove("hidden");
}

function hide(element) {
  element?.classList.add("hidden");
}

function storedTheme() {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "";
  } catch {
    return "";
  }
}

function saveTheme(value) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, value);
  } catch {
    // Storage may be unavailable in restricted WebViews; the current session still works.
  }
}

function syncTelegramChrome(theme) {
  const background = theme === "light" ? "#f5f0e7" : "#08131f";
  const header = theme === "light" ? "#fbf7f0" : "#0b1724";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", background);
  try {
    telegram?.setBackgroundColor?.(background);
    telegram?.setHeaderColor?.(header);
  } catch {
    // Older Telegram clients can reject custom colors; CSS remains authoritative.
  }
}

function applyTheme(theme, { persist = false } = {}) {
  const normalized = theme === "light" ? "light" : "dark";
  document.documentElement.dataset.theme = normalized;
  elements.themeIcon.textContent = normalized === "dark" ? "☀" : "☾";
  elements.themeToggle.setAttribute(
    "aria-label",
    normalized === "dark" ? "Включить светлую тему" : "Включить тёмную тему",
  );
  elements.profileThemeToggle.textContent =
    normalized === "dark" ? "Светлая тема" : "Тёмная тема";
  syncTelegramChrome(normalized);
  if (persist) saveTheme(normalized);
}

function toggleTheme() {
  const current = document.documentElement.dataset.theme;
  applyTheme(current === "dark" ? "light" : "dark", { persist: true });
}

function setLoading(title, message) {
  elements.loadingTitle.textContent = title;
  elements.loadingMessage.textContent = message;
  hide(elements.error);
  hide(elements.content);
  show(elements.loading);
}

function renderError(message) {
  hide(elements.loading);
  hide(elements.content);
  elements.errorMessage.textContent = message;
  show(elements.error);
}

function showToast(message) {
  window.clearTimeout(toastTimer);
  elements.toast.textContent = message;
  show(elements.toast);
  toastTimer = window.setTimeout(() => hide(elements.toast), 2600);
}

function setView(name) {
  if (!VIEW_NAMES.includes(name)) return;
  currentView = name;
  for (const [viewName, view] of Object.entries(elements.views)) {
    view.classList.toggle("hidden", viewName !== name);
  }
  for (const item of elements.navItems) {
    const active = item.dataset.view === name;
    item.classList.toggle("active", active);
    if (active) {
      item.setAttribute("aria-current", "page");
    } else {
      item.removeAttribute("aria-current");
    }
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function initials(name) {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "КД";
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function profilePhoto(imageUrl, name, target) {
  if (!imageUrl) {
    target.textContent = initials(name);
    return;
  }
  const image = document.createElement("img");
  image.src = imageUrl;
  image.alt = "";
  image.referrerPolicy = "no-referrer";
  image.addEventListener("error", () => {
    target.replaceChildren(document.createTextNode(initials(name)));
  });
  target.replaceChildren(image);
}

function renderProfile(payload) {
  profile = payload.user;
  const name = profile.full_name || profile.first_name || "Карточный домик";
  elements.userName.textContent = name;
  elements.profileTitle.textContent = name;
  elements.profileUsername.textContent = profile.username
    ? `@${String(profile.username).replace(/^@/, "")}`
    : "Telegram Mini App";
  profilePhoto(profile.photo_url, name, elements.avatar);
  profilePhoto(profile.photo_url, name, elements.profileAvatar);
}

function formatDay(isoDate) {
  const date = new Date(`${isoDate}T12:00:00+03:00`);
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    timeZone: "Europe/Moscow",
  }).format(date);
}

function formatWeekday(isoDate) {
  const date = new Date(`${isoDate}T12:00:00+03:00`);
  const value = new Intl.DateTimeFormat("ru-RU", {
    weekday: "long",
    timeZone: "Europe/Moscow",
  }).format(date);
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Moscow",
  }).format(date);
}

function currencyIcon(currency) {
  const normalized = String(currency || "").trim().toLowerCase();
  return {
    "алмазы": "💎",
    "diamond": "💎",
    "diamonds": "💎",
    "чашки": "🍵",
    "чай": "🍵",
    "tea": "🍵",
    "сокровища": "🪙",
    "treasures": "🪙",
  }[normalized] || "";
}

function rarityLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return "Стандартный";
  const normalized = raw.toLowerCase();
  const labels = {
    bronze: "Бронзовая",
    silver: "Серебряная",
    gold: "Золотая",
    diamond: "Алмазная",
    standard: "Стандартный",
  };
  return labels[normalized] || raw.charAt(0).toUpperCase() + raw.slice(1);
}

function rewardIcon(type) {
  const normalized = String(type || "").toLowerCase();
  if (normalized.includes("tea") || normalized.includes("cup")) return "🍵";
  if (normalized.includes("diamond")) return "💎";
  return currencyIcon(type);
}

function viewerHasLuxury() {
  return Number(currentHome?.viewer?.luxury_level || 0) > 0;
}

function purchaseLuxury() {
  const url = currentHome?.viewer?.luxury_contact_url;
  if (!url) {
    showToast("Контакт для Luxury временно недоступен.");
    return;
  }
  openTelegramLink(telegram, url);
}

function renderViewer(viewer) {
  const level = Number(viewer?.luxury_level || 0);
  if (level > 0) {
    elements.luxuryStatus.textContent = `♛ Luxury ${level}`;
    elements.luxuryStatus.classList.add("owned");
    elements.profileLuxury.textContent = `Luxury ${level}`;
  } else {
    elements.luxuryStatus.textContent = "Купить Luxury";
    elements.luxuryStatus.classList.remove("owned");
    elements.profileLuxury.textContent = "Нет активного Luxury";
  }

  const canCalendar = Boolean(viewer?.can_use_calendar);
  elements.calendarLock.classList.toggle("hidden", canCalendar);
  elements.calendarTab.classList.toggle("locked", !canCalendar);
  elements.freeSlots.classList.toggle("locked", !viewer?.can_use_free_slots);
}

function renderDay(home) {
  elements.dayTitle.textContent = home.is_today ? "Сегодня" : formatWeekday(home.date);
  elements.dayDate.textContent = formatDay(home.date);
  elements.upcomingTitle.textContent = home.is_today ? "Дальше сегодня" : "Аукционы";
  elements.todayTab.classList.toggle("active", home.is_today);
  elements.calendarTab.classList.toggle("active", !home.is_today);
  elements.calendarDate.value = home.date;
}

function updateCountdown(endTime) {
  window.clearInterval(countdownTimer);
  const tick = () => {
    if (!endTime) {
      elements.activeCountdown.textContent = "";
      return;
    }
    const remaining = new Date(endTime).getTime() - Date.now();
    if (remaining <= 0) {
      elements.activeCountdown.textContent = "завершается";
      return;
    }
    const totalSeconds = Math.floor(remaining / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    elements.activeCountdown.textContent =
      `осталось ${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  };
  tick();
  countdownTimer = window.setInterval(tick, 1000);
}

async function imageUrl(path) {
  if (!path) return "";
  if (imageCache.has(path)) return imageCache.get(path);
  const blob = await loadCardImage(initData, path);
  const url = URL.createObjectURL(blob);
  imageCache.set(path, url);
  return url;
}

async function setImage(image, placeholder, card) {
  image.classList.add("hidden");
  placeholder.classList.remove("hidden");
  const path = card?.image_url;
  if (!path) return;

  try {
    image.src = await imageUrl(path);
    image.alt = [card.hero_name, card.name].filter(Boolean).join(" — ");
    image.classList.remove("hidden");
    placeholder.classList.add("hidden");
  } catch {
    image.removeAttribute("src");
  }
}

function renderActive(auction) {
  if (!auction) {
    hide(elements.activeAuction);
    show(elements.noActive);
    updateCountdown(null);
    return;
  }

  show(elements.activeAuction);
  hide(elements.noActive);

  const card = auction.card || {};
  const currency = currencyIcon(auction.currency);
  const seller = auction.seller;
  elements.activeLotNumber.textContent = `Лот #${auction.id}`;
  elements.activeName.textContent =
    [card.hero_name, card.name].filter(Boolean).join(" — ") || "Карточный лот";
  elements.activeRarity.textContent = `★ ${rarityLabel(card.rarity)}`;
  elements.activeSeller.textContent = seller?.verified
    ? "Продавец: скрыт · подтверждён"
    : "Продавец: скрыт · не верифицирован";
  elements.activeSeller.classList.toggle("unverified", !seller?.verified);

  elements.activePrice.textContent = `${auction.display_price} ${currency}`.trim();
  elements.activeStartPrice.textContent = `Старт: ${auction.start_price} ${currency}`.trim();
  elements.activeEndTime.textContent = `До ${formatTime(auction.end_time)} МСК`;
  updateCountdown(auction.end_time);

  const deckParts = [];
  if (card.deck_id) deckParts.push(`Колода №${card.deck_id}`);
  if (card.deck_name) deckParts.push(card.deck_name);
  elements.activeDeck.textContent = deckParts.join(" · ");

  elements.activeCardMeta.replaceChildren();
  if (card.obtain_amount > 0) {
    const reward = document.createElement("span");
    reward.textContent = `Подарок +${card.obtain_amount} ${rewardIcon(card.obtain_type)}`.trim();
    elements.activeCardMeta.append(reward);
  }
  if (card.num) {
    const number = document.createElement("span");
    number.textContent = `Карта №${card.num}`;
    elements.activeCardMeta.append(number);
  }

  elements.activeStory.textContent = card.story ? `▤ ${card.story}` : "";
  elements.activeStory.classList.toggle("hidden", !card.story);

  const canOpen = Boolean(auction.telegram_url);
  elements.openTelegram.disabled = !canOpen;
  elements.openTelegram.dataset.url = auction.telegram_url || "";
  elements.openTelegram.title = canOpen ? "" : "Лот ещё не опубликован в Telegram";

  setImage(elements.activeImage, elements.activeImagePlaceholder, card);
}

function upcomingRow(auction) {
  const card = auction.card || {};
  const row = document.createElement("button");
  row.className = "upcoming-row";
  row.type = "button";
  row.disabled = !auction.telegram_url;

  const media = document.createElement("span");
  media.className = "upcoming-media";
  const image = document.createElement("img");
  image.alt = "";
  image.className = "hidden";
  const placeholder = document.createElement("span");
  placeholder.className = "mini-placeholder";
  placeholder.textContent = "КД";
  media.append(image, placeholder);
  setImage(image, placeholder, card);

  const copy = document.createElement("span");
  copy.className = "upcoming-copy";
  const title = document.createElement("strong");
  title.textContent =
    `${formatTime(auction.start_time)} · ${[card.hero_name, card.name].filter(Boolean).join(" — ") || "Карточный лот"}`;
  const detail = document.createElement("small");
  detail.textContent = rarityLabel(card.rarity);
  copy.append(title, detail);

  const price = document.createElement("span");
  price.className = "upcoming-price";
  const priceValue = document.createElement("strong");
  priceValue.textContent = `${auction.display_price} ${currencyIcon(auction.currency)}`.trim();
  const priceRarity = document.createElement("small");
  priceRarity.textContent = rarityLabel(card.rarity);
  price.append(priceValue, priceRarity);

  const chevron = document.createElement("span");
  chevron.className = "chevron";
  chevron.textContent = "›";

  row.append(media, copy, price, chevron);
  if (auction.telegram_url) {
    row.addEventListener("click", () => openTelegramLink(telegram, auction.telegram_url));
  }
  return row;
}

function renderUpcoming(auctions) {
  elements.upcomingList.replaceChildren();
  const list = Array.isArray(auctions) ? auctions : [];
  elements.upcomingCount.textContent = list.length ? `${list.length} ближайших` : "";
  elements.upcomingEmpty.classList.toggle("hidden", list.length > 0);

  for (const auction of list) {
    elements.upcomingList.append(upcomingRow(auction));
  }
}

function renderHome(home) {
  currentHome = home;
  renderViewer(home.viewer);
  renderDay(home);
  renderActive(home.active);
  renderUpcoming(home.upcoming);

  hide(elements.loading);
  hide(elements.error);
  show(elements.content);
  setView(currentView);
  schedulePolling();
}

function schedulePolling() {
  window.clearInterval(pollTimer);
  if (!currentHome?.is_today) return;
  pollTimer = window.setInterval(() => refreshHome({ silent: true }), 15000);
}

function homeErrorMessage(error) {
  if (error.status === 401) {
    return "Telegram-сессия устарела. Закройте Mini App и откройте его снова.";
  }
  if (error.status === 403 && error.code === "luxury_required") {
    return "Календарь доступен пользователям Luxury.";
  }
  if (error.code === "request_timeout") {
    return "Сервер отвечает слишком долго. Проверьте соединение и повторите попытку.";
  }
  return "Сервер временно недоступен. Повторите попытку.";
}

async function refreshHome({ silent = false } = {}) {
  if (pollBusy) return;
  pollBusy = true;
  if (!silent) {
    setLoading("Загружаем аукционы", "Получаем актуальное расписание и ставки.");
  }

  try {
    renderHome(await loadAuctionHome(initData, selectedDate));
  } catch (error) {
    if (silent) {
      showToast("Не удалось обновить ставку. Повторим автоматически.");
    } else {
      renderError(homeErrorMessage(error));
    }
  } finally {
    pollBusy = false;
  }
}

async function showFreeSlots() {
  if (!viewerHasLuxury()) {
    showToast("Свободные слоты доступны с Luxury.");
    purchaseLuxury();
    return;
  }

  elements.freeSlotsList.textContent = "Загружаем…";
  elements.freeSlotsDate.textContent = formatDay(currentHome.date);
  show(elements.freeSlotsPanel);

  try {
    const payload = await loadFreeSlots(initData, currentHome.date);
    elements.freeSlotsList.replaceChildren();
    if (!payload.slots.length) {
      elements.freeSlotsList.textContent = "Свободных слотов на эту дату нет.";
      return;
    }
    for (const slot of payload.slots) {
      const chip = document.createElement("span");
      chip.className = "slot-chip";
      chip.textContent = slot;
      elements.freeSlotsList.append(chip);
    }
  } catch (error) {
    elements.freeSlotsList.textContent =
      error.status === 403 ? "Нужен Luxury-доступ." : "Не удалось загрузить слоты.";
  }
}

function openCalendar() {
  if (!viewerHasLuxury()) {
    showToast("Календарь доступен с Luxury.");
    purchaseLuxury();
    return;
  }
  elements.calendarDate.min = todayIso();
  elements.calendarDate.value = currentHome.date;
  show(elements.calendarPanel);
}

function todayIso() {
  const formatter = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Europe/Moscow",
  });
  return formatter.format(new Date());
}

async function loadSelectedCalendarDay() {
  const value = elements.calendarDate.value;
  if (!value) return;
  selectedDate = value === todayIso() ? "" : value;
  hide(elements.calendarPanel);
  hide(elements.freeSlotsPanel);
  await refreshHome();
}

async function loadToday() {
  selectedDate = "";
  hide(elements.calendarPanel);
  hide(elements.freeSlotsPanel);
  await refreshHome();
}

async function boot() {
  window.clearInterval(pollTimer);
  window.clearInterval(countdownTimer);
  hide(elements.error);
  hide(elements.content);
  currentView = "auction";
  setLoading("Открываем аукционы", "Проверяем Telegram-сессию и актуальные лоты.");

  initData = telegramInitData(telegram);
  if (!initData) {
    renderError(
      "Откройте Mini App через Telegram. В обычном браузере Telegram initData отсутствует.",
    );
    return;
  }

  try {
    const [me, home] = await Promise.all([loadMe(initData), loadAuctionHome(initData)]);
    renderProfile(me);
    selectedDate = "";
    renderHome(home);
  } catch (error) {
    renderError(homeErrorMessage(error));
  }
}

const initialTheme = storedTheme() || telegramColorScheme(telegram);
applyTheme(initialTheme);
if (!storedTheme()) {
  telegram?.onEvent?.("themeChanged", () => {
    if (!storedTheme()) applyTheme(telegramColorScheme(telegram));
  });
}

elements.retry.addEventListener("click", boot);
elements.themeToggle.addEventListener("click", toggleTheme);
elements.profileThemeToggle.addEventListener("click", toggleTheme);
elements.profileShortcut.addEventListener("click", () => setView("profile"));
elements.notificationsButton.addEventListener("click", () => setView("subscriptions"));
elements.luxuryStatus.addEventListener("click", () => {
  if (viewerHasLuxury()) {
    showToast(elements.luxuryStatus.textContent);
    return;
  }
  purchaseLuxury();
});
elements.freeSlots.addEventListener("click", showFreeSlots);
elements.freeSlotsClose.addEventListener("click", () => hide(elements.freeSlotsPanel));
elements.todayTab.addEventListener("click", loadToday);
elements.calendarTab.addEventListener("click", openCalendar);
elements.calendarLoad.addEventListener("click", loadSelectedCalendarDay);
elements.openTelegram.addEventListener("click", () => {
  openTelegramLink(telegram, elements.openTelegram.dataset.url || "");
});
elements.rulesButton.addEventListener("click", () => {
  elements.rulesPanel.classList.toggle("hidden");
});
for (const item of elements.navItems) {
  item.addEventListener("click", () => setView(item.dataset.view || "auction"));
}
for (const button of elements.returnButtons) {
  button.addEventListener("click", () => closeTelegramApp(telegram));
}

window.addEventListener("beforeunload", () => {
  window.clearInterval(pollTimer);
  window.clearInterval(countdownTimer);
  for (const url of imageCache.values()) URL.revokeObjectURL(url);
});

boot();
