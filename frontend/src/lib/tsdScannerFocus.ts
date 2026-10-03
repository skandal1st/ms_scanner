/** Restore Android IME focus and recover keyboard-wedge input after navigation. */
export function bindTsdScannerFocus(options: {
  input: () => HTMLInputElement | null
  blocked: () => boolean
  append: (value: string) => void
}) {
  let timer: number | undefined
  const otherEditor = (target: EventTarget | null) => target instanceof HTMLElement
    && target !== options.input()
    && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))
  const focus = () => {
    if (!options.blocked() && document.visibilityState === 'visible'
      && !otherEditor(document.activeElement)) options.input()?.focus({ preventScroll: true })
  }
  const recover = () => {
    window.clearTimeout(timer)
    timer = window.setTimeout(() => {
      // Do not destroy a code selection while the operator copies it.
      if (!window.getSelection()?.toString()) focus()
    }, 0)
  }
  const keydown = (event: KeyboardEvent) => {
    if (options.blocked() || event.defaultPrevented || event.ctrlKey || event.altKey || event.metaKey
      || event.isComposing || otherEditor(event.target) || document.activeElement === options.input()) return
    if (event.key.length === 1 && event.key !== ' ') {
      focus()
      event.preventDefault()
      options.append(event.key)
    } else if (event.key === 'Unidentified') focus()
  }
  const paste = (event: ClipboardEvent) => {
    if (options.blocked() || otherEditor(event.target) || document.activeElement === options.input()) return
    const text = event.clipboardData?.getData('text')
    if (text) { focus(); event.preventDefault(); options.append(text) }
  }
  const focusout = (event: FocusEvent) => {
    if (event.target === options.input() && !event.relatedTarget) recover()
  }
  window.addEventListener('pointerup', recover)
  window.addEventListener('focus', recover)
  document.addEventListener('visibilitychange', recover)
  document.addEventListener('focusout', focusout)
  window.addEventListener('keydown', keydown, true)
  window.addEventListener('paste', paste, true)
  focus()
  return () => {
    window.clearTimeout(timer)
    window.removeEventListener('pointerup', recover)
    window.removeEventListener('focus', recover)
    document.removeEventListener('visibilitychange', recover)
    document.removeEventListener('focusout', focusout)
    window.removeEventListener('keydown', keydown, true)
    window.removeEventListener('paste', paste, true)
  }
}
