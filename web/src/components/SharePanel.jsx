function SharePanel({ shareLink, onCopy, onRevoke, onClose }) {
  return (
    <aside className="itinerary">
      <div className="itinerary-header">
        <h2>Lien de partage</h2>
        <button className="secondary" onClick={onClose}>
          Fermer
        </button>
      </div>
      {shareLink.status === 'loading' && <p className="typing">Création du lien…</p>}
      {shareLink.status === 'error' && <div className="error">{shareLink.error}</div>}
      {shareLink.status === 'ready' && (
        <div className="share">
          <p>
            Toute personne qui a ce lien peut lire {shareLink.subject} sans se connecter, jusqu'au{' '}
            {shareLink.expires}.
          </p>
          <a href={shareLink.url} target="_blank" rel="noreferrer">
            {shareLink.url}
          </a>
          {shareLink.warning && <div className="error">{shareLink.warning}</div>}
          <p className="share-note">Le lien s'ouvre depuis les appareils du même réseau tant que SensAI reste lancé.</p>
          <button className="secondary" onClick={onCopy}>
            Copier
          </button>
          <button className="secondary danger" onClick={onRevoke}>
            Révoquer
          </button>
        </div>
      )}
    </aside>
  )
}

export default SharePanel
