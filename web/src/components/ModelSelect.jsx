import { useEffect, useRef, useState } from 'react'

export function isInstalled(installed, name) {
  return installed.includes(name) || installed.includes(`${name}:latest`)
}

function ModelSelect({ recommended, suggested, installed, value, onChange, disabled }) {
  const [open, setOpen] = useState(false)
  const menuRef = useRef(null)
  const known = [...recommended, ...suggested]
  const others = installed.filter((name) => !isInstalled(known, name.replace(/:latest$/, '')))

  useEffect(() => {
    if (!open) {
      return
    }
    function closeOnClickOutside(event) {
      if (!menuRef.current.contains(event.target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', closeOnClickOutside)
    return () => document.removeEventListener('mousedown', closeOnClickOutside)
  }, [open])

  function choose(name) {
    setOpen(false)
    onChange(name)
  }

  function option(name) {
    const classes = ['model-option']
    if (!isInstalled(installed, name)) {
      classes.push('not-installed')
    }
    if (name === value) {
      classes.push('selected')
    }
    return (
      <button key={name} type="button" className={classes.join(' ')} onClick={() => choose(name)}>
        {isInstalled(installed, name) ? name : `${name} (à installer)`}
      </button>
    )
  }

  function group(label, names) {
    if (names.length === 0) {
      return null
    }
    const ready = names.filter((name) => isInstalled(installed, name))
    const toInstall = names.filter((name) => !isInstalled(installed, name))
    return (
      <div key={label}>
        <div className="model-group">{label}</div>
        {[...ready, ...toInstall].map(option)}
      </div>
    )
  }

  return (
    <div className="models">
      <div className="model-menu" ref={menuRef}>
        <button type="button" className="model-current" onClick={() => setOpen(!open)} disabled={disabled}>
          {value} ▾
        </button>
        {open && (
          <div className="model-list">
            {group('Conseillé', recommended)}
            {group('Proposés', suggested)}
            {group('Déjà installés', others)}
          </div>
        )}
      </div>
    </div>
  )
}

export default ModelSelect
