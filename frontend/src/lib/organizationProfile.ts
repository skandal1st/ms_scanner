export const ORGANIZATION_PROFILE_KEY = 'organization_profile_id'

export function getOrganizationProfileId(): string | null {
  return localStorage.getItem(ORGANIZATION_PROFILE_KEY)
}

export function setOrganizationProfileId(id: string | null | undefined): void {
  if (id) localStorage.setItem(ORGANIZATION_PROFILE_KEY, id)
  else localStorage.removeItem(ORGANIZATION_PROFILE_KEY)
}
