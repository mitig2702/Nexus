const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
const API_KEY = import.meta.env.VITE_API_KEY || "";

async function request(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...options.headers };
  if (API_KEY) headers["X-API-Key"] = API_KEY;

  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    const message = data?.detail?.error || data?.error || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  getRequests: (params = {}) => {
    const qs = new URLSearchParams(params).toString();
    return request(`/requests${qs ? `?${qs}` : ""}`);
  },

  getRequest: (id) => request(`/requests/${id}`),

  overrideRequest: (id, payload) =>
    request(`/requests/${id}/override`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  ingestRequest: (payload) =>
    request("/requests/ingest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  batchProcess: () =>
    request("/requests/batch-process", {
      method: "POST",
    }),

  getInvoices: () => request("/invoices"),

  seedData: () =>
    request("/seed", {
      method: "POST",
    }),

  getStats: () => request("/stats"),
};
