import { Routes, Route } from 'react-router';
import { useEffect } from 'react';
import { useDispatch } from 'react-redux';
import { fetchAssets } from '@/store/assets';
import { fetchSettings } from '@/store/settings';
import { connectWebSocket } from '@/store/websocket';
import { store } from '@/store/index';

import { Integrations } from '@/components/integrations';
import { Navbar } from '@/components/navbar';
import { ScheduleOverview } from '@/components/home';
import { Settings } from '@/components/settings';
import { SystemInfo } from '@/components/system-info';
import { Footer } from '@/components/footer';

export const App = () => {
  const dispatch = useDispatch<typeof store.dispatch>();

  useEffect(() => {
    dispatch(fetchAssets());
    dispatch(fetchSettings());
    dispatch(connectWebSocket());
  }, [dispatch]);

  return (
    <>
      <Navbar />

      <Routes>
        <Route path="/" element={<ScheduleOverview />} />
        <Route path="/integrations" element={<Integrations />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/system-info" element={<SystemInfo />} />
      </Routes>

      <div className="container mt-4">
        <div className="row">
          <div className="col-6 small text-white">
            <span>
              Want to get more out of your digital signage?
              <a
                className="brand"
                href="https://www.screenly.io/?utm_source=Anthias&utm_medium=root-page&utm_campaign=UI"
                target="_blank"
                rel="noopener noreferrer"
              >
                {' '}
                <strong>Try Screenly</strong>.
              </a>
            </span>
          </div>
        </div>
      </div>

      <Footer />
    </>
  );
};
