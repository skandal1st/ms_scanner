function statusColors(color?: number | null) {
  if (typeof color !== 'number' || !Number.isInteger(color) || color < 0 || color > 0xffffff) return undefined
  const red = (color >> 16) & 255
  const green = (color >> 8) & 255
  const blue = color & 255
  const linear = [red, green, blue].map(value => {
    const channel = value / 255
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  })
  const luminance = linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
  return { backgroundColor: `#${color.toString(16).padStart(6, '0')}`, color: luminance > 0.179 ? '#111827' : '#ffffff' }
}

export function OrderStatusBadge({ name, color }: { name?: string | null; color?: number | null }) {
  return <span className="order-status-badge" style={name ? statusColors(color) : undefined}>{name || 'Статус не указан'}</span>
}
