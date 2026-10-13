// The Harness owns child sessions and cancellation. This adapter binds its
// actual Agent identities to the application's retained research tasks.
export const name = 'studio-research-delegation';
export const inject = ['tools', 'subagents', 'llm'];

const common = ['read_research_instrument', 'read_research_dossier',
  'read_market_source', 'read_public_source', 'read_sector_source',
  'read_quant_analysis', 'read_research_numbers', 'compare_instruments'];
const information = ['search_market_information', 'search_public_information', 'search_sector_information'];
const roleTools = {
  recent_updates: [...information, 'read_sector_company'],
  themes: [...information, 'read_sector_company', 'read_quant_analysis'],
  quantitative: ['read_sector_company', 'read_research_numbers', 'read_stored_market_data',
    'compare_instruments', 'read_quant_capability', 'read_quant_inputs', 'run_quant_analysis',
    'read_quant_analysis', 'search_public_information'],
};

// The pinned native structured-output transport accepts a smaller JSON Schema
// vocabulary than Pydantic. Project the same schema for that transport; the
// server still checks every original constraint before accepting a proposal.
export function nativeResultSchema(schema) {
  const annotations = ['description', 'title', 'default', 'examples'];
  const visit = (node, references = []) => {
    if (node.$ref) {
      const ref = node.$ref;
      if (!ref.startsWith('#/$defs/') || references.includes(ref)) throw new Error('Unsupported research schema reference.');
      const target = schema.$defs?.[ref.slice('#/$defs/'.length)];
      if (!target) throw new Error('Missing research schema definition.');
      return visit({ ...target, ...Object.fromEntries(Object.entries(node).filter(([key]) => key !== '$ref')) }, [...references, ref]);
    }
    if (node.allOf || node.not || node.if) throw new Error('Unsupported research result schema composition.');
    const result = Object.fromEntries(annotations.filter(key => key in node).map(key => [key, node[key]]));
    const variants = node.anyOf || node.oneOf;
    if (variants) result.oneOf = variants.map(item => visit(item, references));
    else {
      if (node.type) result.type = node.type;
      if (node.properties) result.properties = Object.fromEntries(Object.entries(node.properties).map(([key, value]) => [key, visit(value, references)]));
      if (node.items) result.items = visit(node.items, references);
      for (const key of ['required', 'enum', 'const']) if (key in node) result[key] = node[key];
      if ('additionalProperties' in node) result.additionalProperties = node.additionalProperties !== false;
    }
    return result;
  };
  return visit(schema);
}

// Count provider requests, not waiting agents. A parent awaiting its children
// holds no permit, including when a theme researcher delegates again.
export function modelSlots(capacity) {
  let active = 0;
  const waiting = [];
  const release = () => {
    active -= 1;
    const next = waiting.shift();
    if (next) {
      next.signal?.removeEventListener('abort', next.abort);
      active += 1;
      next.resolve(release);
    }
  };
  return signal => {
    signal?.throwIfAborted();
    if (active < capacity) {
      active += 1;
      return Promise.resolve(release);
    }
    return new Promise((resolve, reject) => {
      const entry = { resolve, reject, signal, abort: null };
      entry.abort = () => {
        const index = waiting.indexOf(entry);
        if (index >= 0) waiting.splice(index, 1);
        reject(signal.reason);
      };
      waiting.push(entry);
      signal?.addEventListener('abort', entry.abort, { once: true });
    });
  };
}

function receipt(value) {
  const result = value.result || value;
  return Object.fromEntries(Object.entries({ task_id: value.task_id, status: value.status, reused: value.reused || false,
    summary: value.summary ?? result.summary, source_ids: value.source_ids ?? result.source_ids,
    result_read: value.result_read ?? { tool: 'read_research_context', section: 'task_results', path: [value.task_id, 'result'] },
  }).filter(([, item]) => item !== undefined));
}

export function apply(ctx) {
  const runId = process.env.INVESTMENT_STUDIO_RESEARCH_RUN_ID;
  const token = process.env.INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN;
  const api = process.env.INVESTMENT_STUDIO_RESEARCH_API_BASE_URL;
  const capacity = Number(process.env.INVESTMENT_STUDIO_RESEARCH_MODEL_CONCURRENCY || 3);
  if (!runId || !token || !api) throw new Error('Research delegation requires a bound run and API.');
  if (!Number.isSafeInteger(capacity) || capacity < 1) throw new Error('Invalid research model concurrency.');
  const acquire = modelSlots(capacity);
  ctx.on('llm/stream', async function* (options, next) {
    const release = await acquire(options.signal);
    try { yield* next(); } finally { release(); }
  });

  const bindings = new Map();
  const arrivals = new Map();
  let rootId;
  const agentId = agent => agent?.session?.id;
  const bind = (agent, task) => {
    const id = agentId(agent);
    if (!id) throw new Error('Native research child has no session identity.');
    bindings.set(id, task);
    for (const resolve of arrivals.get(id) || []) resolve(task);
    arrivals.delete(id);
  };
  const assigned = async exec => {
    const id = agentId(exec.agent);
    // No native child can exist before the root's first delegation sets rootId.
    if (!id || !rootId || id === rootId) throw new Error('This tool belongs to an assigned research child.');
    if (bindings.has(id)) return bindings.get(id);
    // Native startup may begin the child turn before start() returns its handle.
    return new Promise((resolve, reject) => {
      const done = task => {
        exec.signal.removeEventListener('abort', abort);
        resolve(task);
      };
      const abort = () => {
        arrivals.set(id, (arrivals.get(id) || []).filter(item => item !== done));
        reject(exec.signal.reason);
      };
      exec.signal.throwIfAborted();
      arrivals.set(id, [...(arrivals.get(id) || []), done]);
      exec.signal.addEventListener('abort', abort, { once: true });
    });
  };
  const request = async (suffix, body, signal) => {
    const response = await fetch(`${api.replace(/\/$/, '')}/research/runs/${encodeURIComponent(runId)}/${suffix}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      signal: AbortSignal.any([signal || new AbortController().signal, AbortSignal.timeout(180000)]),
    });
    const value = await response.json();
    if (!response.ok) throw new Error(`Research task rejected (${response.status}): ${JSON.stringify(value.detail ?? value)}`);
    return value;
  };
  const output = { schema: { type: 'object', additionalProperties: true },
    render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] };

  // Reject invalid citations/sparse writes while the child is still running,
  // so it receives the real API error and can repair its own proposal. The
  // native identity, not a model argument, selects the retained task.
  ctx.on('tools/execute', async function (exec, next) {
    if (exec.name !== 'structured_output') return next();
    const task = await assigned(exec);
    task.accepted = await request(`tasks/${encodeURIComponent(task.task_id)}/result`, { result: exec.arguments }, exec.signal);
    return next();
  });

  ctx.tools.register({
    name: 'read_assigned_context',
    description: 'Read your own assigned task, role skill, frozen instrument context and evidence pointers. Your identity is supplied by the runtime; you cannot select another task.',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: {
        offset: { type: 'integer' },
        limit: { type: 'integer' },
        path: { type: 'array', items: { oneOf: [{ type: 'string' }, { type: 'integer' }] } },
      },
    }, output, isConcurrencySafe: () => true,
    async execute(args, exec) {
      const task = await assigned(exec);
      const query = new URLSearchParams();
      if (args.offset !== undefined) query.set('offset', String(args.offset));
      if (args.limit !== undefined) query.set('limit', String(args.limit));
      if (args.path !== undefined) query.set('path', JSON.stringify(args.path));
      return request(`tasks/${encodeURIComponent(task.task_id)}/context?${query}`, undefined, exec.signal);
    },
  });

  ctx.tools.register({
    name: 'delegate_research',
    description: 'Delegate a concrete independent research question to a fresh specialist. Issue independent calls together to run them in parallel. Choose recent_updates for external event proposals, themes for theme updates, or quantitative for numerical findings and retained computation source_ids; quantitative results do not contain event, theme or facts publication objects. A themes specialist or the coordinator incorporates quantitative findings into the final research. Do not create a session for a trivial check or unchanged section. A themes specialist may delegate independent theme questions once more. Each child receives only its task context and role tools, shares this run’s retained evidence, and returns an unpublished proposal. Waits until the result is saved; the parent remains responsible for synthesis.',
    parameters: {
      type: 'object', additionalProperties: false,
      required: ['role', 'instrument_id', 'instruction'],
      properties: {
        role: { type: 'string', enum: ['recent_updates', 'themes', 'quantitative'] },
        instrument_id: { type: 'string' },
        instruction: { type: 'string', description: 'The specific question, scope and expected useful result. Reuse an existing completed task instead of repeating it.' },
        theme_ids: { type: 'array', items: { type: 'string' }, description: 'Existing theme IDs, only when role is themes; omit for a new theme question. For recent_updates or quantitative, omit or use an empty list; describe any relevant theme background in instruction.' },
      },
    }, output, isConcurrencySafe: () => true,
    async execute(args, exec) {
      const id = agentId(exec.agent);
      if (!id) throw new Error('Delegation requires an actual calling agent.');
      if (!rootId) rootId = id;
      const parentTask = id === rootId ? null : await assigned(exec);
      if (parentTask && (parentTask.role !== 'themes' || parentTask.depth >= 2 || args.role !== 'themes')) {
        throw new Error('Only a first-level theme specialist can delegate theme subtasks.');
      }
      const task = await request('tasks', { ...args, parent_task_id: parentTask?.task_id ?? null }, exec.signal);
      const parentReceipt = value => ({ ...receipt(value), ...(parentTask ? {
        result_read: { tool: 'read_assigned_context', path: ['task_results', value.task_id, 'result'] },
      } : {}) });
      if (task.reused) return parentReceipt(task);
      let run;
      let binding;
      let saved = false;
      try {
        const depth = parentTask ? parentTask.depth + 1 : 1;
        const allow = [...new Set([...common, ...roleTools[args.role]])].map(value => `mcp__research__${value}`);
        allow.push('read_assigned_context');
        if (args.role === 'themes' && depth === 1) allow.push('delegate_research');
        run = await ctx.subagents.start('spawn', {
          parent: exec.agent, signal: exec.signal, maxDepth: 2,
          label: `research:${task.task_id}`,
          toolFilter: { allow }, outputSchema: nativeResultSchema(task.result_schema),
          persona: `${process.env.INVESTMENT_STUDIO_RESEARCH_PERSONA || ''}\nYou are the assigned ${args.role} specialist, not the coordinator. Work only on this question. You may read/search/calculate retained evidence but cannot publish or change human records. Return the requested structured proposal with exact source IDs. A quiet result or explicit limitation is valid; do not invent events or themes.`,
          prompt: [{ type: 'text', text: `Task: ${args.instruction}\nYour role-specific context and skill follow. These are already supplied; inspect selected original evidence through their read tools as needed, without reloading unrelated sections. Task identifiers and scope are runtime-owned.\n${JSON.stringify(task.context)}` }],
        });
        binding = { task_id: task.task_id, role: args.role, depth };
        bind(run.localAgent, binding);
        const result = await run.result;
        if (binding.accepted) {
          saved = true;
          return parentReceipt(binding.accepted);
        }
        if (result.stopReason !== 'completed' || result.structured === undefined) {
          throw new Error(`Research child did not return a complete proposal (${result.stopReason}).`);
        }
        throw new Error('Native result has no accepted research task receipt.');
      } catch (error) {
        if (!saved && !binding?.accepted) {
          try {
            await request(`tasks/${encodeURIComponent(task.task_id)}/failure`, {
              message: exec.signal.aborted ? 'Research task was cancelled.' : String(error.message || error).slice(0, 2000),
            }, AbortSignal.timeout(5000));
          } catch { /* Run revocation/attempt recovery owns rejected cleanup. */ }
        }
        throw error;
      } finally {
        if (run) {
          try { await run.dispose(); }
          finally { bindings.delete(agentId(run.localAgent)); }
        }
      }
    },
  });
}
