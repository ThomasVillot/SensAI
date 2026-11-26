export async function createConversation() {
  const response = await fetch('/api/conversations', { method: 'POST' })
  if (!response.ok) {
    throw new Error(`erreur HTTP ${response.status}`)
  }
  const data = await response.json()
  return data.conversation_id
}

export async function fetchModels() {
  const response = await fetch('/api/models')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data
}

export async function fetchWarnings() {
  const response = await fetch('/api/warnings')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.warnings
}

export async function fetchProfiles() {
  const response = await fetch('/api/profiles')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.profiles
}

export async function fetchPrompt(profile) {
  const response = await fetch(`/api/prompt?profile=${encodeURIComponent(profile)}`)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.prompt
}

export async function streamChat(conversationId, message, model, profile, onEvent, signal) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message, model, profile }),
    signal,
  })
  await readEvents(response, onEvent)
}

export async function installModel(model, onEvent) {
  const response = await fetch('/api/models/install', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ model }),
  })
  await readEvents(response, onEvent)
}

async function readEvents(response, onEvent) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) {
      break
    }
    buffer += decoder.decode(value, { stream: true })
    const blocks = buffer.split('\n\n')
    buffer = blocks.pop()
    for (const block of blocks) {
      if (block.startsWith('data: ')) {
        onEvent(JSON.parse(block.slice(6)))
      }
    }
  }
}

export async function fetchConversations() {
  const response = await fetch('/api/conversations')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.conversations
}

export async function openConversation(conversationId) {
  const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}`)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data
}

export async function fetchCache() {
  const response = await fetch('/api/cache')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.mode
}

export async function changeCache(mode) {
  const response = await fetch('/api/cache', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.mode
}

export async function shareConversation(conversationId) {
  const response = await fetch(`/api/conversations/${encodeURIComponent(conversationId)}/share`, { method: 'POST' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data
}

export async function shareDocument() {
  const response = await fetch('/api/document/share', { method: 'POST' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data
}

export async function revokeShare(token) {
  const response = await fetch(`/api/shares/${encodeURIComponent(token)}`, { method: 'DELETE' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.revoked
}

export async function clearTrip() {
  const response = await fetch('/api/trip', { method: 'DELETE' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.cleared
}

export async function fetchDocument() {
  const response = await fetch('/api/document')
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    throw new Error(data.detail || `erreur HTTP ${response.status}`)
  }
  return data.markdown
}
