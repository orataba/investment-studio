export type TableCell = string | number | boolean | null | undefined
export type TableExportFormat = 'csv' | 'xlsx'

const textEncoder = new TextEncoder()
let crc32Table: number[] | null = null

export function csvEscape(value: TableCell) {
  if (value == null) {
    return ''
  }
  const text = String(value)
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function downloadCsv(filename: string, rows: TableCell[][]) {
  const csv = rows.map((row) => row.map(csvEscape).join(',')).join('\r\n')
  downloadBlob(ensureExtension(filename, 'csv'), new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' }))
}

export function downloadXlsx(filename: string, rows: TableCell[][], sheetName = 'Sheet1') {
  const workbook = buildXlsxWorkbook(rows, normalizeSheetName(sheetName))
  downloadBlob(
    ensureExtension(filename, 'xlsx'),
    new Blob([workbook], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }),
  )
}

export function downloadTable(filenameBase: string, rows: TableCell[][], format: TableExportFormat, sheetName = 'Sheet1') {
  if (format === 'xlsx') {
    downloadXlsx(filenameBase, rows, sheetName)
    return
  }
  downloadCsv(filenameBase, rows)
}

function downloadBlob(filename: string, blob: Blob) {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

function ensureExtension(filename: string, extension: TableExportFormat) {
  const trimmed = filename.trim()
  const withoutKnownExtension = trimmed.replace(/\.(csv|xlsx)$/i, '')
  return `${withoutKnownExtension || 'export'}.${extension}`
}

function normalizeSheetName(value: string) {
  const normalized = value.replace(/[\\/?*:[\]]/g, ' ').trim().slice(0, 31)
  return normalized || 'Sheet1'
}

function buildXlsxWorkbook(rows: TableCell[][], sheetName: string) {
  return createZip([
    {
      name: '[Content_Types].xml',
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' +
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
        '<Default Extension="xml" ContentType="application/xml"/>' +
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' +
        '</Types>',
    },
    {
      name: '_rels/.rels',
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>' +
        '</Relationships>',
    },
    {
      name: 'xl/workbook.xml',
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">' +
        '<sheets>' +
        `<sheet name="${escapeXmlAttribute(sheetName)}" sheetId="1" r:id="rId1"/>` +
        '</sheets>' +
        '</workbook>',
    },
    {
      name: 'xl/_rels/workbook.xml.rels',
      content:
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>' +
        '</Relationships>',
    },
    {
      name: 'xl/worksheets/sheet1.xml',
      content: buildWorksheetXml(rows),
    },
  ])
}

function buildWorksheetXml(rows: TableCell[][]) {
  const columnCount = rows.reduce((max, row) => Math.max(max, row.length), 0)
  const dimension = rows.length && columnCount ? `A1:${columnName(columnCount - 1)}${rows.length}` : 'A1'
  const columns = columnCount
    ? `<cols>${Array.from({ length: columnCount }, (_, index) => {
        const width = Math.min(48, Math.max(10, estimateColumnWidth(rows, index)))
        return `<col min="${index + 1}" max="${index + 1}" width="${width}" customWidth="1"/>`
      }).join('')}</cols>`
    : ''
  const sheetData = rows
    .map((row, rowIndex) => {
      const rowNumber = rowIndex + 1
      const cells = row
        .map((cell, columnIndex) => buildCellXml(cell, rowNumber, columnIndex))
        .filter(Boolean)
        .join('')
      return `<row r="${rowNumber}">${cells}</row>`
    })
    .join('')

  return (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' +
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">' +
    `<dimension ref="${dimension}"/>` +
    columns +
    `<sheetData>${sheetData}</sheetData>` +
    '</worksheet>'
  )
}

function buildCellXml(value: TableCell, rowNumber: number, columnIndex: number) {
  if (value == null) {
    return ''
  }
  const ref = `${columnName(columnIndex)}${rowNumber}`
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `<c r="${ref}"><v>${value}</v></c>`
  }
  if (typeof value === 'boolean') {
    return `<c r="${ref}" t="b"><v>${value ? 1 : 0}</v></c>`
  }
  const text = String(value)
  const preserveSpace = /^\s|\s$|\n|\r/.test(text) ? ' xml:space="preserve"' : ''
  return `<c r="${ref}" t="inlineStr"><is><t${preserveSpace}>${escapeXmlText(text)}</t></is></c>`
}

function estimateColumnWidth(rows: TableCell[][], columnIndex: number) {
  return rows.reduce((max, row) => {
    const value = row[columnIndex]
    const length = value == null ? 0 : String(value).length
    return Math.max(max, Math.min(48, length + 2))
  }, 10)
}

function columnName(index: number) {
  let column = ''
  let current = index + 1
  while (current > 0) {
    const remainder = (current - 1) % 26
    column = String.fromCharCode(65 + remainder) + column
    current = Math.floor((current - 1) / 26)
  }
  return column
}

function escapeXmlText(value: string) {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

function escapeXmlAttribute(value: string) {
  return escapeXmlText(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;')
}

function createZip(entries: Array<{ name: string; content: string }>) {
  const chunks: Uint8Array[] = []
  const centralDirectory: Uint8Array[] = []
  const timestamp = dosTimestamp(new Date())
  let offset = 0

  entries.forEach((entry) => {
    const nameBytes = textEncoder.encode(entry.name)
    const data = textEncoder.encode(entry.content)
    const crc = crc32(data)
    const localHeader = createLocalFileHeader(nameBytes, data, crc, timestamp)
    const localOffset = offset
    chunks.push(localHeader, nameBytes, data)
    offset += localHeader.length + nameBytes.length + data.length
    centralDirectory.push(createCentralDirectoryHeader(nameBytes, data, crc, timestamp, localOffset))
  })

  const centralDirectoryOffset = offset
  centralDirectory.forEach((header) => {
    chunks.push(header)
    offset += header.length
  })
  const centralDirectorySize = offset - centralDirectoryOffset
  chunks.push(createEndOfCentralDirectory(entries.length, centralDirectorySize, centralDirectoryOffset))
  return concatUint8Arrays(chunks)
}

function createLocalFileHeader(
  nameBytes: Uint8Array,
  data: Uint8Array,
  crc: number,
  timestamp: { time: number; date: number },
) {
  const header = new Uint8Array(30)
  const view = new DataView(header.buffer)
  view.setUint32(0, 0x04034b50, true)
  view.setUint16(4, 20, true)
  view.setUint16(6, 0x0800, true)
  view.setUint16(8, 0, true)
  view.setUint16(10, timestamp.time, true)
  view.setUint16(12, timestamp.date, true)
  view.setUint32(14, crc, true)
  view.setUint32(18, data.length, true)
  view.setUint32(22, data.length, true)
  view.setUint16(26, nameBytes.length, true)
  view.setUint16(28, 0, true)
  return header
}

function createCentralDirectoryHeader(
  nameBytes: Uint8Array,
  data: Uint8Array,
  crc: number,
  timestamp: { time: number; date: number },
  localOffset: number,
) {
  const header = new Uint8Array(46 + nameBytes.length)
  const view = new DataView(header.buffer)
  view.setUint32(0, 0x02014b50, true)
  view.setUint16(4, 20, true)
  view.setUint16(6, 20, true)
  view.setUint16(8, 0x0800, true)
  view.setUint16(10, 0, true)
  view.setUint16(12, timestamp.time, true)
  view.setUint16(14, timestamp.date, true)
  view.setUint32(16, crc, true)
  view.setUint32(20, data.length, true)
  view.setUint32(24, data.length, true)
  view.setUint16(28, nameBytes.length, true)
  view.setUint16(30, 0, true)
  view.setUint16(32, 0, true)
  view.setUint16(34, 0, true)
  view.setUint16(36, 0, true)
  view.setUint32(38, 0, true)
  view.setUint32(42, localOffset, true)
  header.set(nameBytes, 46)
  return header
}

function createEndOfCentralDirectory(entryCount: number, centralDirectorySize: number, centralDirectoryOffset: number) {
  const header = new Uint8Array(22)
  const view = new DataView(header.buffer)
  view.setUint32(0, 0x06054b50, true)
  view.setUint16(4, 0, true)
  view.setUint16(6, 0, true)
  view.setUint16(8, entryCount, true)
  view.setUint16(10, entryCount, true)
  view.setUint32(12, centralDirectorySize, true)
  view.setUint32(16, centralDirectoryOffset, true)
  view.setUint16(20, 0, true)
  return header
}

function dosTimestamp(date: Date) {
  const year = Math.max(1980, date.getFullYear())
  return {
    time: (date.getHours() << 11) | (date.getMinutes() << 5) | Math.floor(date.getSeconds() / 2),
    date: ((year - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate(),
  }
}

function crc32(bytes: Uint8Array) {
  const table = getCrc32Table()
  let crc = 0xffffffff
  bytes.forEach((byte) => {
    crc = (crc >>> 8) ^ table[(crc ^ byte) & 0xff]
  })
  return (crc ^ 0xffffffff) >>> 0
}

function getCrc32Table() {
  if (crc32Table) {
    return crc32Table
  }
  crc32Table = Array.from({ length: 256 }, (_, index) => {
    let value = index
    for (let bit = 0; bit < 8; bit += 1) {
      value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1
    }
    return value >>> 0
  })
  return crc32Table
}

function concatUint8Arrays(chunks: Uint8Array[]) {
  const length = chunks.reduce((total, chunk) => total + chunk.length, 0)
  const result = new Uint8Array(length)
  let offset = 0
  chunks.forEach((chunk) => {
    result.set(chunk, offset)
    offset += chunk.length
  })
  return result
}
