// Project only native provider counters. The parent owns the private temporary
// file; neither prompts, tool arguments, outputs nor credentials are written.
import { appendFileSync, statSync } from 'node:fs';
export const name = 'studio-provider-usage';
const MAX_BYTES = 4 * 1024 * 1024;
export function apply(ctx) {
  const path = process.env.INVESTMENT_STUDIO_MODEL_USAGE_FILE;
  const invocation = process.env.INVESTMENT_STUDIO_MODEL_USAGE_INVOCATION;
  if (!path || !invocation) return;
  let full = false;
  ctx.on('session/event', (session, event) => {
    if (full) return;
    let kind;
    let tokens;
    if (event.type === 'step/start') kind = 'start';
    else if (event.type === 'step/end') kind = 'end';
    else if (event.type === 'turn/end') kind = 'turn_end';
    else {
      const usage = event.type === 'assistant/chunk' && event.data.chunk.type === 'usage'
        ? event.data.chunk.usage : event.type === 'assistant/message' ? event.data.usage : undefined;
      if (!usage) return;
      kind = 'usage';
      tokens = { uncached_input_tokens: usage.inputTokens, output_tokens: usage.outputTokens,
        cache_read_tokens: usage.cacheReadTokens ?? 0, cache_write_tokens: usage.cacheWriteTokens ?? 0 };
      if (Object.values(tokens).some(value => !Number.isSafeInteger(value) || value < 0)) return;
    }
    const row = { invocation_id: invocation, session_id: session.id,
      kind, turn: event.data.turn, ...(kind === 'turn_end' ? {} : {step: event.data.step}), ...(tokens ? {tokens} : {}) };
    const line = JSON.stringify(row) + '\n';
    try {
      // Once full, omit further events. Missing end/usage is reported partial.
      if (statSync(path).size + Buffer.byteLength(line) > MAX_BYTES - 64) {
        appendFileSync(path, '{"kind":"overflow"}\n', {mode: 0o600});
        full = true;
      } else appendFileSync(path, line, {mode: 0o600});
    } catch {
      // Usage is diagnostic and cannot abort the original model/tool result.
    }
  });
}
