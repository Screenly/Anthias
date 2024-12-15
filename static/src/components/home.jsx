import { useEffect } from 'react'

export const ScheduleOverview = () => {
  useEffect(() => {
    document.title = 'Schedule Overview'
  }, [])
  return (
    <div className="container">
      <h2 className="text-white">Schedule Overview</h2>
    </div>
  )
}
