import { Routes, Route } from 'react-router'
import { useEffect } from 'react'
import { useDispatch } from 'react-redux'
import { fetchAssets } from '@/store/assets'
import { fetchSettings } from '@/store/settings'
import { connectWebSocket, disconnectWebSocket } from '@/store/websocket'
import { store } from '@/store/index'

import { Integrations } from '@/components/integrations'
import { Navbar } from '@/components/navbar'
import { ScheduleOverview } from '@/components/home'
import { Settings } from '@/components/settings'
import { SystemInfo } from '@/components/system-info'
import { Footer } from '@/components/footer'
import Http404 from '@/components/http-404'

export const App = () => {
  const dispatch = useDispatch<typeof store.dispatch>()

  useEffect(() => {
    dispatch(fetchAssets())
    dispatch(fetchSettings())
    dispatch(connectWebSocket())

    // Cleanup function to disconnect WebSocket when component unmounts
    return () => {
      dispatch(disconnectWebSocket())
    }
  }, [dispatch])

  return (
    <>
      <Navbar />

      <Routes>
        <Route path="/" element={<ScheduleOverview />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/system-info" element={<SystemInfo />} />
        <Route path="*" element={<Http404 />} />
      </Routes>

      <Footer />
    </>
  )
}
