import { loadAuctions, loadMe } from "./api.js";
import { initializeTelegram, telegramInitData } from "./telegram.js";

const elements = {
  loading: document.querySelector("#loading"),
  loadingTitle: document.querySelector("#loading-title"),
  loadingMessage: document.querySelector("#loading-message"),
  error: document.querySelector("#error"),
  errorMessage: document.querySelector("#error-message"),
  retry: document.querySelector("#retry"),
  pageTitle: document.querySelector("#page-title"),
  profile: document.querySelector("#profile"),
  auctions: document.querySelector("#auctions"),
  auctionList: document.querySelector("#auction-list"),
  auctionEmpty: document.querySelector("#auction-empty"),
  openAuctions: document.querySelector("#open-auctions"),
  fullName: document.querySelector("#full-name"),
  username: document.querySelector("#username"),
  premium: document.querySelector("#premium"),
  avatar: document.querySelector("#avatar"),
  navItems: [...document.querySelectorAll(".nav-item[data-view]")],
};

const telegram = initializeTelegram();
let initData = "";
let auctionsLoaded = false;

function show(element) {
  element.classList.remove("hidden");
}

function hide(element) {
  element.classList.add("hidden");
}

function setLoading(title, message) {
  elements.loadingTitle.textContent = title;
  elements.loadingMessage.textContent = message;
  hide(elements.error);
  show(elements.loading);
}

function initials(name) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "КД";
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
  showView("profile");
}

function renderError(message) {
  hide(elements.loading);
  hide(elements.profile);
  hide(elements.auctions);
  elements.errorMessage.textContent = message;
  show(elements.error);
}

function formatAuctionTime(value) {
  if (!value) return "Время уточняется";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Время уточняется";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "Europe/Moscow",
  }).format(date);
}

function formatCurrency(currency) {
  const normalized = String(currency || "").toLowerCase();
  return {
    tea: "☕",
    cups: "☕",
    cup: "☕",
    diamonds: "💎",
    diamond: "💎",
  }[normalized] || currency;
}

function auctionStatus(status) {
  return {
    active: "Идёт сейчас",
    publishing: "Публикуется",
    scheduled: "Запланирован",
  }[status] || status;
}

function renderAuctions(payload) {
  elements.auctionList.replaceChildren();
  const auctions = payload.auctions || [];
  elements.auctionEmpty.classList.toggle("hidden", auctions.length > 0);

  for (const auction of auctions) {
    const card = document.createElement("article");
    card.className = "panel auction-card";

    const heading = document.createElement("div");
    const name = document.createElement("h3");
    name.textContent = auction.card_name || "Карточный лот";
    const hero = document.createElement("p");
    hero.className = "muted";
    hero.textContent = auction.hero_name || "Герой не указан";
    heading.append(name, hero);

    const badge = document.createElement("span");
    badge.className = `status-badge status-${auction.status}`;
    badge.textContent = auctionStatus(auction.status);

    const meta = document.createElement("div");
    meta.className = "auction-meta";
    const price = document.createElement("strong");
    price.textContent = `${auction.start_price} ${formatCurrency(auction.currency)}`.trim();
    const time = document.createElement("span");
    time.textContent = formatAuctionTime(auction.start_time);
    meta.append(price, time);

    card.append(heading, badge, meta);
    elements.auctionList.append(card);
  }
  auctionsLoaded = true;
}

function showView(view) {
  hide(elements.loading);
  hide(elements.error);
  elements.pageTitle.textContent = view === "auctions" ? "Аукцион" : "Профиль";
  elements.profile.classList.toggle("hidden", view !== "profile");
  elements.auctions.classList.toggle("hidden", view !== "auctions");
  for (const item of elements.navItems) {
    const active = item.dataset.view === view;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  }
}

async function openAuctions() {
  showView("auctions");
  if (auctionsLoaded) return;
  elements.auctionEmpty.textContent = "Загружаем публичные лоты…";
  show(elements.auctionEmpty);
  try {
    renderAuctions(await loadAuctions(initData));
  } catch (error) {
    elements.auctionEmpty.textContent = error.status === 401
      ? "Telegram-сессия устарела. Закройте приложение и откройте его снова."
      : "Не удалось загрузить аукционы. Повторите открытие раздела.";
  }
}

async function boot() {
  hide(elements.error);
  hide(elements.profile);
  hide(elements.auctions);
  setLoading("Загружаем профиль", "Telegram проверяет, кто именно открыл приложение.");

  initData = telegramInitData(telegram);
  if (!initData) {
    renderError("Откройте Mini App через Telegram. В обычном браузере Telegram initData отсутствует.");
    return;
  }

  try {
    renderProfile(await loadMe(initData));
  } catch (error) {
    if (error.status === 401) {
      renderError("Telegram-сессия не прошла проверку. Закройте Mini App и откройте его снова из бота.");
      return;
    }
    renderError("Сервер временно недоступен. Повторите попытку.");
  }
}

elements.retry.addEventListener("click", boot);
elements.openAuctions.addEventListener("click", openAuctions);
for (const item of elements.navItems) {
  item.addEventListener("click", () => {
    if (item.dataset.view === "auctions") openAuctions();
    else showView("profile");
  });
}
boot();
