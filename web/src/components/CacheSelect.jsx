function CacheSelect({ value, onChange, disabled }) {
  return (
    <select
      className="profile"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      title="Cache des recherches web"
    >
      <option value="naif">Cache naïf</option>
      <option value="semantic">Cache sémantique</option>
    </select>
  )
}

export default CacheSelect
