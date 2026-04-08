import { useEffect, useState } from 'react'
import { fetchMatchDetail } from './api'

const MARKET_LABELS = {
  1: '1X2',
  2: 'O/U',
  3: 'BTTS',
  4: 'Exact',
  match_odds: '1X2',
  over_under: 'O/U',
  btts: 'BTTS',
  exact_score: 'Exact',
}

function marketLabel(mkt) {
  if (mkt == null) return '?'
  return MARKET_LABELS[mkt] || String(mkt).toUpperCase()
}

const MODEL_TYPE_COLORS = {
  Poisson: '#6366f1',
  XGBoost: '#10b981',
  MonteCarlo: '#f59e0b',
  Ensemble: '#8b5cf6',
  Causal: '#ec4899',
  Sentiment: '#14b8a6',
  Anomaly: '#f97316',
  LSTM: '#3b82f6',
  Transformer: '#a855f7',
  GNN: '#06b6d4',
  Bayesian: '#84cc16',
  RL: '#ef4444',
}

function ratingColor(r) {
  if (r >= 8) return '#10b981'
  if (r >= 6) return '#f59e0b'
  return '#ef4444'
}

function ProbBar({ label, value, color }) {
  const pct = Math.round((value || 0) * 100)
  return (
    <div className="prob-bar-row">
      <span className="prob-bar-label">{label}</span>
      <div className="prob-bar-track">
        <div className="prob-bar-fill" style={{ width: `${pct}%`, background: color || '#6366f1' }} />
      </div>
      <span className="prob-bar-value">{pct}%</span>
    </div>
  )
}

function ModelCard({ model }) {
  const color = MODEL_TYPE_COLORS[model.model_type] || '#64748b'
  const rating = model.rating || model.confidence_1x2 * 10 || 5
  return (
    <div className="model-card" style={{ borderLeft: `3px solid ${color}` }}>
      <div className="model-card-header">
        <span className="model-type-badge" style={{ background: color }}>
          {model.model_type || model.type}
        </span>
        <span className="model-name">{model.model_name || model.name}</span>
        <span className="model-rating" style={{ color: ratingColor(rating) }}>
          ★ {(rating).toFixed(1)}
        </span>
      </div>
      {model.probabilities && (
        <div className="model-probs">
          {Object.entries(model.probabilities).map(([k, v]) => (
            <span key={k} className="model-prob-chip">
              {k.replace('_prob', '').replace('_2_5', '').toUpperCase()}: {v}%
            </span>
          ))}
        </div>
      )}
      <div className="model-meta">
        <span>Confidence: <strong>{((model.confidence || model.confidence_1x2 || 0.5) * 100).toFixed(0)}%</strong></span>
        {model.latency_ms != null && <span>Latency: {model.latency_ms}ms</span>}
        {model.weight != null && <span>Weight: {(model.weight || 1).toFixed(2)}×</span>}
      </div>
    </div>
  )
}

function MarketSection({ title, icon, probs, breakdown }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="market-section">
      <div className="market-section-header" onClick={() => setOpen(o => !o)}>
        <span>{icon} {title}</span>
        <span className="market-toggle">{open ? '▲' : '▼'} {breakdown?.length || 0} models</span>
      </div>
      <div className="market-prob-grid">
        {probs.map(({ label, value, color }) => (
          <ProbBar key={label} label={label} value={value} color={color} />
        ))}
      </div>
      {open && breakdown && breakdown.length > 0 && (
        <div className="model-breakdown">
          <h5 className="breakdown-title">Child Model Ratings</h5>
          {breakdown.map((m, i) => (
            <ModelCard key={i} model={m} />
          ))}
        </div>
      )}
    </div>
  )
}

export default function MatchDetail({ matchId, onClose }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setLoading(true)
    setError('')
    fetchMatchDetail(matchId)
      .then(setDetail)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [matchId])

  if (loading) return (
    <div className="detail-overlay" onClick={onClose}>
      <div className="detail-modal" onClick={e => e.stopPropagation()}>
        <div style={{ padding: 40, textAlign: 'center', color: '#64748b' }}>Loading match details…</div>
      </div>
    </div>
  )

  if (error) return (
    <div className="detail-overlay" onClick={onClose}>
      <div className="detail-modal" onClick={e => e.stopPropagation()}>
        <div style={{ padding: 40, textAlign: 'center', color: '#ef4444' }}>{error}</div>
        <button onClick={onClose} className="secondary-button" style={{ margin: '0 auto 20px', display: 'block' }}>Close</button>
      </div>
    </div>
  )

  if (!detail) return null

  const { match, prediction, markets, neural_info, clv } = detail
  const model_summary = detail.model_summary || { active_models: 0, total_models: 12, models: [] }
  const marketsData = markets || {}
  const edgePct = ((prediction.edge || 0) * 100).toFixed(2)
  const edgePos = (prediction.edge || 0) > 0

  return (
    <div className="detail-overlay" onClick={onClose}>
      <div className="detail-modal" onClick={e => e.stopPropagation()}>
        <button className="detail-close" onClick={onClose}>✕</button>

        {/* Match Header */}
        <div className="detail-header">
          <div className="detail-teams">
            <span className="detail-home">{match.home_team}</span>
            <span className="detail-vs">vs</span>
            <span className="detail-away">{match.away_team}</span>
          </div>
          <div className="detail-meta-row">
            <span className="detail-badge">{match.league?.replace(/_/g, ' ').toUpperCase()}</span>
            <span className="detail-badge secondary">
              {new Date(match.kickoff_time).toLocaleString('en-GB', {
                day: '2-digit', month: 'short', year: 'numeric',
                hour: '2-digit', minute: '2-digit', timeZone: 'UTC'
              })} UTC
            </span>
            {match.actual_outcome && (
              <span className="detail-badge success">Result: {match.actual_outcome?.toUpperCase()}</span>
            )}
          </div>
        </div>

        {/* Key Stats Row */}
        <div className="detail-stats-row">
          <div className="stat-chip">
            <span className="stat-label">Bet Side</span>
            <span className="stat-value accent">{prediction.bet_side?.toUpperCase() || '—'}</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Edge</span>
            <span className="stat-value" style={{ color: edgePos ? '#10b981' : '#ef4444' }}>
              {edgePos ? '+' : ''}{edgePct}%
            </span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Odds</span>
            <span className="stat-value">{prediction.entry_odds?.toFixed(2) || '—'}</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Stake</span>
            <span className="stat-value accent">{((prediction.recommended_stake || 0) * 100).toFixed(2)}%</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Confidence</span>
            <span className="stat-value">{((prediction.confidence || 0) * 100).toFixed(0)}%</span>
          </div>
          <div className="stat-chip">
            <span className="stat-label">Models</span>
            <span className="stat-value">{model_summary.active_models}/{model_summary.total_models}</span>
          </div>
        </div>

        {/* Neural Info */}
        {neural_info && (
          <div className="neural-info-box">
            <h4>⚡ Neural Information ({neural_info.model})</h4>
            <div className="neural-grid">
              <div><span>xG Home</span><strong>{neural_info.home_xG}</strong></div>
              <div><span>xG Away</span><strong>{neural_info.away_xG}</strong></div>
              {neural_info.dixon_coles_rho != null && (
                <div><span>Dixon-Coles ρ</span><strong>{neural_info.dixon_coles_rho?.toFixed(3)}</strong></div>
              )}
              <div>
                <span>Total xG</span>
                <strong>{(neural_info.home_xG + neural_info.away_xG).toFixed(2)}</strong>
              </div>
            </div>
          </div>
        )}

        {/* Markets */}
        <div className="markets-container">
          <MarketSection
            title="1X2 Match Odds"
            icon="🏆"
            probs={[
              { label: 'Home Win', value: prediction.home_prob, color: '#6366f1' },
              { label: 'Draw', value: prediction.draw_prob, color: '#f59e0b' },
              { label: 'Away Win', value: prediction.away_prob, color: '#10b981' },
            ]}
            breakdown={marketsData['1x2']?.model_breakdown}
          />

          {(prediction.over_25_prob != null) && (
            <MarketSection
              title="Over / Under 2.5"
              icon="⚽"
              probs={[
                { label: 'Over 2.5', value: prediction.over_25_prob, color: '#10b981' },
                { label: 'Under 2.5', value: prediction.under_25_prob, color: '#64748b' },
              ]}
              breakdown={marketsData['over_under']?.model_breakdown}
            />
          )}

          {(prediction.btts_prob != null) && (
            <MarketSection
              title="Both Teams to Score"
              icon="🎯"
              probs={[
                { label: 'BTTS Yes', value: prediction.btts_prob, color: '#ec4899' },
                { label: 'BTTS No', value: prediction.no_btts_prob, color: '#64748b' },
              ]}
              breakdown={marketsData['btts']?.model_breakdown}
            />
          )}
        </div>

        {/* Model Summary Table */}
        <div className="model-summary-section">
          <h4>📊 Model Summary</h4>
          <div className="model-summary-grid">
            {model_summary.models.map((m, i) => (
              <div
                key={i}
                className={`model-summary-row ${m.failed ? 'failed' : ''}`}
                style={{ borderLeft: `3px solid ${MODEL_TYPE_COLORS[m.type] || '#64748b'}` }}
              >
                <div className="msrow-name">
                  <span className="model-type-badge" style={{ background: MODEL_TYPE_COLORS[m.type] || '#64748b', fontSize: '0.7rem' }}>
                    {m.type}
                  </span>
                  <span>{m.name}</span>
                </div>
                <div className="msrow-ratings">
                  <span title="1X2 Confidence">1X2: <strong style={{ color: ratingColor((m.confidence_1x2 || 0.5) * 10) }}>{((m.confidence_1x2 || 0.5) * 10).toFixed(1)}</strong></span>
                  <span title="O/U Confidence">O/U: <strong style={{ color: ratingColor((m.confidence_ou || 0.5) * 10) }}>{((m.confidence_ou || 0.5) * 10).toFixed(1)}</strong></span>
                  <span title="BTTS Confidence">BTTS: <strong style={{ color: ratingColor((m.confidence_btts || 0.5) * 10) }}>{((m.confidence_btts || 0.5) * 10).toFixed(1)}</strong></span>
                </div>
                <div className="msrow-markets">
                  {(m.markets || []).map((mkt, mi) => (
                    <span key={mi} className="market-chip">{marketLabel(mkt)}</span>
                  ))}
                </div>
                {m.failed && <span className="failed-badge">FAILED</span>}
              </div>
            ))}
          </div>
        </div>

        {/* CLV Section */}
        {clv && (
          <div className="clv-section">
            <h4>📈 Closing Line Value</h4>
            <div className="detail-stats-row">
              <div className="stat-chip">
                <span className="stat-label">CLV</span>
                <span className="stat-value" style={{ color: (clv.clv || 0) > 0 ? '#10b981' : '#ef4444' }}>
                  {clv.clv != null ? `${(clv.clv * 100).toFixed(2)}%` : 'Pending'}
                </span>
              </div>
              <div className="stat-chip">
                <span className="stat-label">Closing Odds</span>
                <span className="stat-value">{clv.closing_odds?.toFixed(2) || 'Pending'}</span>
              </div>
              <div className="stat-chip">
                <span className="stat-label">Outcome</span>
                <span className="stat-value">{clv.bet_outcome?.toUpperCase() || 'Pending'}</span>
              </div>
              <div className="stat-chip">
                <span className="stat-label">Profit</span>
                <span className="stat-value" style={{ color: (clv.profit || 0) > 0 ? '#10b981' : '#ef4444' }}>
                  {clv.profit != null ? `${(clv.profit * 100).toFixed(2)}%` : 'Pending'}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
