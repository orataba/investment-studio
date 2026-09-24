// Pinned headless prints only provider errors; max-tokens otherwise looks like
// an unexplained exit 1. Observe the native outcome without changing its loop.
export const name = 'research-harness-outcome';

export function apply(ctx) {
  ctx.on('session/event', (_session, event) => {
    if (event.type === 'turn/end' && event.data.reason.kind === 'max-tokens') {
      process.stderr.write('RESEARCH_HARNESS_END {"reason":"max-tokens"}\n');
    }
  });
}
