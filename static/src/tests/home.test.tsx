import { render, screen, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { Provider } from 'react-redux';
import { configureStore } from '@reduxjs/toolkit';
import { ScheduleOverview } from '@/components/home';
import { RootState } from '@/types';
import { assetsReducer, assetModalReducer } from '@/store/assets';
import settingsReducer from '@/store/settings';
import websocketReducer from '@/store/websocket';

const initialState: RootState = {
  assets: {
    items: [
      {
        asset_id: 'ff18e72b5a2447fab372f5effa0797b1',
        name: 'https://react.dev/',
        uri: 'https://react.dev/',
        start_date: '2025-07-07T22:51:55.640000Z',
        end_date: '2025-08-06T22:51:55.640000Z',
        duration: 10,
        mimetype: 'webpage',
        is_enabled: 1,
        nocache: false,
        play_order: 0,
        skip_asset_check: false,
        is_active: true,
        is_processing: false,
      },
      {
        asset_id: '5bbf68491a0d4461bfe860911265b8be',
        name: 'https://angular.dev/',
        uri: 'https://angular.dev/',
        start_date: '2025-07-07T22:52:47.421000Z',
        end_date: '2025-08-06T22:52:47.421000Z',
        duration: 10,
        mimetype: 'webpage',
        is_enabled: 1,
        nocache: false,
        play_order: 1,
        skip_asset_check: false,
        is_active: true,
        is_processing: false,
      },
      {
        asset_id: '6eb86ce9d5c14597ae68017d4dd93900',
        name: 'https://vuejs.org/',
        uri: 'https://vuejs.org/',
        start_date: '2025-07-07T22:52:58.934000Z',
        end_date: '2025-08-06T22:52:58.934000Z',
        duration: 10,
        mimetype: 'webpage',
        is_enabled: 1,
        nocache: false,
        play_order: 2,
        skip_asset_check: false,
        is_active: true,
        is_processing: false,
      },
    ],
    status: 'succeeded',
    error: null,
  },
  assetModal: {
    activeTab: 'uri',
    formData: {
      uri: '',
      skipAssetCheck: false,
    },
    isValid: true,
    errorMessage: '',
    statusMessage: '',
    isSubmitting: false,
    uploadProgress: 0,
  },
  settings: {
    settings: {
      playerName: '',
      defaultDuration: 10,
      defaultStreamingDuration: 300,
      audioOutput: 'hdmi',
      dateFormat: 'mm/dd/yyyy',
      authBackend: '',
      currentPassword: '',
      user: '',
      password: '',
      confirmPassword: '',
      showSplash: true,
      defaultAssets: false,
      shufflePlaylist: false,
      use24HourClock: false,
      debugLogging: false,
    },
    deviceModel: '',
    prevAuthBackend: '',
    hasSavedBasicAuth: false,
    isLoading: false,
    isUploading: false,
    uploadProgress: 0,
    error: null,
  },
  websocket: {
    isConnected: false,
    isConnecting: false,
    error: null,
    lastMessage: null,
    reconnectAttempts: 0,
  },
};

const createTestStore = (preloadedState = {}) => {
  return configureStore({
    reducer: {
      assets: assetsReducer,
      assetModal: assetModalReducer,
      settings: settingsReducer,
      websocket: websocketReducer,
    },
    preloadedState,
  });
};

const renderWithRedux = (
  component: React.ReactElement,
  state: RootState = initialState,
) => {
  const store = createTestStore(state);
  return {
    ...render(<Provider store={store}>{component}</Provider>),
    store,
  };
};

describe('ScheduleOverview', () => {
  it('renders the home page', async () => {
    await act(async () => {
      renderWithRedux(<ScheduleOverview />);
    });

    expect(screen.getByText('Schedule Overview')).toBeInTheDocument();

    expect(screen.getByText('https://react.dev/')).toBeInTheDocument();
    expect(screen.getByText('https://angular.dev/')).toBeInTheDocument();
    expect(screen.getByText('https://vuejs.org/')).toBeInTheDocument();
  });
});
