function ConversationList({ conversations, currentId, onOpen, disabled }) {
  return (
    <aside className="conversations">
      <h2>Conversations</h2>
      {conversations.length === 0 && <p className="no-conversation">Aucune conversation sauvegardée</p>}
      {conversations.map((conversation) => (
        <button
          key={conversation.id}
          className={conversation.id === currentId ? 'conversation current' : 'conversation'}
          onClick={() => onOpen(conversation.id)}
          disabled={disabled}
        >
          <span className="title">{conversation.title || 'Sans titre'}</span>
          <span className="date">{conversation.updated}</span>
        </button>
      ))}
    </aside>
  )
}

export default ConversationList
