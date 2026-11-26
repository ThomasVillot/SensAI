function PromptPanel({ prompt, onClose }) {
  return (
    <aside className="itinerary">
      <div className="itinerary-header">
        <h2>Prompt système</h2>
        <button className="secondary" onClick={onClose}>
          Fermer
        </button>
      </div>
      {prompt.status === 'loading' && <p className="typing">Chargement…</p>}
      {prompt.status === 'error' && <div className="error">{prompt.error}</div>}
      {prompt.status === 'ready' && <pre className="prompt">{prompt.text}</pre>}
    </aside>
  )
}

export default PromptPanel
