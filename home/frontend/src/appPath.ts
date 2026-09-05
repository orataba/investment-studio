const appBasePath = import.meta.env.BASE_URL.replace(/\/$/, '')

export function appPath(path: string) {
  const absolutePath = path.startsWith('/') ? path : `/${path}`
  return `${appBasePath}${absolutePath}` || '/'
}

export function stripAppBasePath(pathname: string) {
  if (!appBasePath) return pathname
  if (pathname === appBasePath) return '/'
  if (pathname.startsWith(`${appBasePath}/`)) {
    return pathname.slice(appBasePath.length)
  }
  return pathname
}
