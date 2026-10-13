// Report native terminal failures and stop review acknowledgements once the
// review MCP has accepted its receipt. The application still owns publication.
import { existsSync } from 'node:fs';

export const name = 'research-harness-outcome';

export function apply(ctx) {
  const reviewResult = process.env.INVESTMENT_STUDIO_WATCHLIST_HARNESS_MODE === 'review'
    && process.env.INVESTMENT_STUDIO_REVIEW_RESULT;
  if (reviewResult) {
    // The review MCP atomically exposes this receipt only after its checks pass.
    // Do not spend another model step acknowledging it: the outer deadline can
    // otherwise kill an accepted review before Python saves its checkpoint.
    // Python still validates the receipt and owns application/publication checks.
    ctx.on('agent/pre-step', async (_step, next) => (
      existsSync(reviewResult) ? { kind: 'reject' } : next()
    ), { prepend: true });
  }
  ctx.on('session/event', (_session, event) => {
    if (event.type === 'turn/end' && event.data.reason.kind === 'max-tokens') {
      process.stderr.write('RESEARCH_HARNESS_END {"reason":"max-tokens"}\n');
    }
    if (event.type === 'turn/end' && event.data.reason.kind === 'error' &&
        event.data.reason.error?.message === 'Provider finish_reason: content_filter') {
      process.stderr.write('RESEARCH_HARNESS_END {"reason":"content-filter"}\n');
    }
  });
}
