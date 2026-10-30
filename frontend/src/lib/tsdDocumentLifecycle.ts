import axios from 'axios'

// These GET responses mean the saved shipment can no longer be collected here.
// Network failures, authentication renewal and server errors must keep the selection.
export function isTsdDocumentClosed(error: unknown): boolean {
  return axios.isAxiosError(error) && (error.response?.status === 404 || error.response?.status === 409)
}

export function retryTsdDocument(failureCount: number, error: unknown): boolean {
  return !isTsdDocumentClosed(error) && failureCount < 1
}
