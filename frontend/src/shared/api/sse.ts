/** Server-Sent Events over a fetch body (the browser EventSource only does GET; docs/frontend-plan.md §3.2). */
export interface RawSseEvent { event: string; data: unknown }

function parseBlock(block: string): RawSseEvent | null {
  let event = 'message';
  const data: string[] = [];
  for (const line of block.split(/\r\n|\n|\r/)) {
    if (!line || line.startsWith(':')) continue;
    const colon = line.indexOf(':');
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? '' : line.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'event') event = value;
    else if (field === 'data') data.push(value);
  }
  return data.length ? { event, data: JSON.parse(data.join('\n')) } : null;
}

export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<RawSseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r\n\r\n|\n\n|\r\r/);
      buffer = done ? '' : blocks.pop() ?? '';
      for (const block of blocks) {
        const parsed = parseBlock(block);
        if (parsed) yield parsed;
      }
      if (done) return;
    }
  } finally {
    reader.releaseLock();
  }
}
