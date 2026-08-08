const DEFAULT_TIMEOUT_MS = 10000;
const IMAGE_TIMEOUT_MS = 12000;

async function request(path, initData, responseType = "json", timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(path, {
      method: "GET",
      headers: {
        Authorization: `tma ${initData}`,
        Accept: responseType === "blob" ? "image/*" : "application/json",
      },
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });

    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const error = new Error(payload.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.code = payload.error || "";
      throw error;
    }

    return responseType === "blob" ? response.blob() : response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      const timeoutError = new Error("request_timeout");
      timeoutError.code = "request_timeout";
      throw timeoutError;
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function loadMe(initData) {
  return request("/api/webapp/me", initData);
}

export function loadAuctionHome(initData, selectedDate = "") {
  const query = selectedDate ? `?date=${encodeURIComponent(selectedDate)}` : "";
  return request(`/api/webapp/auction-home${query}`, initData);
}

export function loadFreeSlots(initData, selectedDate) {
  const query = selectedDate ? `?date=${encodeURIComponent(selectedDate)}` : "";
  return request(`/api/webapp/free-slots${query}`, initData);
}

export function loadCardImage(initData, path) {
  return request(path, initData, "blob", IMAGE_TIMEOUT_MS);
}
