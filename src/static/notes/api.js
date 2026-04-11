// Minimal fetch wrappers around the /v1 API.

const API_KEY = window.__CHAT_API_KEY__ || '';

function authHeaders() {
  return API_KEY ? { Authorization: `Bearer ${API_KEY}` } : {};
}

export async function listPages() {
  const r = await fetch('/v1/pages', { headers: authHeaders() });
  if (!r.ok) throw new Error(`listPages: ${r.status}`);
  return (await r.json()).pages;
}

export async function getPage(id) {
  const r = await fetch(`/v1/pages/${encodeURIComponent(id)}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`getPage: ${r.status}`);
  return r.json();
}

export function pageRawUrl(id) {
  return `/v1/pages/${encodeURIComponent(id)}/raw`;
}

export async function deletePage(id) {
  const r = await fetch(`/v1/pages/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: authHeaders(),
  });
  if (!r.ok) throw new Error(`deletePage: ${r.status}`);
}

export async function search(q, { limit = 8 } = {}) {
  const url = `/v1/search?q=${encodeURIComponent(q)}&limit=${limit}`;
  const r = await fetch(url, { headers: authHeaders() });
  if (!r.ok) throw new Error(`search: ${r.status}`);
  return r.json();
}

export async function recentCommits(limit = 10) {
  const r = await fetch(`/v1/commits?limit=${limit}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`commits: ${r.status}`);
  return (await r.json()).commits;
}

export async function latestSession() {
  const r = await fetch('/v1/sessions/latest', { headers: authHeaders() });
  if (!r.ok) return { session_id: null, messages: [] };
  return r.json();
}

/**
 * POST /v1/responses and stream the SSE body.
 * Yields:
 *   { type: 'delta', text }                  - assistant content chunk
 *   { type: 'tool_call', id, name, preview } - a tool round is starting
 *   { type: 'tool_request', ... }            - frontend-side tool call
 *   { type: 'done', sessionId }              - stream finished
 *
 * `openPageId`: when the user is viewing a page in the sheet, pass
 * the slug here. The backend prepends a context preamble so the agent
 * knows which page "this page" refers to.
 */
export async function* streamChat({ prompt, sessionId, openPageId, toolResults }) {
  const body = {};
  if (prompt) body.prompt = prompt;
  if (sessionId) body.session_id = sessionId;
  if (openPageId) body.open_page_id = openPageId;
  if (toolResults) body.tool_results = toolResults;

  const resp = await fetch('/v1/responses', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok || !resp.body) {
    const text = await resp.text().catch(() => '');
    throw new Error(`streamChat: ${resp.status} ${text}`);
  }

  const newSessionId = resp.headers.get('X-Session-Id');
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      for (const line of rawEvent.split('\n')) {
        if (!line.startsWith('data: ')) continue;
        const payload = line.slice(6);
        if (payload === '[DONE]') {
          yield { type: 'done', sessionId: newSessionId };
          return;
        }
        try {
          const obj = JSON.parse(payload);
          if (obj.choices?.[0]?.delta?.content) {
            yield { type: 'delta', text: obj.choices[0].delta.content };
          } else if (obj.tool_call) {
            yield { type: 'tool_call', ...obj.tool_call };
          } else if (obj.tool_request) {
            yield { type: 'tool_request', ...obj.tool_request };
          }
        } catch {
          /* ignore */
        }
      }
    }
  }
  yield { type: 'done', sessionId: newSessionId };
}
