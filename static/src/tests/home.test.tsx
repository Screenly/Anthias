import { test, expect } from 'bun:test'
import { render, screen, act } from '@testing-library/react'
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

test('ScheduleOverview renders the home page', async () => {
  await act(async () => {
    renderWithRedux(<ScheduleOverview />)
  })

  expect(screen.getByText('Schedule Overview')).toBeInTheDocument()

  expect(screen.getByText('https://react.dev/')).toBeInTheDocument()
  expect(screen.getByText('https://angular.dev/')).toBeInTheDocument()
  expect(screen.getByText('https://vuejs.org/')).toBeInTheDocument()
})
