import type { Workplace, OrganizationProfile, Document } from '../api/client'

export function documentWorkplaces(profiles: OrganizationProfile[], doc: Document): Workplace[] {
  const workplaces = profiles.find(profile => profile.id === doc.organization_profile_id)?.workplaces
    .filter(workplace => workplace.is_active) ?? []
  const specific = workplaces.filter(workplace => Boolean(doc.moysklad_store_id) && workplace.store_ids.includes(doc.moysklad_store_id!))
  return specific.length ? specific : workplaces.filter(workplace => workplace.store_ids.length === 0)
}

export function preferredWorkplace(workplaces: Workplace[]): Workplace | undefined {
  return workplaces.length === 1 ? workplaces[0] : workplaces.find(workplace => workplace.is_default)
}
