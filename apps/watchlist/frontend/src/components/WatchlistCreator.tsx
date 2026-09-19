import type { WatchlistRecord } from '../lib/api'

export function watchlistCreatorLabel(watchlist: WatchlistRecord, zh: boolean) {
  if (watchlist.owner_type === 'system') return zh ? '系统列表' : 'System list'
  const name = watchlist.created_by_display_name
  return name ? `${zh ? '创建人' : 'Created by'} ${name}` : (zh ? '创建人未记录' : 'Creator not recorded')
}

export default function WatchlistCreator({ watchlist, zh }: { watchlist: WatchlistRecord; zh: boolean }) {
  return <span className="watchlist-creator" translate="no">{watchlistCreatorLabel(watchlist, zh)}</span>
}
