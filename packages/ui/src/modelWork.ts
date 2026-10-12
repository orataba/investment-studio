/** Authoritative paid-work capability; absence does not override endpoint permission. */
export type ModelWorkStatus = { paused: boolean; code: string | null; message: string | null }
