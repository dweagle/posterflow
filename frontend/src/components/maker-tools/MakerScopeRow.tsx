import type { ReactNode } from 'react'

type Props = {
  /** The page's own sub-tabs or filters, kept on the left. */
  children?: ReactNode
  /** Scope picker (IDarr drive / artwork scope) or its fallback, on the right. */
  control?: ReactNode
}

/** Sticky row above a card list, like the Requests page header: stays put while the list scrolls. */
export default function MakerScopeRow({ children, control }: Props) {
  return (
    <div className="maker-scope-row">
      <div className="maker-scope-row-main">{children}</div>
      {control && <div className="maker-scope-row-control">{control}</div>}
    </div>
  )
}
