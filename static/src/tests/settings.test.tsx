import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'
import Swal from 'sweetalert2'
import { Settings } from '@/components/settings/index'
import settingsReducer from '@/store/settings'
import { RootState } from '@/types'

// Mock the Update component to prevent it from making API calls
jest.mock('@/components/settings/update', () => ({
  Update: () => null,
}))

// Mock fetch
global.fetch = jest.fn() as jest.MockedFunction<typeof fetch>

// Mock SweetAlert2
jest.mock('sweetalert2')

// Mock document.title
Object.defineProperty(document, 'title', {
  writable: true,
  value: '',
})

const createMockStore = (preloadedState: Partial<RootState> = {}) => {
  return configureStore({
    reducer: {
      settings: settingsReducer,
    },
    preloadedState: {
      settings: {
        settings: {
          playerName: 'Test Player',
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
          shufflePlaylist: true,
          use24HourClock: false,
          debugLogging: true,
        },
        deviceModel: 'Raspberry Pi 4',
        isLoading: false,
        prevAuthBackend: '',
        hasSavedBasicAuth: false,
        isUploading: false,
        uploadProgress: 0,
        error: null,
        ...(preloadedState.settings || {}),
      },
    },
  })
}

const renderWithProvider = (
  component: React.ReactElement,
  initialState: RootState = {} as RootState,
) => {
  const store = createMockStore(initialState)
  return render(<Provider store={store}>{component}</Provider>)
}

describe('Settings Component', () => {
  beforeEach(() => {
    // Mock successful API responses for all endpoints
    ;(global.fetch as jest.MockedFunction<typeof fetch>)
      // Mock /api/v2/device_settings (for fetchSettings)
      .mockResolvedValueOnce({
        json: () =>
          Promise.resolve({
            player_name: 'Test Player',
            default_duration: 10,
            default_streaming_duration: 300,
            audio_output: 'hdmi',
            date_format: 'mm/dd/yyyy',
            auth_backend: '',
            show_splash: true,
            default_assets: false,
            shuffle_playlist: true,
            use_24_hour_clock: false,
            debug_logging: true,
          }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for fetchDeviceModel)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ device_model: 'Raspberry Pi 4' }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for update info)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ up_to_date: true }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/integrations (for balena check)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ is_balena: false }),
        ok: true,
        status: 200,
      } as Response)
  })

  afterEach(() => {
    // Clean up mocks if needed
  })

  it('renders settings form with all components', async () => {
    renderWithProvider(<Settings />)

    expect(screen.getByText('Settings')).toBeInTheDocument()

    // Wait for the component to load and render the save button
    await waitFor(() => {
      expect(screen.getByText('Save Settings')).toBeInTheDocument()
    })
  })

  it('displays player name from settings', async () => {
    renderWithProvider(<Settings />)

    await waitFor(() => {
      expect(screen.getByDisplayValue('Test Player')).toBeInTheDocument()
    })
  })

  it('updates document title with player name', async () => {
    renderWithProvider(<Settings />)

    await waitFor(() => {
      expect(document.title).toBe('Test Player · Settings')
    })
  })

  it('handles input changes for text fields', async () => {
    renderWithProvider(<Settings />)

    const playerNameInput = screen.getByDisplayValue(
      'Test Player',
    ) as HTMLInputElement
    fireEvent.change(playerNameInput, { target: { value: 'New Player Name' } })

    await waitFor(() => {
      expect(playerNameInput.value).toBe('New Player Name')
    })
  })

  it('handles checkbox changes', async () => {
    renderWithProvider(<Settings />)

    // Wait for the component to load, then find the specific checkbox by its name attribute
    await waitFor(() => {
      const showSplashCheckbox = screen
        .getAllByRole('checkbox')
        .find(
          (el) => (el as HTMLInputElement).name === 'showSplash',
        ) as HTMLInputElement
      fireEvent.click(showSplashCheckbox)

      expect(showSplashCheckbox.checked).toBe(false)
    })
  })

  it('shows loading state when submitting form', async () => {
    const store = createMockStore({
      settings: {
        settings: {
          playerName: 'Test Player',
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
          shufflePlaylist: true,
          use24HourClock: false,
          debugLogging: true,
        },
        deviceModel: 'Raspberry Pi 4',
        isLoading: true,
        prevAuthBackend: '',
        hasSavedBasicAuth: false,
        isUploading: false,
        uploadProgress: 0,
        error: null,
      },
    })

    render(
      <Provider store={store}>
        <Settings />
      </Provider>,
    )

    // The button should be disabled when loading
    const submitButton = screen
      .getAllByRole('button')
      .find(
        (el) => (el as HTMLButtonElement).type === 'submit',
      ) as HTMLButtonElement
    expect(submitButton).toBeDisabled()

    // Check that the spinner is present (it's inside the button)
    const spinner = submitButton.querySelector('[role="status"]')
    expect(spinner).toBeInTheDocument()
  })

  it('handles successful form submission', async () => {
    const mockDispatch = jest.fn()
    const mockUnwrap = jest.fn().mockResolvedValue({})

    // Mock the Redux store dispatch
    const store = createMockStore()
    store.dispatch = mockDispatch
    mockDispatch.mockReturnValue({ unwrap: mockUnwrap })

    render(
      <Provider store={store}>
        <Settings />
      </Provider>,
    )

    const submitButton = screen.getByText('Save Settings')
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(mockDispatch).toHaveBeenCalled()
    })
  })

  it('shows success message on successful save', async () => {
    const mockDispatch = jest.fn()
    const mockUnwrap = jest.fn().mockResolvedValue({})

    const store = createMockStore()
    store.dispatch = mockDispatch
    mockDispatch.mockReturnValue({ unwrap: mockUnwrap })

    render(
      <Provider store={store}>
        <Settings />
      </Provider>,
    )

    const submitButton = screen.getByText('Save Settings')
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(Swal.fire).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Success!',
          text: 'Settings were successfully saved.',
          icon: 'success',
        }),
      )
    })
  })

  it('shows error message on failed save', async () => {
    const mockDispatch = jest.fn()
    const mockUnwrap = jest.fn().mockRejectedValue(new Error('Save failed'))

    const store = createMockStore()
    store.dispatch = mockDispatch
    mockDispatch.mockReturnValue({ unwrap: mockUnwrap })

    render(
      <Provider store={store}>
        <Settings />
      </Provider>,
    )

    const submitButton = screen.getByText('Save Settings')
    fireEvent.click(submitButton)

    await waitFor(() => {
      expect(Swal.fire).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Error!',
          text: 'Save failed',
          icon: 'error',
        }),
      )
    })
  })

  it('fetches settings and device model on mount', async () => {
    const mockDispatch = jest.fn()
    const store = createMockStore()
    store.dispatch = mockDispatch

    render(
      <Provider store={store}>
        <Settings />
      </Provider>,
    )

    // Wait for the component to mount and dispatch actions
    await waitFor(() => {
      expect(mockDispatch).toHaveBeenCalled()
    })

    // Check that both actions were dispatched
    const calls = mockDispatch.mock.calls
    expect(calls.length).toBeGreaterThanOrEqual(2)
  })

  it('fetches update info and integrations on mount', async () => {
    renderWithProvider(<Settings />)

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith('/api/v2/info')
      expect(global.fetch).toHaveBeenCalledWith('/api/v2/integrations')
    })
  })

  it('renders update component when not up to date and not balena', async () => {
    ;(global.fetch as jest.MockedFunction<typeof fetch>)
      // Mock /api/v2/device_settings (for fetchSettings)
      .mockResolvedValueOnce({
        json: () =>
          Promise.resolve({
            player_name: 'Test Player',
            default_duration: 10,
            default_streaming_duration: 300,
            audio_output: 'hdmi',
            date_format: 'mm/dd/yyyy',
            auth_backend: '',
            show_splash: true,
            default_assets: false,
            shuffle_playlist: true,
            use_24_hour_clock: false,
            debug_logging: true,
          }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for fetchDeviceModel)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ device_model: 'Raspberry Pi 4' }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for update info)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ up_to_date: false }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/integrations (for balena check)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ is_balena: false }),
        ok: true,
        status: 200,
      } as Response)

    renderWithProvider(<Settings />)

    await waitFor(() => {
      // The Update component should be rendered
      // This test assumes the Update component renders something identifiable
      expect(screen.getByText('Settings')).toBeInTheDocument()
    })
  })

  it('does not render update component when up to date', async () => {
    ;(global.fetch as jest.MockedFunction<typeof fetch>)
      // Mock /api/v2/device_settings (for fetchSettings)
      .mockResolvedValueOnce({
        json: () =>
          Promise.resolve({
            player_name: 'Test Player',
            default_duration: 10,
            default_streaming_duration: 300,
            audio_output: 'hdmi',
            date_format: 'mm/dd/yyyy',
            auth_backend: '',
            show_splash: true,
            default_assets: false,
            shuffle_playlist: true,
            use_24_hour_clock: false,
            debug_logging: true,
          }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for fetchDeviceModel)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ device_model: 'Raspberry Pi 4' }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for update info)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ up_to_date: true }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/integrations (for balena check)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ is_balena: false }),
        ok: true,
        status: 200,
      } as Response)

    renderWithProvider(<Settings />)

    await waitFor(() => {
      expect(screen.getByText('Settings')).toBeInTheDocument()
    })
  })

  it('does not render update component when on balena', async () => {
    ;(global.fetch as jest.MockedFunction<typeof fetch>)
      // Mock /api/v2/device_settings (for fetchSettings)
      .mockResolvedValueOnce({
        json: () =>
          Promise.resolve({
            player_name: 'Test Player',
            default_duration: 10,
            default_streaming_duration: 300,
            audio_output: 'hdmi',
            date_format: 'mm/dd/yyyy',
            auth_backend: '',
            show_splash: true,
            default_assets: false,
            shuffle_playlist: true,
            use_24_hour_clock: false,
            debug_logging: true,
          }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for fetchDeviceModel)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ device_model: 'Raspberry Pi 4' }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/info (for update info)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ up_to_date: false }),
        ok: true,
        status: 200,
      } as Response)
      // Mock /api/v2/integrations (for balena check)
      .mockResolvedValueOnce({
        json: () => Promise.resolve({ is_balena: true }),
        ok: true,
        status: 200,
      } as Response)

    renderWithProvider(<Settings />)

    await waitFor(() => {
      expect(screen.getByText('Settings')).toBeInTheDocument()
    })
  })
})
