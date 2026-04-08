// frontend/src/App.jsx
// VIT Sports Intelligence Network — v2.3.0
// Added: Accumulator tab (v2.3.0)

import { useEffect, useMemo, useState } from 'react'
import { fetchHealth, fetchHistory, fetchPicks, predictMatch, API_KEY } from './api'
import AdminPanel from './AdminPanel'
import AccumulatorPanel from './AccumulatorPanel'
import MatchDetail from './MatchDetail'
import './App.css'

const DEFAULT_FORM = {
  home_team: '',
  away_team: '',
  league: 'premier_league',
  kickoff_time: new Date().toISOString().slice(0, 16),
  home: 2.0,
  draw: 3.2,
  away: 3.8,
}

const MODEL_TYPE_COLORS = {
  Poisson: '#6366f1', XGBoost: '#10b981', MonteCarlo: '#f59e0b',
  Ensemble: '#8b5cf6', Causal: '#ec4899', Sentiment: '#14b8a6', Anomaly: '#f97316',
}

function ratingColor(r) {
  if (r >= 7) return '#10b981'
  if (r >= 5) return '#f59e0b'
  return '#ef4444'
}

function PickCard({ pick, onOpen }) {
  const edge = ((pick.edge || 0) * 100).toFixed(2)
  const isCertified = pick.pick_type === 'certified'
  return (
    <div className={`pick-card ${isCertified ? 'certified' : 'high-conf'}`} onClick={() => onOpen(pick.match_id)}>
      <div className="pick-card-badge">{isCertified ? '🏅 Certified' : '⚡ High Confidence'}</div>
      <div className="pick-card-teams">{pick.home_team} <span>vs</span> {pick.away_team}</div>
      <div className="pick-card-stats">
        <span>🎯 <strong>{pick.bet_side?.toUpperCase()}</strong></span>
        <span style={{ color: '#10b981' }}>📈 +{edge}% edge</span>
        <span>🎲 {pick.entry_odds?.toFixed(2)} odds</span>
        <span>💰 {((pick.recommended_stake || 0) * 100).toFixed(2)}% stake</span>
      </div>
      <div className="pick-card-models">
        <span>🤖 {pick.num_models} models</span>
        <span>✅ {pick.model_agreement_pct}% agree</span>
        <span>🧠 1X2 conf: {((pick.avg_1x2_confidence || 0) * 100).toFixed(0)}%</span>
      </div>
      <div className="pick-card-footer">
        {new Date(pick.timestamp).toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })}
        <span className="pick-view-link">View details →</span>
      </div>
    </div>
  )
}

function App() {
  const [activeTab, setActiveTab]   = useState('dashboard')
  const [health, setHealth]         = useState(null)
  const [history, setHistory]       = useState([])
  const [picks, setPicks]           = useState(null)
  const [form, setForm]             = useState(DEFAULT_FORM)
  const [prediction, setPrediction] = useState(null)
  const [loading, setLoading]       = useState(false)
  const [picksLoading, setPicksLoading] = useState(false)
  const [error, setError]           = useState('')
  const [page, setPage]             = useState(0)
  const [selectedMatchId, setSelectedMatchId] = useState(null)
  const itemsPerPage = 8

  const marketOdds = useMemo(
    () => ({ home: parseFloat(form.home), draw: parseFloat(form.draw), away: parseFloat(form.away) }),
    [form.home, form.draw, form.away],
  )

  useEffect(() => {
    fetchHealthStatus()
    loadHistory()
    const id = setInterval(fetchHealthStatus, 15000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    if (activeTab === 'picks' && !picks) loadPicks()
  }, [activeTab])

  async function fetchHealthStatus() {
    try { setHealth(await fetchHealth()) } catch (e) { setError(e.message) }
  }

  async function loadHistory() {
    try {
      const res = await fetchHistory(100, 0)
      setHistory(res.predictions || [])
      setPage(0)
    } catch (e) { setError(e.message) }
  }

  async function loadPicks() {
    setPicksLoading(true)
    try { const res = await fetchPicks(); setPicks(res) }
    catch (e) { setError(e.message) }
    finally { setPicksLoading(false) }
  }

  async function submitPrediction(e) {
    e.preventDefault()
    if (!form.home_team.trim() || !form.away_team.trim()) { setError('Please enter both team names'); return }
    if (form.home_team === form.away_team) { setError('Home and away teams must be different'); return }
    setLoading(true); setError(''); setPrediction(null)
    try {
      const payload = {
        home_team: form.home_team.trim(), away_team: form.away_team.trim(),
        league: form.league, kickoff_time: new Date(form.kickoff_time).toISOString(),
        market_odds: marketOdds,
      }
      const res = await predictMatch(payload)
      setPrediction(res)
      await loadHistory()
      if (picks) setPicks(null)
    } catch (e) { setError(e.message) } finally { setLoading(false) }
  }

  function updateField(key, val) { setForm(f => ({ ...f, [key]: val })) }

  const paginated = history.slice(page * itemsPerPage, (page + 1) * itemsPerPage)
  const maxPages  = Math.ceil(history.length / itemsPerPage)

  // v2.3.0: Added Accumulator tab
  const tabs = [
    { id: 'dashboard',   label: '📊 Dashboard' },
    { id: 'picks',       label: '🏅 Picks' },
    { id: 'accumulator', label: '🎰 Accumulator' },
    { id: 'admin',       label: '⚙️ Admin' },
  ]

  return (
    <div className="app-shell">
      {selectedMatchId && (
        <MatchDetail matchId={selectedMatchId} onClose={() => setSelectedMatchId(null)} />
      )}

      <header className="hero-panel">
        <div>
          <h1>⚽ VIT Predict</h1>
          <p>12-Model Ensemble · v2.3.0</p>
          <div className="tab-bar">
            {tabs.map(t => (
              <button key={t.id} className={activeTab === t.id ? 'tab-btn active' : 'tab-btn'}
                onClick={() => setActiveTab(t.id)}>{t.label}</button>
            ))}
          </div>
        </div>
        <div className="status-card">
          <h2>System Status</h2>
          {health ? (
            <ul>
              <li>Status: <strong>{health.status === 'ok' ? '✓ Online' : '✗ Offline'}</strong></li>
              <li>Models: <strong>{health.models_loaded || 0}/12</strong></li>
              <li>Database: <strong>{health.db_connected ? '✓ Connected' : '✗ Disconnected'}</strong></li>
              <li>CLV Tracking: <strong>{health.clv_tracking_enabled ? '✓ Enabled' : '✗ Disabled'}</strong></li>
            </ul>
          ) : (
            <p style={{ color: '#94a3b8', margin: 0 }}>Connecting…</p>
          )}
        </div>
      </header>

      <main>
        {/* ── Admin Panel ──────────────────────────────────────────── */}
        {activeTab === 'admin' && (
          <section className="panel">
            <AdminPanel apiKey={API_KEY} />
          </section>
        )}

        {/* ── Accumulator Panel (v2.3.0) ──────────────────────────── */}
        {activeTab === 'accumulator' && (
          <section className="panel">
            <div className="panel-header" style={{ marginBottom: 20 }}>
              <h2>🎰 Accumulator Generator</h2>
              <p style={{ color: '#64748b', margin: 0, fontSize: '0.9rem' }}>
                Find best accumulator combinations with edge analysis and correlation adjustment.
              </p>
            </div>
            <AccumulatorPanel apiKey={API_KEY} />
          </section>
        )}

        {/* ── Picks ────────────────────────────────────────────────── */}
        {activeTab === 'picks' && (
          <section className="panel picks-panel">
            <div className="panel-header">
              <h2>🏅 Market Picks</h2>
              <button type="button" onClick={loadPicks} className="secondary-button" disabled={picksLoading}>
                {picksLoading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
            {picksLoading && <div className="picks-loading">Loading picks from model ensemble…</div>}
            {picks && !picksLoading && (
              <>
                <div className="picks-info">
                  Certified picks require &gt;{(picks.edge_thresholds?.certified * 100).toFixed(0)}% edge |{' '}
                  High-confidence picks require &gt;{(picks.edge_thresholds?.high_confidence * 100).toFixed(0)}% edge
                </div>
                {picks.certified_picks?.length > 0 && (
                  <div className="picks-section">
                    <h3 className="picks-section-title">🏅 Certified Picks ({picks.certified_count})</h3>
                    <div className="picks-grid">
                      {picks.certified_picks.map(p => <PickCard key={p.match_id} pick={p} onOpen={setSelectedMatchId} />)}
                    </div>
                  </div>
                )}
                {picks.high_confidence_picks?.length > 0 && (
                  <div className="picks-section">
                    <h3 className="picks-section-title">⚡ High Confidence Picks ({picks.high_confidence_count})</h3>
                    <div className="picks-grid">
                      {picks.high_confidence_picks.map(p => <PickCard key={p.match_id} pick={p} onOpen={setSelectedMatchId} />)}
                    </div>
                  </div>
                )}
                {picks.certified_picks?.length === 0 && picks.high_confidence_picks?.length === 0 && (
                  <div className="picks-empty">
                    <div>📊</div>
                    <p>No qualifying picks yet. Generate predictions with market odds to populate this section.</p>
                    <button className="primary-button" onClick={() => setActiveTab('dashboard')} style={{ marginTop: 16, width: 'auto' }}>
                      Go to Dashboard →
                    </button>
                  </div>
                )}
              </>
            )}
            {!picks && !picksLoading && (
              <div className="picks-empty">
                <div>📊</div><p>Click Refresh to load certified and high-confidence picks.</p>
              </div>
            )}
          </section>
        )}

        {/* ── Dashboard ────────────────────────────────────────────── */}
        {activeTab === 'dashboard' && (
          <>
            <section className="panel">
              <h2>🎯 Make a Prediction</h2>
              <form className="prediction-form" onSubmit={submitPrediction}>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 20 }}>
                  <div className="field-group">
                    <label htmlFor="home_team">Home Team</label>
                    <input id="home_team" type="text" placeholder="e.g., Arsenal"
                      value={form.home_team} onChange={e => updateField('home_team', e.target.value)} required />
                  </div>
                  <div className="field-group">
                    <label htmlFor="away_team">Away Team</label>
                    <input id="away_team" type="text" placeholder="e.g., Chelsea"
                      value={form.away_team} onChange={e => updateField('away_team', e.target.value)} required />
                  </div>
                  <div className="field-group">
                    <label htmlFor="league">League</label>
                    <select id="league" value={form.league} onChange={e => updateField('league', e.target.value)}>
                      <option value="premier_league">Premier League</option>
                      <option value="la_liga">La Liga</option>
                      <option value="bundesliga">Bundesliga</option>
                      <option value="serie_a">Serie A</option>
                      <option value="ligue_1">Ligue 1</option>
                    </select>
                  </div>
                </div>
                <div style={{ marginTop: 20 }}>
                  <div className="field-group">
                    <label htmlFor="kickoff_time">Kickoff Time</label>
                    <input id="kickoff_time" type="datetime-local" value={form.kickoff_time}
                      onChange={e => updateField('kickoff_time', e.target.value)} required />
                  </div>
                </div>
                <div style={{ marginTop: 20 }}>
                  <label style={{ fontWeight: 600, color: '#334155', marginBottom: 12, display: 'block' }}>Market Odds</label>
                  <div className="market-grid">
                    {['home', 'draw', 'away'].map(k => (
                      <div key={k} className="field-group">
                        <label htmlFor={k}>{k.charAt(0).toUpperCase() + k.slice(1)}</label>
                        <input id={k} type="number" min="1" step="0.01" placeholder="2.00"
                          value={form[k]} onChange={e => updateField(k, e.target.value)} required />
                      </div>
                    ))}
                  </div>
                </div>
                <button type="submit" className="primary-button" disabled={loading}>
                  {loading ? 'Generating…' : 'Get Prediction'}
                </button>
              </form>

              {error && <div className="alert error">{error}</div>}

              {prediction && (
                <div className="result-card">
                  <h3>📊 Prediction Results</h3>
                  <dl>
                    {[
                      ['Match ID',        `#${prediction.match_id}`,                          '#64748b'],
                      ['Home Win',        `${(prediction.home_prob * 100).toFixed(1)}%`,       null],
                      ['Draw',            `${(prediction.draw_prob * 100).toFixed(1)}%`,       null],
                      ['Away Win',        `${(prediction.away_prob * 100).toFixed(1)}%`,       null],
                      ...(prediction.over_25_prob != null ? [
                        ['Over 2.5',      `${(prediction.over_25_prob * 100).toFixed(1)}%`,   null],
                        ['BTTS',          `${(prediction.btts_prob * 100).toFixed(1)}%`,      null],
                      ] : []),
                      ['Consensus',       `${(prediction.consensus_prob * 100).toFixed(1)}%`, null],
                      ['Expected Value',  `${(prediction.final_ev * 100).toFixed(2)}%`,       prediction.final_ev > 0 ? '#10b981' : '#ef4444'],
                      ['Recommended Stake', `${(prediction.recommended_stake * 100).toFixed(2)}%`, '#0ea5e9'],
                      ['Confidence',      `${(prediction.confidence * 100).toFixed(0)}%`,     '#f97316'],
                    ].map(([label, val, color]) => (
                      <div key={label}>
                        <dt>{label}</dt>
                        <dd style={color ? { color, fontWeight: 700 } : {}}>{val}</dd>
                      </div>
                    ))}
                  </dl>
                  <button className="secondary-button" style={{ marginTop: 12 }}
                    onClick={() => setSelectedMatchId(prediction.match_id)}>
                    View Full Detail →
                  </button>
                </div>
              )}
            </section>

            {history.length > 0 && (
              <section className="panel history-panel">
                <div className="panel-header">
                  <h2>📈 Prediction History</h2>
                  <button type="button" onClick={loadHistory} className="secondary-button">Refresh</button>
                </div>
                <p className="history-hint">Click any row to view model insights and market breakdown.</p>
                <div className="history-table-wrapper">
                  <table className="history-table">
                    <thead>
                      <tr>
                        <th>Match</th><th>Home %</th><th>Draw %</th><th>Away %</th>
                        <th>O2.5</th><th>BTTS</th><th>Edge</th><th>Stake</th><th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {paginated.map(item => (
                        <tr key={`${item.match_id}-${item.timestamp}`}
                          className="history-row-clickable" onClick={() => setSelectedMatchId(item.match_id)}>
                          <td style={{ fontWeight: 500 }}>
                            <span style={{ color: '#64748b', fontSize: '0.85rem' }}>#{item.match_id}</span>{' '}
                            {item.home_team?.split(' ').slice(-1)[0]} v {item.away_team?.split(' ').slice(-1)[0]}
                          </td>
                          <td>{(item.home_prob * 100).toFixed(1)}%</td>
                          <td>{(item.draw_prob * 100).toFixed(1)}%</td>
                          <td>{(item.away_prob * 100).toFixed(1)}%</td>
                          <td>{item.over_25_prob != null ? `${(item.over_25_prob * 100).toFixed(0)}%` : '—'}</td>
                          <td>{item.btts_prob != null ? `${(item.btts_prob * 100).toFixed(0)}%` : '—'}</td>
                          <td style={{ color: (item.final_ev || item.edge) > 0 ? '#10b981' : '#ef4444', fontWeight: 600 }}>
                            {((item.final_ev || item.edge) * 100).toFixed(2)}%
                          </td>
                          <td>{(item.recommended_stake * 100).toFixed(2)}%</td>
                          <td style={{ color: '#94a3b8', fontSize: '0.9rem' }}>
                            {item.timestamp ? new Date(item.timestamp).toLocaleString('en-US', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {maxPages > 1 && (
                  <div style={{ marginTop: 20, display: 'flex', gap: 10, justifyContent: 'center', alignItems: 'center' }}>
                    <button className="secondary-button" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>← Previous</button>
                    <span style={{ color: '#64748b', fontWeight: 500 }}>Page {page + 1} of {maxPages}</span>
                    <button className="secondary-button" onClick={() => setPage(p => Math.min(maxPages - 1, p + 1))} disabled={page >= maxPages - 1}>Next →</button>
                  </div>
                )}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  )
}

export default App
