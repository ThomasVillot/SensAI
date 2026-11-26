function ExportSelect({ onExport, onShare, disabled }) {
  function choose(value) {
    if (value === 'share') {
      onShare()
    } else {
      onExport(value)
    }
  }


  return (
    <select
      className="profile"
      value=""
      onChange={(event) => choose(event.target.value)}
      disabled={disabled}
      title="Exporter ou partager la conversation"
    >
      <option value="" disabled>
        Exporter
      </option>
      <option value="md">Markdown</option>
      <option value="json">JSON</option>
      <option value="pdf">PDF</option>
      <option value="share">Lien de partage</option>
    </select>
  )
}

export default ExportSelect
