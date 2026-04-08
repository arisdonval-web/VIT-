// frontend/src/api.js
// VIT Sports Intelligence Network — v2.3.0
// Added: model status, reload, data source health, manual match,
//        CSV upload, accumulator candidates/generate/send

const API_BASE_URL = import.meta.env.VITE_API_URL || ''
export const API_KEY = import.meta.env.VITE_API_KEY || 'dev_api_key_12345'

function defaultHeaders(extra = {}) {
  return { 'Content-Type': 'application/json', 'x-api-key': API_KEY, ...extra }
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

// ── Existing ──────────────────────────────────────────────────────────
export async function fetchHealth()               { return apiFetch('/health') }
export async function fetchHistory(limit=10, offset=0) { return apiFetch(`/history?limit=${limit}&offset=${offset}`) }
export async function fetchMatchDetail(matchId)   { return apiFetch(`/history/${matchId}`) }
export async function fetchPicks()                { return apiFetch('/history/picks') }
export async function predictMatch(matchData)     { return apiFetch('/predict', { method: 'POST', body: JSON.stringify(matchData) }) }
export async function fetchAdminFixtures(apiKey, count=10) { return apiFetch(`/admin/fixtures?api_key=${encodeURIComponent(apiKey)}&count=${count}`) }

// ── v2.2.0 — Model Management ─────────────────────────────────────────
export async function fetchModelStatus(apiKey) {
  return apiFetch(`/admin/models/status?api_key=${encodeURIComponent(apiKey)}`)
}

export async function reloadModels(apiKey, modelKey = null) {
  return apiFetch(`/admin/models/reload?api_key=${encodeURIComponent(apiKey)}`, {
    method: 'POST',
    body: JSON.stringify({ model_key: modelKey }),
  })
}

// ── v2.2.0 — Data Source Health ──────────────────────────────────────
export async function fetchDataSourceStatus(apiKey) {
  return apiFetch(`/admin/data-sources/status?api_key=${encodeURIComponent(apiKey)}`)
}

// ── v2.2.0 — Manual Match Entry ──────────────────────────────────────
export async function addManualMatch(apiKey, matchData) {
  return apiFetch(`/admin/matches/manual?api_key=${encodeURIComponent(apiKey)}`, {
    method: 'POST',
    body: JSON.stringify(matchData),
  })
}

// ── v2.2.0 — CSV Upload ──────────────────────────────────────────────
export async function uploadCSVFixtures(apiKey, file) {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(
    `${API_BASE_URL}/admin/upload/csv?api_key=${encodeURIComponent(apiKey)}`,
    { method: 'POST', headers: { 'x-api-key': API_KEY }, body: formData }
  )
  if (!res.ok) throw new Error(await res.text() || 'Upload failed')
  return res.json()
}

// ── v2.3.0 — Accumulator ─────────────────────────────────────────────
export async function fetchAccumulatorCandidates(apiKey, { minConfidence = 0.60, minEdge = 0.01, count = 15 } = {}) {
  return apiFetch(
    `/admin/accumulator/candidates?api_key=${encodeURIComponent(apiKey)}&min_confidence=${minConfidence}&min_edge=${minEdge}&count=${count}`
  )
}

export async function generateAccumulators(apiKey, { candidates, minLegs = 2, maxLegs = 6, topN = 10 }) {
  return apiFetch(`/admin/accumulator/generate?api_key=${encodeURIComponent(apiKey)}`, {
    method: 'POST',
    body: JSON.stringify({ candidates, min_legs: minLegs, max_legs: maxLegs, top_n: topN }),
  })
}

export async function sendAccumulatorToTelegram(apiKey, accumulator, channelNote = '') {
  return apiFetch(`/admin/accumulator/send?api_key=${encodeURIComponent(apiKey)}`, {
    method: 'POST',
    body: JSON.stringify({ accumulator, channel_note: channelNote }),
  })
}
