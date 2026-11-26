import { useEffect, useState } from 'react'
import {
  changeCache,
  clearTrip,
  createConversation,
  fetchCache,
  fetchConversations,
  fetchDocument,
  fetchModels,
  fetchProfiles,
  fetchPrompt,
  fetchWarnings,
  installModel,
  openConversation,
  revokeShare,
  shareConversation,
  shareDocument,
  streamChat,
} from './api.js'
import CacheSelect from './components/CacheSelect.jsx'
import ChatInput from './components/ChatInput.jsx'
import ConversationList from './components/ConversationList.jsx'
import DocumentPanel from './components/DocumentPanel.jsx'
import ExportSelect from './components/ExportSelect.jsx'
import MessageList from './components/MessageList.jsx'
import ModelSelect, { isInstalled } from './components/ModelSelect.jsx'
import ProfileSelect from './components/ProfileSelect.jsx'
import PromptPanel from './components/PromptPanel.jsx'
import SharePanel from './components/SharePanel.jsx'
import { toChatMessages } from './history.js'

function describeResume(turns, archivedTurns) {
  const text = `Conversation reprise : ${turns} échange(s) rechargé(s)`
  if (!archivedTurns) {
    return text
  }
  return `${text}, ${archivedTurns} plus ancien(s) non rechargé(s) : SensAI ne s'en souvient plus`
}

function App() {
  const [models, setModels] = useState([])
  const [recommendedModels, setRecommendedModels] = useState([])
  const [suggestedModels, setSuggestedModels] = useState([])
  const [model, setModel] = useState('')
  const [installing, setInstalling] = useState(false)
  const [installStatus, setInstallStatus] = useState('')
  const [profiles, setProfiles] = useState([])
  const [profile, setProfile] = useState('')
  const [cacheMode, setCacheMode] = useState('naif')
  const [conversationId, setConversationId] = useState(null)
  const [messages, setMessages] = useState([])
  const [busy, setBusy] = useState(false)
  const [prompt, setPrompt] = useState(null)
  const [tripDocument, setTripDocument] = useState(null)
  const [shareLink, setShareLink] = useState(null)
  const [replyController, setReplyController] = useState(null)
  const [savedConversations, setSavedConversations] = useState([])
  const [notice, setNotice] = useState(null)
  const [draft, setDraft] = useState('')

  useEffect(() => {
    fetchModels()
      .then((data) => {
        setModels(data.installed)
        setRecommendedModels(data.recommended)
        setSuggestedModels(data.suggested)
        setModel(data.default)
      })
      .catch(() => setModels([]))
    fetchProfiles()
      .then(setProfiles)
      .catch(() => setProfiles([]))
    fetchCache()
      .then(setCacheMode)
      .catch(() => {})
    fetchWarnings()
      .then((warnings) => {
        if (warnings.length > 0) {
          setNotice({ kind: 'warning', text: `Avertissement : ${warnings.join(' · ')}` })
        }
      })
      .catch(() => {})
    refreshConversations()
  }, [])

  useEffect(() => {
    if (!notice) {
      return
    }
    const timer = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(timer)
  }, [notice])

  function refreshConversations() {
    fetchConversations()
      .then(setSavedConversations)
      .catch(() => setSavedConversations([]))
  }

  async function chooseModel(name) {
    if (isInstalled(models, name)) {
      setModel(name)
      return
    }
    if (!window.confirm(`Installer ${name} ? Le téléchargement peut faire plusieurs Go.`)) {
      return
    }
    setInstalling(true)
    setInstallStatus(`Installation de ${name}…`)
    try {
      await installModel(name, (event) => {
        if (event.type === 'progress') {
          setInstallStatus(`Installation de ${name} : ${event.percent} %`)
        } else if (event.type === 'error') {
          throw new Error(event.message)
        }
      })
      const data = await fetchModels()
      setModels(data.installed)
      setModel(name)
      setInstallStatus('')
      setNotice({ kind: 'success', text: `${name} est installé` })
    } catch (error) {
      setInstallStatus(`Installation impossible : ${error.message}`)
    } finally {
      setInstalling(false)
    }
  }

  async function chooseCache(mode) {
    setBusy(true)
    try {
      setCacheMode(await changeCache(mode))
      setNotice({ kind: 'success', text: mode === 'semantic' ? 'Cache sémantique activé' : 'Cache naïf activé' })
    } catch (error) {
      setNotice({ kind: 'warning', text: `Changement de cache impossible : ${error.message}` })
    } finally {
      setBusy(false)
    }
  }

  function updateLastMessage(changes) {
    setMessages((current) => {
      const last = current[current.length - 1]
      return [...current.slice(0, -1), { ...last, ...changes(last) }]
    })
  }

  function addText(parts, text) {
    const last = parts[parts.length - 1]
    if (last && last.type === 'text') {
      return [...parts.slice(0, -1), { ...last, content: last.content + text }]
    }
    return [...parts, { type: 'text', content: text }]
  }

  function handleEvent(event) {
    if (event.type === 'step') {
      updateLastMessage(() => ({ step: event }))
    } else if (event.type === 'token') {
      updateLastMessage((last) => ({ parts: addText(last.parts, event.content) }))
    } else if (event.type === 'tool_call') {
      updateLastMessage((last) => ({
        parts: [...last.parts, { type: 'tool', name: event.name, arguments: event.arguments }],
      }))
    } else if (event.type === 'tool_result') {
      updateLastMessage((last) => ({
        parts: [...last.parts.slice(0, -1), { ...last.parts[last.parts.length - 1], result: event.result }],
      }))
      if (event.name === 'document_edit') {
        refreshDocument()
      }
    } else if (event.type === 'no_tools') {
      updateLastMessage(() => ({ noTools: true }))
    } else if (event.type === 'stats') {
      updateLastMessage(() => ({ stats: event }))
    } else if (event.type === 'guardrail') {
      updateLastMessage((last) => ({
        parts: last.parts.filter((part) => part.type === 'tool'),
        blocked: event.message,
      }))
    } else if (event.type === 'error') {
      updateLastMessage(() => ({ error: event.message }))
    }
  }

  async function sendMessage(text) {
    const controller = new AbortController()
    setReplyController(controller)
    setBusy(true)
    setMessages((current) => [
      ...current,
      { role: 'user', content: text },
      { role: 'assistant', parts: [], stats: null, error: null },
    ])
    try {
      let id = conversationId
      if (!id) {
        id = await createConversation()
        setConversationId(id)
      }
      await streamChat(id, text, model, profile, handleEvent, controller.signal)
    } catch (error) {
      if (error.name === 'AbortError') {
        setMessages((current) => current.slice(0, -2))
        setDraft(text)
      } else {
        updateLastMessage(() => ({ error: `Le serveur ne répond pas (${error.message}).` }))
      }
    } finally {
      setReplyController(null)
      setBusy(false)
      refreshConversations()
    }
  }

  function stopReply() {
    replyController?.abort()
  }

  function newConversation() {
    setConversationId(null)
    setMessages([])
  }

  async function openSavedConversation(id) {
    try {
      const data = await openConversation(id)
      setConversationId(id)
      setMessages(toChatMessages(data.messages))
      setNotice({ kind: 'warning', text: describeResume(data.turns, data.archived_turns) })
    } catch {
      refreshConversations()
    }
  }

  function exportConversation(format) {
    window.location.href = `/api/conversations/${encodeURIComponent(conversationId)}/export?format=${format}`
  }

  async function showPrompt() {
    setTripDocument(null)
    setShareLink(null)
    setPrompt({ status: 'loading' })
    try {
      const text = await fetchPrompt(profile)
      setPrompt({ status: 'ready', text })
    } catch (error) {
      setPrompt({ status: 'error', error: error.message })
    }
  }

  async function showDocument() {
    setPrompt(null)
    setShareLink(null)
    setTripDocument({ status: 'loading' })
    try {
      const markdown = await fetchDocument()
      setTripDocument({ status: 'ready', markdown })
    } catch (error) {
      setTripDocument({ status: 'error', error: error.message })
    }
  }

  function refreshDocument() {
    fetchDocument()
      .then((markdown) => {
        setPrompt(null)
        setShareLink(null)
        setTripDocument({ status: 'ready', markdown })
      })
      .catch(() => {})
  }

  async function createShareLink(subject, request) {
    const question = `Créer un lien public vers ${subject} ? Toute personne qui a le lien pourra le lire sans se connecter.`
    if (!window.confirm(question)) {
      return
    }
    setPrompt(null)
    setTripDocument(null)
    setShareLink({ status: 'loading' })
    try {
      const data = await request()
      setShareLink({ status: 'ready', subject, ...data })
    } catch (error) {
      setShareLink({ status: 'error', error: error.message })
    }
  }

  async function copyShareLink() {
    await navigator.clipboard.writeText(shareLink.url)
    setNotice({ kind: 'success', text: 'Lien copié' })
  }

  async function revokeShareLink() {
    try {
      await revokeShare(shareLink.token)
      setShareLink(null)
      setNotice({ kind: 'success', text: 'Lien révoqué' })
    } catch (error) {
      setNotice({ kind: 'warning', text: `Révocation impossible : ${error.message}` })
    }
  }

  async function eraseTrip() {
    if (!window.confirm('Effacer toute la mémoire du voyage (voyage, dépenses, lieux, document) ?')) {
      return
    }
    try {
      const cleared = await clearTrip()
      setTripDocument(null)
      setNotice({ kind: 'success', text: cleared ? 'Mémoire du voyage effacée' : 'La mémoire du voyage est déjà vide' })
    } catch (error) {
      setNotice({ kind: 'warning', text: `Effacement impossible : ${error.message}` })
    }
  }

  const hasFinishedTurn = messages.some((message) => message.stats || message.restored)

  return (
    <div className="page">
      <ConversationList
        conversations={savedConversations}
        currentId={conversationId}
        onOpen={openSavedConversation}
        disabled={busy}
      />
      <div className="app">
        <header className="header">
          <h1>SensAI</h1>
          <ModelSelect
            recommended={recommendedModels}
            suggested={suggestedModels}
            installed={models}
            value={model}
            onChange={chooseModel}
            disabled={busy || installing}
          />
          <ProfileSelect
            profiles={profiles}
            value={profile}
            onChange={setProfile}
            disabled={busy || messages.length > 0}
          />
          <CacheSelect value={cacheMode} onChange={chooseCache} disabled={busy} />
          <button className="secondary" onClick={newConversation} disabled={busy}>
            Nouvelle conversation
          </button>
          <ExportSelect
            onExport={exportConversation}
            onShare={() => createShareLink('cette conversation', () => shareConversation(conversationId))}
            disabled={busy || !hasFinishedTurn}
          />
          <button className="secondary" onClick={showDocument} disabled={busy}>
            Document
          </button>
          <button className="secondary" onClick={showPrompt}>
            Prompt système
          </button>
        </header>
        {installStatus && <div className="install-status">{installStatus}</div>}
        <MessageList messages={messages} />
        <ChatInput
          text={draft}
          onTextChange={setDraft}
          onSend={sendMessage}
          onStop={stopReply}
          disabled={busy}
          replying={replyController !== null}
        />
      </div>
      {notice && <div className={`notice ${notice.kind}`}>{notice.text}</div>}
      {tripDocument && (
        <DocumentPanel
          tripDocument={tripDocument}
          onClose={() => setTripDocument(null)}
          onShare={() => createShareLink("le document d'itinéraire", shareDocument)}
          onErase={eraseTrip}
        />
      )}
      {shareLink && (
        <SharePanel
          shareLink={shareLink}
          onCopy={copyShareLink}
          onRevoke={revokeShareLink}
          onClose={() => setShareLink(null)}
        />
      )}
      {prompt && <PromptPanel prompt={prompt} onClose={() => setPrompt(null)} />}
    </div>
  )
}

export default App
