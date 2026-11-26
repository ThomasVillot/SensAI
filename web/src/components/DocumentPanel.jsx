import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

function download(format) {
  window.location.href = `/api/document/export?format=${format}`
}

function DocumentPanel({ tripDocument, onClose, onShare, onErase }) {
  return (
    <aside className="itinerary">
      <div className="itinerary-header">
        <h2>Document du voyage</h2>
        <button className="secondary" onClick={onClose}>
          Fermer
        </button>
      </div>
      {tripDocument.status === 'loading' && <p className="typing">Chargement…</p>}
      {tripDocument.status === 'error' && <div className="error">{tripDocument.error}</div>}
      {tripDocument.status === 'ready' && (
        <div className="trip-document">
          <Markdown remarkPlugins={[remarkGfm]}>{tripDocument.markdown}</Markdown>
          <button className="secondary" onClick={() => download('md')}>
            Markdown
          </button>
          <button className="secondary" onClick={() => download('json')}>
            JSON
          </button>
          <button className="secondary" onClick={() => download('pdf')}>
            PDF
          </button>
          <button className="secondary" onClick={onShare}>
            Partager
          </button>
          <button className="secondary danger" onClick={onErase}>
            Effacer le voyage
          </button>
        </div>
      )}
    </aside>
  )
}

export default DocumentPanel
