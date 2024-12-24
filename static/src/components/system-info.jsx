import { useEffect } from 'react'

export const SystemInfo = () => {
  useEffect(() => {
    document.title = 'System Info'
  }, [])

  return (
    <div className="container">
      <h2 className="text-white">System Info</h2>
    </div>
  )
}
