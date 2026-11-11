import { X } from 'lucide-react'
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  type CSSProperties,
  type MouseEvent,
  type ReactNode,
  type Ref,
} from 'react'

// The one dialog frame. Markup composes it:
//
//   <Modal onClose={close} size="sm" className="my-modal">
//     <Modal.Header title="Title" />
//     <Modal.Body>…</Modal.Body>
//     <Modal.Footer>…buttons…</Modal.Footer>
//   </Modal>
//
// `onClose` wires the × button, Escape and a click on the dimmed overlay; leave it out for a
// dialog the user must answer. `busy` keeps the dialog open while a request runs.

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl'

type ModalContextValue = { onClose?: () => void; busy: boolean; titleId: string }

const ModalContext = createContext<ModalContextValue>({ busy: false, titleId: '' })

// Open modals, outermost first. Escape closes only the one on top.
const openStack: symbol[] = []

interface ModalProps {
  onClose?: () => void
  size?: ModalSize
  className?: string
  overlayClassName?: string
  style?: CSSProperties
  busy?: boolean
  closeOnOverlay?: boolean
  closeOnEscape?: boolean
  children: ReactNode
}

function ModalRoot({
  onClose,
  size = 'md',
  className,
  overlayClassName,
  style,
  busy = false,
  closeOnOverlay = true,
  closeOnEscape = true,
  children,
}: ModalProps) {
  const titleId = useId()
  const self = useRef(Symbol('modal'))
  const contentRef = useRef<HTMLDivElement>(null)
  const pressedOverlay = useRef(false)

  useEffect(() => {
    const me = self.current
    openStack.push(me)
    return () => {
      const at = openStack.indexOf(me)
      if (at >= 0) openStack.splice(at, 1)
    }
  }, [])

  useEffect(() => {
    if (!onClose || !closeOnEscape || busy) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (openStack[openStack.length - 1] !== self.current) return
      // Decided after every other listener ran: a lightbox or picker stacked on the dialog
      // consumes Escape with preventDefault() and the dialog stays open.
      queueMicrotask(() => {
        if (!event.defaultPrevented) onClose()
      })
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose, closeOnEscape, busy])

  useEffect(() => {
    // Keyboard focus starts inside the dialog unless a field already took it (autoFocus).
    const el = contentRef.current
    if (el && !el.contains(document.activeElement)) el.focus()
  }, [])

  const dismiss = onClose && closeOnOverlay && !busy ? onClose : undefined
  const onOverlayMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    pressedOverlay.current = event.target === event.currentTarget
  }
  const onOverlayClick = (event: MouseEvent<HTMLDivElement>) => {
    // Both the press and the release on the overlay itself: a drag that starts on a field and
    // ends outside the card must not close the dialog.
    if (dismiss && pressedOverlay.current && event.target === event.currentTarget) dismiss()
    pressedOverlay.current = false
  }

  const classes = ['modal-content', size !== 'md' ? `modal-${size}` : '', className].filter(Boolean).join(' ')

  return (
    <ModalContext.Provider value={{ onClose, busy, titleId }}>
      <div
        className={overlayClassName ? `modal-overlay ${overlayClassName}` : 'modal-overlay'}
        onMouseDown={onOverlayMouseDown}
        onClick={onOverlayClick}
      >
        <div
          ref={contentRef}
          className={classes}
          style={style}
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
          tabIndex={-1}
        >
          {children}
        </div>
      </div>
    </ModalContext.Provider>
  )
}

interface ModalHeaderProps {
  title: ReactNode
  className?: string
  children?: ReactNode
}

// The title and the × button; `children` sit between them (badges, tabs, a subtitle line).
function ModalHeader({ title, className, children }: ModalHeaderProps) {
  const { onClose, busy, titleId } = useContext(ModalContext)
  return (
    <div className={className ? `modal-header ${className}` : 'modal-header'}>
      <h2 id={titleId}>{title}</h2>
      {children}
      {onClose && (
        <button type="button" className="modal-close" onClick={onClose} disabled={busy} aria-label="Close">
          <X size={18} />
        </button>
      )}
    </div>
  )
}

interface ModalSectionProps {
  className?: string
  ref?: Ref<HTMLDivElement>
  children?: ReactNode
}

function ModalBody({ className, ref, children }: ModalSectionProps) {
  return (
    <div ref={ref} className={className ? `modal-body ${className}` : 'modal-body'}>
      {children}
    </div>
  )
}

function ModalFooter({ className, ref, children }: ModalSectionProps) {
  return (
    <div ref={ref} className={className ? `modal-footer ${className}` : 'modal-footer'}>
      {children}
    </div>
  )
}

const Modal = Object.assign(ModalRoot, { Header: ModalHeader, Body: ModalBody, Footer: ModalFooter })

export default Modal
