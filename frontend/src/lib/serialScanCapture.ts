// A modal can temporarily consume codes from the existing serial reader without
// closing the port or sending those codes to the assembly behind the modal.
const captures: { receive: (code: string) => void }[] = []

export function captureSerialScans(receive: (code: string) => void) {
  const capture = { receive }
  captures.push(capture)
  return () => {
    const index = captures.indexOf(capture)
    if (index >= 0) captures.splice(index, 1)
  }
}

export function deliverCapturedSerialScan(code: string): boolean {
  const capture = captures[captures.length - 1]
  if (!capture) return false
  capture.receive(code)
  return true
}
