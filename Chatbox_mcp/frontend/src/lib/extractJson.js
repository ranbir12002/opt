// src/lib/extractJson.js
export function extractJsonBlock(text) {
  if (typeof text !== 'string') return { ok: false, error: 'not a string' };
  const fence = /```json\s*([\s\S]*?)```/i;
  const m = text.match(fence);
  if (m && m[1]) {
    try { return { ok: true, data: JSON.parse(m[1].trim()) }; }
    catch (e) { return { ok: false, error: 'bad fenced json: ' + e.message }; }
  }
  const t = text.trim();
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    try { return { ok: true, data: JSON.parse(t) }; } catch {}
  }
  return { ok: false, error: 'no json found' };
}
