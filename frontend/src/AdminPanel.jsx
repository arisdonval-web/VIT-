// frontend/src/AdminPanel.jsx
// VIT Sports Intelligence — v2.2.0
// Sections: Model Status | Data Sources | Manual Match | CSV Upload | Predictions

import { useEffect, useRef, useState } from 'react'
import {
  fetchModelStatus, reloadModels,
  fetchDataSourceStatus,
  addManualMatch,
  uploadCSVFixtures,
  API_KEY,
} from './api'

const LEAGUE_OPTIONS = [
  { value: 'premier_league', label: 'Premier League' },
  { value: 'la_liga',        label: 'La Liga' },
  { value: 'bundesliga',     label: 'Bundesliga' },
  { value: 'serie_a',        label: 'Serie A' },
  { value: 'ligue_1',        label: 'Ligue 1' },
]

// ── Shared style tokens ───────────────────────────────────────────────
const card = {
  background: '#fff', border: '1px solid #e2e8f0',
  borderRadius: 12, padding: '20px 24px', marginBottom: 20,
  boxShadow: '0 2px 8px rgba(15,23,42,0.06)',
}
const sectionTitle = { fontSize: '1rem', fontWeight: 700, color: '#0f172a', marginBottom: 16, marginTop: 0 }
const labelStyle   = { display: 'block', fontSize: '0.78rem', fontWeight: 600, color: '#475569', marginBottom: 4 }
const inputStyle   = {
  width: '100%', padding: '8px 12px', border: '1px solid #cbd5e1',
  borderRadius: 8, fontSize: '0.9rem', background: '#f8fafc', outline: 'none',
}
const btnPrimary = {
  background: 'linear-gradient(135deg,#0ea5e9,#6366f1)', color: '#fff',
  border: 'none', borderRadius: 8, padding: '9px 20px',
  fontWeight: 600, fontSize: '0.88rem', cursor: 'pointer',
}
const btnSecondary = {
  background: '#f1f5f9', color: '#334155',
  border: '1px solid #e2e8f0', borderRadius: 8, padding: '9px 20px',
  fontWeight: 600, fontSize: '0.88rem', cursor: 'pointer',
}
const badge = (color) => ({
  display: 'inline-block', padding: '2px 10px', borderRadius: 99,
  fontSize: '0.75rem', fontWeight: 700,
  background: color === 'green' ? '#dcfce7' : color === 'red' ? '#fee2e2' : color === 'yellow' ? '#fef9c3' : '#f1f5f9',
  color:      color === 'green' ? '#15803d' : color === 'red' ? '#b91c1c' : color === 'yellow' ? '#92400e' : '#64748b',
})

// ── Status badge helper ───────────────────────────────────────────────
function SourceBadge({ status }) {
  const map = {
    live:     { color: 'green',  label: '● Live' },
    limited:  { color: 'yellow', label: '⚠ Limited' },
    error:    { color: 'red',    label: '✕ Error' },
    down:     { color: 'red',    label: '✕ Down' },
    no_key:   { color: 'gray',   label: '— No Key' },
  }
  const { color, label } = map[status] || { color: 'gray', label: status }
  return <span style={badge(color)}>{label}</span>
}

// ── Model row ─────────────────────────────────────────────────────────
function ModelRow({ model }) {
  const isReady = model.status === 'ready'
  return (
    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
      <td style={{ padding: '8px 12px', fontWeight: 600, fontSize: '0.88rem' }}>{model.name}</td>
      <td style={{ padding: '8px 12px', color: '#64748b', fontSize: '0.82rem' }}>{model.model_type}</td>
      <td style={{ padding: '8px 12px' }}>
        <span style={badge(isReady ? 'green' : 'red')}>
          {isReady ? '✓ Ready' : '✕ Failed'}
        </span>
      </td>
      <td style={{ padding: '8px 12px', color: '#94a3b8', fontSize: '0.82rem' }}>
        {isReady ? `w: ${model.weight?.toFixed(2)}` : (model.error ? model.error.slice(0, 40) + '…' : '—')}
      </td>
    </tr>
  )
}

// ── CSV result row ────────────────────────────────────────────────────
function CsvResultRow({ r }) {
  return (
    <tr style={{ borderBottom: '1px solid #f1f5f9' }}>
      <td style={{ padding: '6px 10px', fontSize: '0.83rem' }}>{r.home_team} vs {r.away_team}</td>
      <td style={{ padding: '6px 10px', fontSize: '0.83rem', color: '#64748b' }}>{r.league}</td>
      <td style={{ padding: '6px 10px', fontSize: '0.83rem' }}>{(r.home_prob * 100).toFixed(1)}% / {(r.draw_prob * 100).toFixed(1)}% / {(r.away_prob * 100).toFixed(1)}%</td>
      <td style={{ padding: '6px 10px', fontSize: '0.83rem', fontWeight: 700, color: r.edge > 0.02 ? '#10b981' : '#94a3b8' }}>
        {r.has_edge ? `${(r.edge * 100).toFixed(2)}%` : 'No edge'}
      </td>
    </tr>
  )
}

// ── Main Component ────────────────────────────────────────────────────
export default function AdminPanel({ apiKey }) {
  const key = apiKey || API_KEY

  // Model status
  const [models, setModels]         = useState(null)
  const [modelsLoading, setML]      = useState(false)
  const [reloading, setReloading]   = useState(false)
  const [reloadMsg, setReloadMsg]   = useState('')

  // Data sources
  const [sources, setSources]       = useState(null)
  const [sourcesLoading, setSL]     = useState(false)

  // Manual match
  const [manualForm, setManualForm] = useState({
    home_team: '', away_team: '', league: 'premier_league',
    kickoff_time: new Date().toISOString().slice(0, 16),
    home_odds: 2.30, draw_odds: 3.30, away_odds: 3.10,
  })
  const [manualResult, setManualResult] = useState(null)
  const [manualLoading, setManualLoad]  = useState(false)
  const [manualError, setManualError]   = useState('')

  // CSV upload
  const [csvFile, setCsvFile]       = useState(null)
  const [csvResult, setCsvResult]   = useState(null)
  const [csvLoading, setCsvLoading] = useState(false)
  const [csvError, setCsvError]     = useState('')
  const fileRef = useRef(null)

  // Streaming predictions (existing)
  const [status, setStatus]         = useState('idle')
  const [log, setLog]               = useState([])
  const [predictions, setPreds]     = useState([])
  const [progress, setProgress]     = useState({ current: 0, total: 0 })
  const [count, setCount]           = useState(10)
  const [streamError, setStreamErr] = useState('')
  const esRef                       = useRef(null)
  const bottomRef                   = useRef(null)

  useEffect(() => {
    loadModelStatus()
    loadDataSources()
    return () => esRef.current?.close()
  }, [])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [predictions, log])

  async function loadModelStatus() {
    setML(true)
    try { setModels(await fetchModelStatus(key)) } catch (e) { console.error(e) }
    finally { setML(false) }
  }

  async function handleReload() {
    setReloading(true); setReloadMsg('')
    try {
      const r = await reloadModels(key)
      setReloadMsg(`✅ ${r.message}`)
      await loadModelStatus()
    } catch (e) { setReloadMsg(`❌ ${e.message}`) }
    finally { setReloading(false) }
  }

  async function loadDataSources() {
    setSL(true)
    try { setSources(await fetchDataSourceStatus(key)) } catch (e) { console.error(e) }
    finally { setSL(false) }
  }

  function updateManual(k, v) { setManualForm(f => ({ ...f, [k]: v })) }

  async function submitManualMatch() {
    setManualLoad(true); setManualError(''); setManualResult(null)
    try {
      const r = await addManualMatch(key, {
        home_team:    manualForm.home_team.trim(),
        away_team:    manualForm.away_team.trim(),
        league:       manualForm.league,
        kickoff_time: new Date(manualForm.kickoff_time).toISOString(),
        home_odds:    parseFloat(manualForm.home_odds),
        draw_odds:    parseFloat(manualForm.draw_odds),
        away_odds:    parseFloat(manualForm.away_odds),
      })
      setManualResult(r)
    } catch (e) { setManualError(e.message) }
    finally { setManualLoad(false) }
  }

  async function submitCSV() {
    if (!csvFile) return
    setCsvLoading(true); setCsvError(''); setCsvResult(null)
    try { setCsvResult(await uploadCSVFixtures(key, csvFile)) }
    catch (e) { setCsvError(e.message) }
    finally { setCsvLoading(false) }
  }

  // ── Streaming predictions ─────────────────────────────────────────
  function startStream() {
    esRef.current?.close()
    setStatus('running'); setLog([]); setPreds([]); setProgress({ current: 0, total: 0 }); setStreamErr('')
    const url = `/admin/stream-predictions?api_key=${encodeURIComponent(key)}&count=${count}&force_alert=true`
    const es  = new EventSource(url)
    esRef.current = es

    es.onmessage = (e) => {
      const d = JSON.parse(e.data)
      if (d.type === 'status')     setLog(l => [...l, d.message])
      if (d.type === 'progress')   setProgress({ current: d.current, total: d.total })
      if (d.type === 'prediction') setPreds(p => [...p, d])
      if (d.type === 'error')      setLog(l => [...l, `⚠ ${d.fixture}: ${d.message}`])
      if (d.type === 'done')       { setStatus('done'); es.close() }
    }
    es.onerror = () => { setStatus('error'); setStreamErr('Stream disconnected.'); es.close() }
  }

  function stopStream() { esRef.current?.close(); setStatus('idle') }

  const readyCount = models?.ready ?? '…'
  const totalCount = models?.total ?? 12

  return (
    <div style={{ maxWidth: 1000, margin: '0 auto' }}>

      {/* ── Model Status ─────────────────────────────────────────── */}
      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ ...sectionTitle, marginBottom: 0 }}>
            🤖 Model Status
            <span style={{ marginLeft: 12, ...badge(readyCount >= 10 ? 'green' : readyCount >= 6 ? 'yellow' : 'red') }}>
              {readyCount}/{totalCount} ready
            </span>
          </h3>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {reloadMsg && <span style={{ fontSize: '0.82rem', color: reloadMsg.startsWith('✅') ? '#15803d' : '#b91c1c' }}>{reloadMsg}</span>}
            <button style={btnSecondary} onClick={loadModelStatus} disabled={modelsLoading}>
              {modelsLoading ? 'Loading…' : '↻ Refresh'}
            </button>
            <button style={btnPrimary} onClick={handleReload} disabled={reloading}>
              {reloading ? 'Reloading…' : '⚡ Reload All Models'}
            </button>
          </div>
        </div>

        {models ? (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                  {['Model', 'Type', 'Status', 'Info'].map(h => (
                    <th key={h} style={{ padding: '8px 12px', textAlign: 'left', fontSize: '0.78rem', fontWeight: 700, color: '#475569' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {models.models?.map(m => <ModelRow key={m.name} model={m} />)}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ color: '#94a3b8', fontSize: '0.88rem', margin: 0 }}>
            {modelsLoading ? 'Loading model status…' : 'Click Refresh to load model status.'}
          </p>
        )}
      </div>

      {/* ── Data Source Health ───────────────────────────────────── */}
      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <h3 style={{ ...sectionTitle, marginBottom: 0 }}>📡 Data Source Health</h3>
          <button style={btnSecondary} onClick={loadDataSources} disabled={sourcesLoading}>
            {sourcesLoading ? 'Checking…' : '↻ Recheck'}
          </button>
        </div>
        {sources ? (
          <div style={{ display: 'flex', gap: 20, flexWrap: 'wrap' }}>
            {Object.entries(sources.sources || {}).map(([key, val]) => (
              <div key={key} style={{ background: '#f8fafc', borderRadius: 10, padding: '12px 18px', flex: 1, minWidth: 200 }}>
                <div style={{ fontWeight: 700, fontSize: '0.88rem', marginBottom: 6 }}>
                  {key === 'football_data' ? '⚽ Football-Data.org' : '📊 The Odds API'}
                </div>
                <div style={{ marginBottom: 4 }}><SourceBadge status={val.status} /></div>
                <div style={{ fontSize: '0.78rem', color: '#64748b' }}>{val.message}</div>
              </div>
            ))}
          </div>
        ) : (
          <p style={{ color: '#94a3b8', fontSize: '0.88rem', margin: 0 }}>
            {sourcesLoading ? 'Checking connections…' : 'Click Recheck to test API connections.'}
          </p>
        )}
      </div>

      {/* ── Manual Match Entry ───────────────────────────────────── */}
      <div style={card}>
        <h3 style={sectionTitle}>➕ Add Match Manually</h3>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginTop: -8, marginBottom: 16 }}>
          Use when the Football-Data API is unavailable. Enter match details to run a prediction immediately.
        </p>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
          {[['home_team', 'Home Team', 'text', 'e.g. Arsenal'], ['away_team', 'Away Team', 'text', 'e.g. Chelsea']].map(([k, lbl, type, ph]) => (
            <div key={k}>
              <label style={labelStyle}>{lbl}</label>
              <input style={inputStyle} type={type} placeholder={ph}
                value={manualForm[k]} onChange={e => updateManual(k, e.target.value)} />
            </div>
          ))}
          <div>
            <label style={labelStyle}>League</label>
            <select style={inputStyle} value={manualForm.league} onChange={e => updateManual('league', e.target.value)}>
              {LEAGUE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <div>
            <label style={labelStyle}>Kickoff Time</label>
            <input style={inputStyle} type="datetime-local" value={manualForm.kickoff_time}
              onChange={e => updateManual('kickoff_time', e.target.value)} />
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 14, marginTop: 14 }}>
          {[['home_odds', 'Home Odds'], ['draw_odds', 'Draw Odds'], ['away_odds', 'Away Odds']].map(([k, lbl]) => (
            <div key={k}>
              <label style={labelStyle}>{lbl}</label>
              <input style={inputStyle} type="number" step="0.01" min="1.01"
                value={manualForm[k]} onChange={e => updateManual(k, e.target.value)} />
            </div>
          ))}
        </div>

        {manualError && <div style={{ marginTop: 12, padding: '8px 12px', background: '#fee2e2', borderRadius: 8, color: '#b91c1c', fontSize: '0.85rem' }}>{manualError}</div>}

        <button style={{ ...btnPrimary, marginTop: 16 }} onClick={submitManualMatch} disabled={manualLoading}>
          {manualLoading ? 'Running prediction…' : '🎯 Run Prediction'}
        </button>

        {manualResult && (
          <div style={{ marginTop: 16, background: '#f0fdf4', border: '1px solid #86efac', borderRadius: 10, padding: '14px 18px' }}>
            <div style={{ fontWeight: 700, marginBottom: 8, color: '#15803d' }}>✅ Prediction complete</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 10, fontSize: '0.88rem' }}>
              {[
                ['Home Win', `${((manualResult.predictions?.home_prob || 0) * 100).toFixed(1)}%`],
                ['Draw',     `${((manualResult.predictions?.draw_prob || 0) * 100).toFixed(1)}%`],
                ['Away Win', `${((manualResult.predictions?.away_prob || 0) * 100).toFixed(1)}%`],
                ['Best Bet', manualResult.best_bet?.best_side?.toUpperCase() || 'None'],
                ['Edge',     `${((manualResult.best_bet?.edge || 0) * 100).toFixed(2)}%`],
                ['Stake',    `${((manualResult.best_bet?.kelly_stake || 0) * 100).toFixed(2)}%`],
              ].map(([l, v]) => (
                <div key={l}><span style={{ color: '#64748b' }}>{l}: </span><strong>{v}</strong></div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* ── CSV Upload ───────────────────────────────────────────── */}
      <div style={card}>
        <h3 style={sectionTitle}>📤 Bulk Upload via CSV</h3>
        <p style={{ color: '#64748b', fontSize: '0.85rem', marginTop: -8, marginBottom: 12 }}>
          CSV columns: <code>home_team, away_team, league, kickoff_time, home_odds, draw_odds, away_odds</code>
        </p>

        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <input ref={fileRef} type="file" accept=".csv" style={{ display: 'none' }}
            onChange={e => setCsvFile(e.target.files[0])} />
          <button style={btnSecondary} onClick={() => fileRef.current.click()}>
            📂 {csvFile ? csvFile.name : 'Choose CSV file'}
          </button>
          <button style={btnPrimary} onClick={submitCSV} disabled={!csvFile || csvLoading}>
            {csvLoading ? 'Processing…' : '⚡ Run Batch Predictions'}
          </button>
          {csvFile && <button style={{ ...btnSecondary, padding: '9px 14px' }} onClick={() => { setCsvFile(null); setCsvResult(null); fileRef.current.value = '' }}>✕ Clear</button>}
        </div>

        {csvError && <div style={{ marginTop: 10, padding: '8px 12px', background: '#fee2e2', borderRadius: 8, color: '#b91c1c', fontSize: '0.85rem' }}>{csvError}</div>}

        {csvResult && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', gap: 16, marginBottom: 12, fontSize: '0.88rem' }}>
              <span style={badge('green')}>✓ {csvResult.processed} processed</span>
              {csvResult.errors > 0 && <span style={badge('red')}>✕ {csvResult.errors} errors</span>}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                    {['Match', 'League', 'H / D / A Prob', 'Edge'].map(h => (
                      <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: '0.76rem', fontWeight: 700, color: '#475569' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {csvResult.results?.map((r, i) => <CsvResultRow key={i} r={r} />)}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* ── Stream Predictions (existing, enhanced) ──────────────── */}
      <div style={card}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16, flexWrap: 'wrap', gap: 12 }}>
          <h3 style={{ ...sectionTitle, marginBottom: 0 }}>⚡ Auto-Fetch & Predict</h3>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <label style={{ ...labelStyle, marginBottom: 0 }}>Fixtures:</label>
            <input type="number" min={1} max={20} value={count}
              onChange={e => setCount(Number(e.target.value))}
              style={{ ...inputStyle, width: 64 }} />
            {status !== 'running'
              ? <button style={btnPrimary} onClick={startStream}>▶ Run</button>
              : <button style={{ ...btnSecondary, color: '#b91c1c' }} onClick={stopStream}>■ Stop</button>
            }
          </div>
        </div>

        {status === 'running' && progress.total > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', color: '#64748b', marginBottom: 6 }}>
              <span>Processing…</span><span>{progress.current}/{progress.total}</span>
            </div>
            <div style={{ background: '#e2e8f0', borderRadius: 99, height: 8, overflow: 'hidden' }}>
              <div style={{ width: `${(progress.current / progress.total) * 100}%`, background: 'linear-gradient(90deg,#0ea5e9,#6366f1)', height: '100%', borderRadius: 99, transition: 'width 0.4s' }} />
            </div>
          </div>
        )}

        {log.map((l, i) => (
          <div key={i} style={{ fontSize: '0.82rem', color: '#64748b', marginBottom: 3 }}>{l}</div>
        ))}

        {predictions.length > 0 && (
          <div style={{ marginTop: 12, overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#f8fafc', borderBottom: '2px solid #e2e8f0' }}>
                  {['#', 'Match', 'H%', 'D%', 'A%', 'Edge', 'Stake', 'Models', 'Alert'].map(h => (
                    <th key={h} style={{ padding: '7px 10px', textAlign: 'left', fontSize: '0.76rem', fontWeight: 700, color: '#475569' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {predictions.map(p => (
                  <tr key={p.index} style={{ borderBottom: '1px solid #f1f5f9' }}>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem', color: '#94a3b8' }}>#{p.index}</td>
                    <td style={{ padding: '7px 10px', fontSize: '0.83rem', fontWeight: 500 }}>
                      {p.home_team?.split(' ').slice(-1)} <span style={{ color: '#94a3b8' }}>v</span> {p.away_team?.split(' ').slice(-1)}
                    </td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem' }}>{(p.home_prob * 100).toFixed(1)}%</td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem' }}>{(p.draw_prob * 100).toFixed(1)}%</td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem' }}>{(p.away_prob * 100).toFixed(1)}%</td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem', fontWeight: 700, color: p.edge > 0.02 ? '#10b981' : '#94a3b8' }}>
                      {(p.edge * 100).toFixed(2)}%
                    </td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem' }}>{(p.stake * 100).toFixed(2)}%</td>
                    <td style={{ padding: '7px 10px', fontSize: '0.82rem', color: '#64748b' }}>
                      {p.models_used ?? 0}/{p.models_total ?? 12}
                    </td>
                    <td style={{ padding: '7px 10px' }}>
                      <span style={badge(p.alert_sent ? 'green' : 'gray')}>{p.alert_sent ? '📲 Sent' : '—'}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {streamError && <div style={{ marginTop: 10, color: '#b91c1c', fontSize: '0.85rem' }}>{streamError}</div>}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
