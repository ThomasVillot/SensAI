function parseText(value) {
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

function ToolValue({ value }) {
  if (typeof value === 'string') {
    const parsed = parseText(value)
    if (typeof parsed === 'object' && parsed !== null) {
      return <ToolValue value={parsed} />
    }
    if (value.startsWith('http')) {
      return (
        <a href={value} target="_blank" rel="noreferrer">
          {value}
        </a>
      )
    }
    return <span>{value}</span>
  }

  if (Array.isArray(value)) {
    if (value.length === 0) {
      return <span>aucun résultat</span>
    }
    return (
      <ul>
        {value.map((item, index) => (
          <li key={index}>
            <ToolValue value={item} />
          </li>
        ))}
      </ul>
    )
  }

  if (typeof value === 'object' && value !== null) {
    return (
      <ul>
        {Object.entries(value).map(([key, item]) => (
          <li key={key}>
            <strong>{key} :</strong> <ToolValue value={item} />
          </li>
        ))}
      </ul>
    )
  }

  return <span>{String(value)}</span>
}

function ToolCall({ tool }) {
  const finished = tool.result !== undefined

  return (
    <details className="tool-call">
      <summary>
        SensAI utilise {tool.name}
        {!finished && ' …'}
      </summary>
      {finished && (
        <div className="tool-result">
          <ToolValue value={tool.result} />
        </div>
      )}
    </details>
  )
}

export default ToolCall
