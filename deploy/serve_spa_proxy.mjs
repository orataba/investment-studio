#!/usr/bin/env node
import { createReadStream, statSync } from 'node:fs'
import { createServer, request as httpRequest } from 'node:http'
import { randomUUID } from 'node:crypto'
import { extname, join, normalize, resolve, sep } from 'node:path'
import { URL } from 'node:url'

const args = new Map()
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1])
}

const distRoot = resolve(args.get('--dist') || 'dist')
const port = Number(args.get('--port') || process.env.PORT || 3100)
const host = args.get('--host') || '127.0.0.1'
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

function sendFile(request, response, filePath) {
  const type = contentTypes.get(extname(filePath).toLowerCase()) || 'application/octet-stream'
  const metadata = statSync(filePath, { throwIfNoEntry: false })
  if (!metadata) {
    response.writeHead(404)
    response.end('File unavailable')
    return
  }
  // Vite filenames identify immutable content. HTML must always revalidate.
  const immutable = filePath.startsWith(join(distRoot, 'assets') + sep)
  const etag = `W/"${metadata.size.toString(16)}-${Math.trunc(metadata.mtimeMs).toString(16)}"`
  const headers = { 'Content-Type': type, ETag: etag,
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache' }
  if (request.headers['if-none-match'] === etag) {
    response.writeHead(304, headers)
    response.end()
    return
  }
  headers['Content-Length'] = metadata.size
  if (request.method === 'HEAD') {
    response.writeHead(200, headers)
    response.end()
    return
  }
  const stream = createReadStream(filePath)
  stream.on('open', () => {
    response.writeHead(200, headers)
    stream.pipe(response)
  })
  stream.on('error', () => {
    if (response.headersSent) {
      response.destroy()
    } else {
      response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
      response.end('File unavailable')
    }
  })
  response.on('close', () => stream.destroy())
}

function resolveStaticPath(pathname) {
  let decoded
  try {
    decoded = decodeURIComponent(pathname.split('?')[0])
  } catch {
    return null
  }
  if (decoded.includes('\0')) return null
  const normalized = normalize(decoded).replace(/^([.][.][/\\])+/, '')
  const candidate = resolve(join(distRoot, normalized))
  if (candidate !== distRoot && !candidate.startsWith(distRoot + sep)) {
    return indexPath
  }
  try {
    if (statSync(candidate, { throwIfNoEntry: false })?.isFile()) {
      return candidate
    }
  } catch (error) {
    if (error.code === 'ENAMETOOLONG' || error.code === 'EINVAL') return null
    throw error
  }
  if (decoded.startsWith('/assets/')) return false
  return indexPath
}

function proxyApi(clientRequest, clientResponse) {
  const targetUrl = new URL(clientRequest.url, apiTarget)
  // Preserve the browser destination and actual peer for local-owner checks.
  // Replacing Host with loopback would let DNS rebinding appear local upstream.
  const forwarded = clientRequest.headers['x-forwarded-for']
  const headers = {
    ...clientRequest.headers,
    'x-forwarded-for': [forwarded, clientRequest.socket.remoteAddress].filter(Boolean).join(', '),
  }
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
      upstreamResponse.on('aborted', () => clientResponse.destroy())
      upstreamResponse.on('error', () => clientResponse.destroy())
      upstreamResponse.pipe(clientResponse)
    },
  )
  upstream.on('error', (error) => {
    if (clientResponse.headersSent) {
      clientResponse.destroy(error)
    } else {
      clientResponse.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
      clientResponse.end('API temporarily unavailable')
    }
  })
  clientRequest.on('aborted', () => upstream.destroy())
  clientResponse.on('close', () => upstream.destroy())
  clientRequest.pipe(upstream)
}

createServer((request, response) => {
  const started = performance.now()
  const suppliedId = request.headers['x-request-id']
  const requestId = typeof suppliedId === 'string' && /^[a-zA-Z0-9_-]{8,64}$/.test(suppliedId) ? suppliedId : randomUUID()
  request.headers['x-request-id'] = requestId
  response.setHeader('X-Request-ID', requestId)
  response.on('close', () => {
    console.log(JSON.stringify({ timestamp: new Date().toISOString(), event: 'web_request',
      request_id: requestId, method: request.method, path: (request.url || '/').split('?')[0],
      status: response.statusCode, duration_ms: Math.round((performance.now() - started) * 100) / 100,
      completed: response.writableFinished }))
  })
  if (request.url === '/api' || request.url?.startsWith('/api/') || request.url?.startsWith('/api?')) {
    proxyApi(request, response)
    return
  }
  let staticPath
  try {
    staticPath = resolveStaticPath(request.url || '/')
  } catch {
    response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end('File temporarily unavailable')
    return
  }
  if (staticPath === null) {
    response.writeHead(400, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end('Malformed request path')
    return
  }
  if (staticPath === false) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    response.end('File unavailable')
    return
  }
  sendFile(request, response, staticPath)
}).listen(port, host, () => {
  console.log(`Serving ${distRoot} on http://${host}:${port}, proxying /api to ${apiTarget.href}`)
})
