import { Routes, Route } from 'react-router'
import { useEffect } from 'react'
import { useDispatch } from 'react-redux'
import { fetchDeviceSettings } from '@/store/assets/asset-modal-slice'

import { Integrations } from '@/components/integrations'
import { Navbar } from '@/components/navbar'
import { ScheduleOverview } from '@/components/home'
import { Settings } from '@/components/settings'
import { SystemInfo } from '@/components/system-info'

export const App = () => {
  const dispatch = useDispatch()

  useEffect(() => {
    dispatch(fetchDeviceSettings())
  }, [dispatch])

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
