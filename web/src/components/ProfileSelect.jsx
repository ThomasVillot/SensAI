function ProfileSelect({ profiles, value, onChange, disabled }) {
  return (
    <select
      className="profile"
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      title="Le profil se choisit avant de commencer ou de reprendre une conversation"
    >
      <option value="">Aucun profil</option>
      {profiles.map((name) => (
        <option key={name} value={name}>
          Profil {name}
        </option>
      ))}
    </select>
  )
}

export default ProfileSelect
