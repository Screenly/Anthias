import { Navbar } from '@/components/navbar'
import { Routes, Route } from 'react-router'

import { ScheduleOverview } from '@/components/home'

const Integrations = () => {
  return (
    <div className="container">
      <h2 className="text-white">Integrations</h2>
    </div>
  )
}

const Settings = () => {
  return (
    <div className="container">
      <h2 className="text-white">Settings</h2>
    </div>
  )
}

const SystemInfo = () => {
  return (
    <div className="container">
      <h2 className="text-white">System Info</h2>
    </div>
  )
}

export const App = () => {
  return (
    <>
      <Navbar />

      <Routes>
        <Route path="/" element={<ScheduleOverview />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/system-info" element={<SystemInfo />} />
      </Routes>
    </>
  )
}
