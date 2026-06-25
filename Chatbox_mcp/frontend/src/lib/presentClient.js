// src/lib/presentClient.js
export async function present(question, payload, hints) {
  const base = import.meta.env.VITE_PRESENTER_BASE || "http://127.0.0.1:9000";
  const res = await fetch(`${base}/api/present`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ question, payload, hints })
  });
  if (!res.ok) throw new Error(`present failed: ${res.status}`);
  return await res.json(); // { envelope, telemetry }
}
