import {
  RootState,
  SystemInfoResponse,
  DeviceSettingsResponse,
  IntegrationsResponse,
} from '@/types'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'

interface MockResponses {
  info?: Partial<SystemInfoResponse>
  deviceSettings?: Partial<DeviceSettingsResponse>
  integrations?: Partial<IntegrationsResponse>
}

export function createMockServer(overrides: Partial<MockResponses> = {}) {
  const defaultInfo: SystemInfoResponse = {
    loadavg: 1.58,
    free_space: '31G',
    display_power: 'CEC error',
    uptime: {
      days: 8,
      hours: 18.56,
    },
    memory: {
      total: 15659,
      used: 9768,
      free: 1522,
      shared: 1439,
      buff: 60,
      available: 3927,
    },
    device_model: 'Generic x86_64 Device',
    anthias_version: 'master@3a4747f',
    mac_address: 'Unable to retrieve MAC address.',
    ip_addresses: ['192.168.1.100', '10.0.0.50'],
    host_user: 'pi',
  }

  const defaultDeviceSettings: DeviceSettingsResponse = {
    player_name: 'Test Player',
  }

  const defaultIntegrations: IntegrationsResponse = {
    is_balena: false,
  }

  return setupServer(
    http.get('/api/v2/info', () => {
      return HttpResponse.json({
        ...defaultInfo,
        ...overrides.info,
      })
    }),
    http.get('/api/v2/device_settings', () => {
      return HttpResponse.json({
        ...defaultDeviceSettings,
        ...overrides.deviceSettings,
      })
    }),
    http.patch('/api/v2/device_settings', () => {
      return HttpResponse.json({
        message: 'Settings were successfully saved.',
      })
    }),
    http.get('/api/v2/integrations', () => {
      return HttpResponse.json({
        ...defaultIntegrations,
        ...overrides.integrations,
      })
    }),
  )
}

export function getInitialState(): RootState {
  return {
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
  }
}
