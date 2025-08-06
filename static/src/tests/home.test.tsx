import { render, screen, act, fireEvent, waitFor } from '@testing-library/react'
import '@testing-library/jest-dom'
import { Provider } from 'react-redux'
import { configureStore } from '@reduxjs/toolkit'
import { ScheduleOverview } from '@/components/home'
import { RootState } from '@/types'
import { assetsReducer, assetModalReducer } from '@/store/assets'
import settingsReducer from '@/store/settings'
import websocketReducer from '@/store/websocket'
import { getInitialState } from '@/tests/utils'

const initialState: RootState = getInitialState()

const createTestStore = (preloadedState = {}) => {
  return configureStore({
    reducer: {
      assets: assetsReducer,
      assetModal: assetModalReducer,
      settings: settingsReducer,
      websocket: websocketReducer,
    },
    preloadedState,
  })
}

const renderWithRedux = (
  component: React.ReactElement,
  state: RootState = initialState,
) => {
  const store = createTestStore(state)
  return {
    ...render(<Provider store={store}>{component}</Provider>),
    store,
  }
}

describe('ScheduleOverview', () => {
  it('renders the home page', async () => {
    await act(async () => {
      renderWithRedux(<ScheduleOverview />)
    })

    expect(screen.getByText('Schedule Overview')).toBeInTheDocument()

    expect(screen.getByText('https://react.dev/')).toBeInTheDocument()
    expect(screen.getByText('https://angular.dev/')).toBeInTheDocument()
    expect(screen.getByText('https://vuejs.org/')).toBeInTheDocument()
  })

  it('can add a new asset via URL', async () => {
    await act(async () => {
      renderWithRedux(<ScheduleOverview />)
    })

    // Verify the Add Asset button is present
    const addAssetButton = screen.getByText('Add Asset')
    expect(addAssetButton).toBeInTheDocument()

    // Click the Add Asset button to open the modal
    fireEvent.click(addAssetButton)

    // Wait for the modal to appear
    await waitFor(() => {
      expect(
        screen.getByText((content, element) => {
          return content === 'Add Asset' && element?.id === 'modalLabel'
        }),
      ).toBeInTheDocument()
    })

    // Verify modal tabs are present
    expect(screen.getByText('URL')).toBeInTheDocument()
    expect(screen.getByText('Upload')).toBeInTheDocument()

    // Find and fill the URI input field
    const uriInput = screen.getByPlaceholderText(
      // eslint-disable-next-line quotes
      "Public URL to this asset's location",
    )
    fireEvent.change(uriInput, { target: { value: 'https://example.com' } })

    // Verify the input was filled
    expect(uriInput).toHaveValue('https://example.com')

    // TODO: Fix the rest of the assertions.

    // Find and click the Save button
    // const saveButton = screen.getByRole('button', { name: 'Save' })
    // fireEvent.click(saveButton)

    // Wait for the asset to be created and appear in the list
    // await waitFor(() => {
    //   expect(screen.getByText('https://example.com')).toBeInTheDocument()
    // })
  })
})
