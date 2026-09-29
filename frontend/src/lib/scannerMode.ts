const STORAGE_KEY = 'scanner_mode'

/**
 * Миграция прежней настройки «USB-клавиатура / COM».
 * Сканирование маркировки теперь всегда работает через Web Serial; значение
 * сохраняем для совместимости с уже открытыми вкладками старой версии приложения.
 */
export function enforceComScannerMode(): void {
  if (typeof window !== 'undefined' && localStorage.getItem(STORAGE_KEY) !== 'com') {
    localStorage.setItem(STORAGE_KEY, 'com')
  }
}

export function isWebSerialSupported(): boolean {
  return typeof navigator !== 'undefined' && 'serial' in navigator
}
