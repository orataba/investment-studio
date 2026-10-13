import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { buildPortfolioSectionPath } from '../lib/navigation'

type PortfolioOption = { portfolio_id: string; portfolio_name: string }

export default function PortfolioSwitcher({ portfolios, currentId }: { portfolios: PortfolioOption[]; currentId: string }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const shell = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()
  const current = portfolios.find((item) => item.portfolio_id === currentId)
  const matches = portfolios.filter((item) => `${item.portfolio_name} ${item.portfolio_id}`.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()))

  useEffect(() => { setOpen(false); setQuery('') }, [currentId])
  useEffect(() => {
    if (!open) return
    input.current?.focus()
    function outside(event: Event) {
      if (!shell.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('focusin', outside)
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('focusin', outside)
    }
  }, [open])
  useEffect(() => {
    if (open) document.getElementById(`${listId}-${active}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [open, active, listId])

  function select(item: PortfolioOption) {
    setOpen(false)
    trigger.current?.focus()
    navigate(buildPortfolioSectionPath(item.portfolio_id, '/overview'))
  }

  return <div className="portfolio-switcher" ref={shell} onKeyDown={(event) => {
    if (event.key === 'Escape' && open) {
      event.preventDefault()
      setOpen(false)
      trigger.current?.focus()
    }
  }}>
    <button ref={trigger} type="button" className="portfolio-switcher-trigger" aria-expanded={open} aria-haspopup="listbox"
      aria-label={`${zh ? '切换组合' : 'Switch portfolio'}: ${current?.portfolio_name ?? currentId}`} title={current?.portfolio_name}
      onClick={() => { setQuery(''); setActive(Math.max(0, portfolios.findIndex((item) => item.portfolio_id === currentId))); setOpen((value) => !value) }}>
      <span translate="no">{current?.portfolio_name ?? currentId}</span><span aria-hidden="true">⌄</span>
    </button>
    {open && <div className="portfolio-switcher-popover">
      <input ref={input} role="combobox" aria-label={zh ? '搜索组合' : 'Search portfolios'} aria-expanded="true" aria-controls={listId}
        aria-autocomplete="list" aria-activedescendant={matches[active] ? `${listId}-${active}` : undefined}
        placeholder={zh ? '搜索组合名称或编号' : 'Search name or ID'} value={query}
        onChange={(event) => { setQuery(event.target.value); setActive(0) }}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault()
            setActive((index) => Math.max(0, Math.min(matches.length - 1, index + (event.key === 'ArrowDown' ? 1 : -1))))
          } else if (event.key === 'Enter' && matches[active]) {
            event.preventDefault()
            select(matches[active])
          }
        }} />
      <div id={listId} role="listbox" aria-label={zh ? '可访问的组合' : 'Accessible portfolios'} className="portfolio-switcher-options">
        {matches.map((item, index) => <button type="button" role="option" tabIndex={-1} id={`${listId}-${index}`} key={item.portfolio_id}
          aria-selected={item.portfolio_id === currentId} className={index === active ? 'portfolio-switcher-option-active' : ''}
          onMouseEnter={() => setActive(index)} onClick={() => select(item)}>
          <span translate="no">{item.portfolio_name}</span>{' '}<small translate="no">{item.portfolio_id}{item.portfolio_id === currentId ? (zh ? ' · 当前' : ' · Current') : ''}</small>
        </button>)}
      </div>
      {!matches.length && <p role="status">{zh ? '没有匹配的组合' : 'No matching portfolios'}</p>}
    </div>}
  </div>
}
