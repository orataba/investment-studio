#!/usr/bin/env node
import { createReadStream, existsSync, statSync } from 'node:fs'
import { createServer, request as httpRequest } from 'node:http'
import { extname, join, normalize, resolve, sep } from 'node:path'
import { URL } from 'node:url'

const args = new Map()
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1])
}

const distRoot = resolve(args.get('--dist') || 'dist')
const port = Number(args.get('--port') || process.env.PORT || 3100)
const host = args.get('--host') || '0.0.0.0'
const apiTarget = new URL(args.get('--api-target') || 'http://127.0.0.1:8000')
const indexPath = join(distRoot, 'index.html')

const contentTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'],
  ['.ico', 'image/x-icon'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
])

function sendFile(response, filePath) {
  const type = contentTypes.get(extname(filePath).toLowerCase()) || 'application/octet-stream'
  response.writeHead(200, { 'Content-Type': type })
  createReadStream(filePath).pipe(response)
}

function resolveStaticPath(pathname) {
  const decoded = decodeURIComponent(pathname.split('?')[0])
  const normalized = normalize(decoded).replace(/^([.][.][/\\])+/, '')
  const candidate = resolve(join(distRoot, normalized))
  if (candidate !== distRoot && !candidate.startsWith(distRoot + sep)) {
    return indexPath
  }
  if (existsSync(candidate) && statSync(candidate).isFile()) {
    return candidate
  }
  return indexPath
}

function proxyApi(clientRequest, clientResponse) {
  const targetUrl = new URL(clientRequest.url, apiTarget)
  const headers = { ...clientRequest.headers, host: apiTarget.host }
  const upstream = httpRequest(
    {
      protocol: apiTarget.protocol,
      hostname: apiTarget.hostname,
      port: apiTarget.port,
      method: clientRequest.method,
      path: `${targetUrl.pathname}${targetUrl.search}`,
      headers,
    },
    (upstreamResponse) => {
      clientResponse.writeHead(upstreamResponse.statusCode || 502, upstreamResponse.headers)
      upstreamResponse.pipe(clientResponse)
    },
  )
  upstream.on('error', (error) => {
    clientResponse.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
    clientResponse.end(`API proxy failed: ${error.message}`)
  })
  clientRequest.pipe(upstream)
}

createServer((request, response) => {
  if (request.url?.startsWith('/api')) {
    proxyApi(request, response)
    return
  }
  sendFile(response, resolveStaticPath(request.url || '/'))
}).listen(port, host, () => {
  console.log(`Serving ${distRoot} on http://${host}:${port}, proxying /api to ${apiTarget.href}`)
})
