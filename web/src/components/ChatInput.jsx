function ChatInput({ text, onTextChange, onSend, onStop, disabled, replying }) {
  function handleSubmit(event) {
    event.preventDefault()
    const message = text.trim()
    if (!message || disabled) {
      return
    }
    onSend(message)
    onTextChange('')
  }

  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      handleSubmit(event)
    }
  }

  return (
    <form className="chat-input" onSubmit={handleSubmit}>
      <textarea
        value={text}
        onChange={(event) => onTextChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Écris ton message…"
        rows={2}
      />
      {replying ? (
        <button key="stop" type="button" className="stop" onClick={onStop}>
          Arrêter
        </button>
      ) : (
        <button key="send" type="submit" disabled={disabled || !text.trim()}>
          Envoyer
        </button>
      )}
    </form>
  )
}

export default ChatInput
