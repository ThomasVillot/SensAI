import { useEffect, useRef } from 'react'
import Message from './Message.jsx'

function average(values) {
  return (values.reduce((sum, value) => sum + value, 0) / values.length).toFixed(2)
}

function describeAverageLatency(messages) {
  const latencies = messages.filter((message) => message.stats).map((message) => message.stats.latency_s)
  if (latencies.length === 0) {
    return null
  }
  const text = `Latence moyenne : ${average(latencies)} s sur ${latencies.length} tour(s)`
  if (latencies.length === 1) {
    return text
  }
  return `${text} (${average(latencies.slice(1))} s hors 1er tour)`
}

function MessageList({ messages }) {
  const listRef = useRef(null)
  const followBottom = useRef(true)
  const messageCount = useRef(0)

  function handleScroll() {
    const list = listRef.current
    followBottom.current = list.scrollHeight - list.scrollTop - list.clientHeight < 40
  }

  useEffect(() => {
    if (messages.length !== messageCount.current) {
      messageCount.current = messages.length
      followBottom.current = true
    }
    if (followBottom.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages])

  if (messages.length === 0) {
    return (
      <main className="messages empty">
        <p>Décris ton voyage : destination, dates, budget…</p>
      </main>
    )
  }

  const averageLatency = describeAverageLatency(messages)

  return (
    <main className="messages" ref={listRef} onScroll={handleScroll}>
      {messages.map((message, index) => (
        <Message key={index} message={message} />
      ))}
      {averageLatency && <div className="average-latency">{averageLatency}</div>}
    </main>
  )
}

export default MessageList
