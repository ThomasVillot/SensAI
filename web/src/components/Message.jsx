import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import ToolCall from './ToolCall.jsx'

function formatStats(stats) {
  const parts = [`${stats.latency_s} s`]
  if (stats.first_token_s !== null) {
    parts.push(`1er token ${stats.first_token_s} s`)
  }
  if (stats.tokens !== null) {
    parts.push(`${stats.tokens} tokens`)
  }
  parts.push(`${stats.calls} appel${stats.calls > 1 ? 's' : ''}`)
  return parts.join(' · ')
}

function Message({ message }) {
  if (message.role === 'user') {
    return <div className="message user">{message.content}</div>
  }

  const lastToolIndex = message.parts.findLastIndex((part) => part.type === 'tool')
  const waiting = message.parts.length === 0 && !message.error && !message.blocked
  const working = message.step && !message.stats && !message.error && !message.blocked

  return (
    <div className="message assistant">
      {message.parts.map((part, index) => {
        if (part.type === 'tool') {
          return <ToolCall key={index} tool={part} />
        }
        if (!part.content.trim()) {
          return null
        }
        return (
          <div key={index} className={index < lastToolIndex ? 'step' : 'answer'}>
            <Markdown remarkPlugins={[remarkGfm]}>{part.content}</Markdown>
          </div>
        )
      })}
      {waiting && <span className="typing">SensAI réfléchit…</span>}
      {working && (
        <div className="typing">
          Étape {message.step.n}/{message.step.max}
        </div>
      )}
      {message.error && (
        <div className="error">
          {message.error}
          <br />
          Ce tour n'est pas conservé dans l'historique.
        </div>
      )}
      {message.blocked && (
        <div className="blocked" role="alert">
          {message.blocked}
          <br />
          Ce tour n'est pas conservé dans l'historique.
        </div>
      )}
      {message.noTools && (
        <div className="stopped">
          Ce modèle ne sait pas utiliser les outils : réponses sans recherche (pas de météo, vols, hôtels…)
        </div>
      )}
      {message.stats && <div className="stats">{formatStats(message.stats)}</div>}
    </div>
  )
}

export default Message
