async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'content-type': 'application/json' },
    ...options,
  })
  const text = await response.text()
  let body = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!response.ok) {
    const detail = body && body.detail ? body.detail : body
    throw new Error(detail || response.statusText)
  }
  return body
}

export const api = {
  projects: () => request('/api/projects'),
  project: (slug) => request(`/api/projects/${slug}`),
  run: (slug, runId) => request(`/api/projects/${slug}/runs/${runId}`),
  graph: (slug, runId) => request(`/api/projects/${slug}/runs/${runId}/graph`),
  journal: (slug, runId) =>
    request(`/api/projects/${slug}/journal?run=${encodeURIComponent(runId)}`),
  answer: (slug, runId, gateId, answer) =>
    request(`/api/projects/${slug}/runs/${runId}/gates/${gateId}`, {
      method: 'POST',
      body: JSON.stringify({ answer }),
    }),
}
