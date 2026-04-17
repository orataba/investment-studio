import React, { useEffect, useRef, useState } from 'react'

export type CopilotCitation = {
  label: string
  ref_type: string
  ref_id?: string | null
  note?: string | null
}

export type CopilotMessage = {
  role: 'assistant' | 'user'
  content: string
  citations?: CopilotCitation[]
  suggestions?: string[]
  generatedAt?: string
}

type CopilotDrawerProps = {
  open: boolean
  title: string
  subtitle?: string
  loading?: boolean
  messages: CopilotMessage[]
  placeholder?: string
  onClose: () => void
  onSend: (message: string) => void
}

export default function CopilotDrawer({
  open,
  title,
  subtitle,
  loading = false,
  messages,
  placeholder = 'Ask Copilot...',
  onClose,
  onSend,
}: CopilotDrawerProps) {
  const [draft, setDraft] = useState('')
  const bodyRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) {
      setDraft('')
      return
    }
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight, behavior: 'smooth' })
  }, [open, messages, loading])

  if (!open) {
    return null
  }

  return (
    <div className="copilot-drawer-shell">
      <button type="button" className="copilot-drawer-backdrop" aria-label="Close Copilot" onClick={onClose} />
      <aside className="copilot-drawer" aria-label={title}>
        <div className="copilot-drawer-header">
          <div>
            <div className="panel-title">AI Copilot</div>
            <div className="copilot-drawer-title">{title}</div>
            {subtitle ? <div className="copilot-drawer-subtitle">{subtitle}</div> : null}
          </div>
          <button type="button" className="copilot-drawer-close" onClick={onClose}>
            ×
          </button>
        </div>

        <div ref={bodyRef} className="copilot-drawer-body">
          {messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              className={
                message.role === 'user'
                  ? 'copilot-message copilot-message-user'
                  : 'copilot-message copilot-message-assistant'
              }
            >
              <div className="copilot-message-role">{message.role === 'user' ? 'You' : 'Copilot'}</div>
              <div className="copilot-message-content">{message.content}</div>
              {message.citations?.length ? (
                <div className="copilot-citation-list">
                  {message.citations.map((citation, citationIndex) => (
                    <div key={`${citation.label}-${citationIndex}`} className="copilot-citation">
                      <strong>{citation.label}</strong>
                      {citation.note ? <span>{citation.note}</span> : null}
                    </div>
                  ))}
                </div>
              ) : null}
              {message.suggestions?.length ? (
                <div className="copilot-suggestion-list">
                  {message.suggestions.map((suggestion) => (
                    <button
                      key={suggestion}
                      type="button"
                      className="copilot-suggestion"
                      onClick={() => onSend(suggestion)}
                    >
                      {suggestion}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
          {loading ? <div className="copilot-message copilot-message-assistant">Copilot is thinking...</div> : null}
        </div>

        <form
          className="copilot-drawer-composer"
          onSubmit={(event) => {
            event.preventDefault()
            const value = draft.trim()
            if (!value || loading) {
              return
            }
            onSend(value)
            setDraft('')
          }}
        >
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={placeholder}
            rows={3}
          />
          <button type="submit" className="button-primary" disabled={loading || !draft.trim()}>
            Send
          </button>
        </form>
      </aside>
    </div>
  )
}
