/**
 * Разобрать вставленный/загруженный список марок в массив кодов.
 *
 * Разделители — перевод строки, табуляция и запятая (коды из письма/CSV в один
 * столбец). Пробел разделителем НЕ считаем: в самих КМ (Data Matrix) встречается
 * группа-разделитель, но обычного пробела там нет, а строки из Excel/письма
 * разделяются переносами. Пустые строки отбрасываем, дубли схлопываем с
 * сохранением порядка.
 */
export function parseCodeList(text: string): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const part of text.split(/[\r\n\t,]+/)) {
    const code = part.trim()
    if (!code || seen.has(code)) continue
    seen.add(code)
    out.push(code)
  }
  return out
}
