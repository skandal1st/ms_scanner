import { useEffect, useId, useState, type KeyboardEvent, type ReactNode } from 'react'

export type FlowWorkspaceTab = 'summary' | 'marks'

interface FlowWorkspaceTabsProps {
  summaryLabel: string
  summaryCount?: string | number
  marksCount: number
  issueCount?: number
  pendingCount?: number
  active: FlowWorkspaceTab
  onChange: (tab: FlowWorkspaceTab) => void
  summary: ReactNode
  marks: ReactNode
}

export function useFlowWorkspaceTab(storageKey: string) {
  const [active, setActive] = useState<FlowWorkspaceTab>(() =>
    sessionStorage.getItem(storageKey) === 'marks' ? 'marks' : 'summary',
  )

  useEffect(() => {
    sessionStorage.setItem(storageKey, active)
  }, [active, storageKey])

  return [active, setActive] as const
}

export function FlowWorkspaceTabs({
  summaryLabel,
  summaryCount,
  marksCount,
  issueCount = 0,
  pendingCount = 0,
  active,
  onChange,
  summary,
  marks,
}: FlowWorkspaceTabsProps) {
  const baseId = useId()
  const tabs: FlowWorkspaceTab[] = ['summary', 'marks']

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, current: FlowWorkspaceTab) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const next = current === 'summary' ? 'marks' : 'summary'
    onChange(next)
    document.getElementById(`${baseId}-${next}-tab`)?.focus()
  }

  return (
    <section className="flow-tabs">
      <div className="flow-tabs__list" role="tablist" aria-label="Рабочая область документа">
        {tabs.map((tab) => {
          const selected = active === tab
          const isSummary = tab === 'summary'
          return (
            <button
              key={tab}
              id={`${baseId}-${tab}-tab`}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={`${baseId}-${tab}-panel`}
              tabIndex={selected ? 0 : -1}
              className={`flow-tabs__tab${selected ? ' flow-tabs__tab--active' : ''}`}
              onClick={() => onChange(tab)}
              onKeyDown={(event) => handleKeyDown(event, tab)}
            >
              <span>{isSummary ? summaryLabel : 'Марки'}</span>
              <span className="flow-tabs__count">
                {isSummary ? summaryCount ?? 0 : marksCount}
              </span>
              {!isSummary && pendingCount > 0 && (
                <span className="flow-tabs__badge flow-tabs__badge--pending" title="Ожидают проверки">
                  {pendingCount}
                </span>
              )}
              {!isSummary && issueCount > 0 && (
                <span className="flow-tabs__badge flow-tabs__badge--issue" title="Требуют внимания">
                  {issueCount}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <div
        id={`${baseId}-summary-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-summary-tab`}
        className="flow-tabs__panel"
        hidden={active !== 'summary'}
      >
        {summary}
      </div>
      <div
        id={`${baseId}-marks-panel`}
        role="tabpanel"
        aria-labelledby={`${baseId}-marks-tab`}
        className="flow-tabs__panel"
        hidden={active !== 'marks'}
      >
        {marks}
      </div>
    </section>
  )
}
