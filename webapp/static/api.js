export async function loadMe(initData) {
  const response = await fetch("/api/webapp/me", {
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
