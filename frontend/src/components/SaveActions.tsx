import { Save, X } from 'lucide-react'

// The save pair every settings page uses: Save turns red while there are unsaved edits, reads
// Saved and sleeps when there is nothing to save, and Cancel beside it throws the edits
// away. The label never changes width: all three texts share one cell.
//
//   <SaveActions dirty={hasUnsaved} saving={saving} onSave={save} onCancel={revert} />

const TEXTS = ['Save', 'Saving…', 'Saved'] as const

interface SaveActionsProps {
  dirty: boolean
  saving?: boolean
  disabled?: boolean
  onSave: () => void
  onCancel?: () => void
  className?: string
}

function SaveActions({ dirty, saving = false, disabled = false, onSave, onCancel, className }: SaveActionsProps) {
  const cls = ['btn-toolbar', dirty ? 'btn-unsaved' : 'btn-primary', className].filter(Boolean).join(' ')
  const current = saving ? 'Saving…' : dirty ? 'Save' : 'Saved'
  return (
    <span className="save-actions">
      <button
        type="button"
        className={cls}
        onClick={onSave}
        disabled={disabled || saving || !dirty}
        title={dirty ? 'Save the edits on this page' : 'Everything here is saved'}
      >
        <Save size={14} />
        <span className="btn-label-stack">
          {TEXTS.map((text) => (
            <span key={text} aria-hidden={text !== current ? 'true' : undefined}>{text}</span>
          ))}
        </span>
      </button>
      {onCancel && (
        <button
          type="button"
          className="btn-toolbar btn-toolbar-link"
          onClick={onCancel}
          disabled={saving || !dirty}
          title="Throw away the unsaved edits and go back to the saved settings"
        >
          <X size={14} /> Cancel
        </button>
      )}
    </span>
  )
}

export default SaveActions
