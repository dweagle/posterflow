// Remembers the user's "add subscribed drive to Drive Priority?" choice per drive type,
// so the popup can be skipped once they pick "Don't ask again".
export type PriorityScope = 'poster' | 'artwork'
export type PriorityPref = 'ask' | 'always' | 'never'

const key = (scope: PriorityScope) => `posterflow.subscribeAddToPriority.${scope}`

export const getAddToPriorityPref = (scope: PriorityScope): PriorityPref => {
  const value = localStorage.getItem(key(scope))
  return value === 'always' || value === 'never' ? value : 'ask'
}

export const setAddToPriorityPref = (scope: PriorityScope, pref: PriorityPref): void => {
  localStorage.setItem(key(scope), pref)
}
