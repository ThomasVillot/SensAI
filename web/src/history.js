function parseToolResult(content) {
  try {
    return JSON.parse(content)
  } catch {
    return content
  }
}

function newReply() {
  return { role: 'assistant', parts: [], stats: null, error: null, restored: true }
}

export function toChatMessages(savedMessages) {
  const messages = []
  for (const saved of savedMessages) {
    if (saved.role === 'user') {
      messages.push({ role: 'user', content: saved.content })
      messages.push(newReply())
      continue
    }

    const reply = messages[messages.length - 1]
    if (saved.role === 'assistant') {
      if (saved.content) {
        reply.parts.push({ type: 'text', content: saved.content })
      }
      for (const call of saved.tool_calls || []) {
        reply.parts.push({ type: 'tool', name: call.function.name, arguments: call.function.arguments })
      }
    } else if (saved.role === 'tool') {
      const result = parseToolResult(saved.content)
      const waitingTool = reply.parts.find((part) => part.type === 'tool' && part.result === undefined)
      if (waitingTool) {
        waitingTool.result = result
      } else {
        reply.parts.push({ type: 'tool', name: saved.tool_name, result })
      }
    }
  }
  return messages
}
