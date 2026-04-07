const API_BASE_URL = import.meta.env.VITE_API_URL || ''
export const API_KEY = import.meta.env.VITE_API_KEY || 'dev_api_key_12345'

function defaultHeaders(extra = {}) {
  return {
    'Content-Type': 'application/json',
    'x-api-key': API_KEY,
    ...extra,
  }
}

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: defaultHeaders(),
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || res.statusText || 'Request failed')
  }
  return res.json()
}

export async function fetchHealth() {
  return apiFetch('/health')
}

export async function fetchHistory(limit = 10, offset = 0) {
  return apiFetch(`/history?limit=${limit}&offset=${offset}`)
}

export async function fetchMatchDetail(matchId) {
  return apiFetch(`/history/${matchId}`)
}

export async function fetchPicks() {
  return apiFetch('/history/picks')
}

export async function predictMatch(matchData) {
  return apiFetch('/predict', { method: 'POST', body: JSON.stringify(matchData) })
}

export async function fetchAdminFixtures(apiKey, count = 10) {
  return apiFetch(`/admin/fixtures?api_key=${encodeURIComponent(apiKey)}&count=${count}`)
}
