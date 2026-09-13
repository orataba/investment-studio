import { useRef, useState } from 'react'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import { renameWatchlist, type WatchlistRecord } from '../lib/api'

export default function RenameWatchlistDialog({ watchlist, onSaved, onCancel }: {
  watchlist: WatchlistRecord
  onSaved: (record: WatchlistRecord) => void
  onCancel: () => void
}) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [name, setName] = useState(watchlist.name)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const nameRef = useRef<HTMLInputElement>(null)
  const close = () => { if (!saving) onCancel() }
  const dialogRef = useModalDialog(true, close, nameRef)
  async function save() {
    if (saving) return
    const trimmed = name.trim()
    if (!trimmed || trimmed.length > 200) {
      setError(zh ? '名称不能为空，且不能超过 200 个字符。' : 'Enter a name of 1–200 characters.')
      return
    }
    if (trimmed === watchlist.name) { onCancel(); return }
    setSaving(true)
    setError('')
    try {
      onSaved(await renameWatchlist(watchlist.watchlist_id, trimmed))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : zh ? '重命名失败，请重试。' : 'Failed to rename watchlist. Please try again.')
      setSaving(false)
    }
  }
  return <div className="watchlists-modal-backdrop" onClick={close}>
    <div ref={dialogRef} className="watchlists-modal watchlists-compact-modal" role="dialog"
      aria-modal="true" aria-labelledby="rename-watchlist-title" tabIndex={-1}
      onClick={(event) => event.stopPropagation()}>
      <form onSubmit={(event) => { event.preventDefault(); void save() }}>
      <div className="watchlists-modal-header">
        <div id="rename-watchlist-title" className="panel-title">{zh ? '重命名关注列表' : 'Rename Watchlist'}</div>
        <button type="button" disabled={saving} onClick={close}>{zh ? '关闭' : 'Close'}</button>
      </div>
      <div className="watchlists-modal-body">
        {error && <div className="error-state" role="alert">{error}</div>}
        <label className="form-field"><span>{zh ? '名称' : 'Name'}</span>
          <input ref={nameRef} className="form-input" value={name} maxLength={200} disabled={saving}
            onChange={(event) => setName(event.target.value)} />
        </label>
      </div>
      <div className="watchlists-modal-actions watchlists-modal-actions-sticky">
        <button type="button" disabled={saving} onClick={close}>{zh ? '取消' : 'Cancel'}</button>
        <button type="submit" className="button-primary" disabled={saving || !name.trim()}>
          {saving ? zh ? '保存中…' : 'Saving…' : zh ? '保存' : 'Save'}
        </button>
      </div>
      </form>
    </div>
  </div>
}
