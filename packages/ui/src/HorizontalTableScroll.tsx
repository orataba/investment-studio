import { useCallback, useEffect, useState, type HTMLAttributes, type Ref } from 'react'
import { useHorizontalTablePan } from './useHorizontalTablePan'
import './horizontal-table-scroll.css'

/** A scroll container, not a business table. Native touch and wheel scrolling
 * remain intact; mouse panning activates only when the table overflows. */
export default function HorizontalTableScroll({ children, className = '', tabIndex, ref: forwardedRef, ...props }: HTMLAttributes<HTMLDivElement> & { ref?: Ref<HTMLDivElement> }) {
  const { ref, handlers, isPanning } = useHorizontalTablePan()
  const attachRef = useCallback((node: HTMLDivElement | null) => {
    ref.current = node
    if (typeof forwardedRef === 'function') return forwardedRef(node)
    if (forwardedRef) forwardedRef.current = node
  }, [forwardedRef, ref])
  const [overflows, setOverflows] = useState(false)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const measure = () => setOverflows(node.scrollWidth > node.clientWidth)
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    for (const child of node.children) observer.observe(child)
    return () => observer.disconnect()
  }, [children, ref])
  return <div {...props} {...handlers} ref={attachRef}
    className={`horizontal-table-scroll ${className}`.trim()}
    data-table-overflow={overflows || undefined}
    data-table-panning={isPanning || undefined}
    tabIndex={tabIndex ?? (overflows ? 0 : undefined)}>{children}</div>
}
