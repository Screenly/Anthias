import { useEffect } from 'react'

export const Integrations = () => {
  useEffect(() => {
    document.title = 'Integrations'
  }, [])

  return (
    <div className="container">
      <h2 className="text-white">Integrations</h2>
    </div>
  )
}
