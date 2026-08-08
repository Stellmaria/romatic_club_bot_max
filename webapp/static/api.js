async function requestJson(path, initData) {
  const response = await fetch(path, {
    method: "GET",
    headers: {
      Authorization: `tma ${initData}`,
      Accept: "application/json",
    },
    credentials: "same-origin",
  });

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }

  return response.json();
}

export function loadMe(initData) {
  return requestJson("/api/webapp/me", initData);
}

export function loadAuctions(initData) {
  return requestJson("/api/webapp/auctions", initData);
}
