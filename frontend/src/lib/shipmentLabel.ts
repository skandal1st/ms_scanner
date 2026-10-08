export function shipmentLabel(document: { name: string; customer_order_name?: string | null; agent_name?: string | null }): string {
  return [document.customer_order_name ? `(${document.customer_order_name})` : '', document.name || 'Без номера', document.agent_name || ''].filter(Boolean).join(' ')
}
