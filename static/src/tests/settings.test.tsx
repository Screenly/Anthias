import { test, expect, beforeAll, afterEach, afterAll, mock } from 'bun:test'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'
import Swal from 'sweetalert2'
import { Settings } from '@/components/settings/index'
import settingsReducer from '@/store/settings'
import { RootState } from '@/types'
import { createMockServer } from '@/tests/utils'

// Mock SweetAlert2
mock.module('sweetalert2', () => ({
  default: {
    fire: mock(() => Promise.resolve({ isConfirmed: true })),
  },
}))

// Mock document.title
Object.defineProperty(document, 'title', {
  writable: true,
  value: '',
})

const server = createMockServer()

beforeAll(() => server.listen())
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

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

const testFormSubmission = async (
  shouldSucceed: boolean,
  expectedMessage: string,
) => {
  const mockUnwrap = shouldSucceed
    ? mock(() => Promise.resolve({}))
    : mock(() => Promise.reject(new Error('Save failed')))

  const mockDispatch = mock(() => ({ unwrap: mockUnwrap }))

  const store = createMockStore()
  store.dispatch = mockDispatch

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

  await waitFor(() => {
    expect(Swal.fire).toHaveBeenCalledWith(
      expect.objectContaining({
        title: shouldSucceed ? 'Success!' : 'Error!',
        text: expectedMessage,
        icon: shouldSucceed ? 'success' : 'error',
      }),
    )
  })
}

test('Settings Component renders settings form with all components', async () => {
  renderWithProvider(<Settings />)

  expect(screen.getByText('Settings')).toBeInTheDocument()

  // Wait for the component to load and render the save button
  await waitFor(() => {
    expect(screen.getByText('Save Settings')).toBeInTheDocument()
  })
})

test('Settings displays player name from settings', async () => {
  renderWithProvider(<Settings />)

  await waitFor(() => {
    expect(screen.getByDisplayValue('Test Player')).toBeInTheDocument()
  })
})

test('Settings updates document title with player name', async () => {
  renderWithProvider(<Settings />)

  await waitFor(() => {
    expect(document.title).toBe('Test Player · Settings')
  })
})

test('Settings handles input changes for text fields', async () => {
  renderWithProvider(<Settings />)

  const playerNameInput = screen.getByDisplayValue(
    'Test Player',
  ) as HTMLInputElement
  fireEvent.change(playerNameInput, { target: { value: 'New Player Name' } })

  await waitFor(() => {
    expect(playerNameInput.value).toBe('New Player Name')
  })
})

test('Settings handles checkbox changes', async () => {
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

test('Settings shows loading state when submitting form', async () => {
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

test('Settings handles successful form submission', async () => {
  await testFormSubmission(true, 'Settings were successfully saved.')
})

test('Settings shows success message on successful save', async () => {
  await testFormSubmission(true, 'Settings were successfully saved.')
})

test('Settings shows error message on failed save', async () => {
  await testFormSubmission(false, 'Save failed')
})

test('Settings fetches settings and device model on mount', async () => {
  const mockDispatch = mock(() => {})
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
