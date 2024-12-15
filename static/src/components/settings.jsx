import { useEffect } from 'react'

export const Settings = () => {
  useEffect(() => {
    document.title = 'Settings'
  }, [])

  return (
    <div className="container">
      <h2 className="text-white">Settings</h2>
    </div>
  )
}
