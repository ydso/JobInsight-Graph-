export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8000";

export async function fetchJson(path) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Accept: "application/json" }
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  return response.json();
}

export function encodePath(value) {
  return encodeURIComponent(value || "");
}
