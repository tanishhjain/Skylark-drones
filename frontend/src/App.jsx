import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

const SUGGESTIONS = [
  "How's our pipeline looking for the renewables sector this quarter?",
  "What's our total amount receivable right now?",
  'Break down open deal value by sector',
  'Prepare a leadership update',
]

let idCounter = 0
function nextId() {
  idCounter += 1
  return idCounter
}

// ---- API helpers, matching main.py exactly ----

async function apiRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, options)
  if (!res.ok) {
    let detail
    try {
      const body = await res.json()
      detail = body.detail || JSON.stringify(body)
    } catch {
      detail = await res.text().catch(() => '')
    }
    throw new Error(detail || `Request failed with status ${res.status}`)
  }
  return res
}

// POST /chat  ->  body: { message, history }
// Response shape isn't fixed by main.py (it's whatever query_engine.answer_question()
// returns), so this checks the common field names and falls back to raw JSON rather
// than silently dropping content. If your backend uses a different key, update
// extractAnswerText below.
async function sendChatMessage(message, history) {
  const res = await apiRequest('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history }),
  })
  return res.json()
}

function extractAnswerText(data) {
  if (data == null) return ''
  if (typeof data === 'string') return data
  if (typeof data.answer === 'string') return data.answer
  if (typeof data.response === 'string') return data.response
  if (typeof data.message === 'string') return data.message
  if (typeof data.text === 'string') return data.text
  if (typeof data.result === 'string') return data.result
  return '```json\n' + JSON.stringify(data, null, 2) + '\n```'
}

// POST /refresh -> { status, deals_rows, work_orders_rows, fetched_at }
async function refreshData() {
  const res = await apiRequest('/refresh', { method: 'POST' })
  return res.json()
}

// GET /leadership-update -> PlainTextResponse (raw markdown string, not JSON)
async function fetchLeadershipUpdate() {
  const res = await apiRequest('/leadership-update', { method: 'GET' })
  return res.text()
}

// GET /health/monday -> { status, account } or throws
async function checkMondayHealth() {
  const res = await apiRequest('/health/monday', { method: 'GET' })
  return res.json()
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isGeneratingReport, setIsGeneratingReport] = useState(false)
  const [error, setError] = useState(null)
  const [mondayStatus, setMondayStatus] = useState('checking') // checking | connected | error
  const threadEndRef = useRef(null)

  useEffect(() => {
    checkMondayHealth()
      .then(() => setMondayStatus('connected'))
      .catch(() => setMondayStatus('error'))
  }, [])

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, isSending, isGeneratingReport])

  const isBusy = isSending || isRefreshing || isGeneratingReport

  function historyForApi(msgs) {
    // Backend expects [{ role: 'user'|'assistant', content: '...' }]
    return msgs
      .filter((m) => m.kind !== 'report')
      .map((m) => ({ role: m.role, content: m.content }))
  }

  async function handleSend(rawText) {
    const text = (rawText ?? input).trim()
    if (!text || isBusy) return

    const userMsg = { id: nextId(), role: 'user', content: text, kind: 'text' }
    const historySoFar = historyForApi(messages)

    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setError(null)
    setIsSending(true)

    try {
      const data = await sendChatMessage(text, historySoFar)
      const answerText = extractAnswerText(data)
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'assistant', content: answerText, kind: 'text' },
      ])
    } catch (err) {
      setError(err.message || 'Something went wrong talking to the agent.')
    } finally {
      setIsSending(false)
    }
  }

  async function handleRefresh() {
    if (isBusy) return
    setError(null)
    setIsRefreshing(true)
    try {
      const data = await refreshData()
      setMessages((prev) => [
        ...prev,
        {
          id: nextId(),
          role: 'assistant',
          kind: 'text',
          content: `Data refreshed from monday.com — **${data.deals_rows}** deals and **${data.work_orders_rows}** work orders loaded.`,
        },
      ])
    } catch (err) {
      setError(err.message || 'Could not refresh data from monday.com.')
    } finally {
      setIsRefreshing(false)
    }
  }

  async function handleGenerateReport() {
    if (isBusy) return
    setError(null)
    setIsGeneratingReport(true)
    try {
      const markdown = await fetchLeadershipUpdate()
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'assistant', kind: 'report', content: markdown },
      ])
    } catch (err) {
      setError(err.message || 'Could not generate the leadership update.')
    } finally {
      setIsGeneratingReport(false)
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="app-shell">
      <div className="app-panel">
        <header className="app-header">
          <div className="brand-row">
            <span
              className={`status-dot ${
                mondayStatus === 'connected' ? 'connected' : mondayStatus === 'error' ? 'error' : ''
              }`}
              title={
                mondayStatus === 'connected'
                  ? 'Connected to monday.com'
                  : mondayStatus === 'error'
                  ? 'Could not reach monday.com'
                  : 'Checking connection…'
              }
            />
            <h1 className="brand-title">Skylark BI Agent</h1>
          </div>
          <p className="brand-subtitle">Ask founder-level questions across the Deals and Work Orders boards.</p>
        </header>

        <div className="action-row">
          <button className="btn" onClick={handleRefresh} disabled={isBusy}>
            {isRefreshing ? <span className="spinner" /> : <span aria-hidden="true">↻</span>}
            <span className="label-full">Refresh data from monday.com</span>
          </button>
          <button className="btn" onClick={handleGenerateReport} disabled={isBusy}>
            {isGeneratingReport ? <span className="spinner" /> : <span aria-hidden="true">📋</span>}
            <span className="label-full">Generate leadership update</span>
          </button>
        </div>

        {error && (
          <div className="error-banner">
            <span>⚠ {error}</span>
            <button onClick={() => setError(null)} aria-label="Dismiss error">
              ×
            </button>
          </div>
        )}

        <div className="chat-thread">
          {messages.length === 0 && (
            <div className="welcome-card">
              Hi — I'm the Skylark BI agent. Ask me anything about our sales pipeline (Deals board) or
              project execution/billing (Work Orders board). I pull live data from monday.com, so give me a
              second on the first query.
            </div>
          )}

          {messages.map((m) => (
            <div key={m.id} className={`bubble-row ${m.role}`}>
              <div
                className={`bubble ${m.role}${m.kind === 'report' ? ' report' : ''}`}
              >
                {m.kind === 'report' && <div className="report-tag">Leadership update</div>}
                {m.role === 'assistant' ? (
                  <div className="md-content">
                    <ReactMarkdown>{m.content}</ReactMarkdown>
                  </div>
                ) : (
                  m.content
                )}
              </div>
            </div>
          ))}

          {(isSending || isGeneratingReport) && (
            <div className="bubble-row assistant">
              <div className="bubble assistant pending">
                <span className="spinner" />
                {isGeneratingReport ? 'Preparing leadership update…' : 'Thinking…'}
              </div>
            </div>
          )}

          <div ref={threadEndRef} />
        </div>

        <div className="chip-row">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              className="chip"
              onClick={() => (s === 'Prepare a leadership update' ? handleGenerateReport() : handleSend(s))}
              disabled={isBusy}
            >
              {s}
            </button>
          ))}
        </div>

        <div className="input-row">
          <textarea
            className="input-box"
            rows={1}
            placeholder="Ask a business question…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isBusy}
          />
          <button className="send-btn" onClick={() => handleSend()} disabled={isBusy || !input.trim()}>
            Send
          </button>
        </div>

        <p className="footer-disclaimer">AI-generated data may be inaccurate. Verify important information.</p>
      </div>
    </div>
  )
}
