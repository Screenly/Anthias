import { configureStore } from '@reduxjs/toolkit'
import { assetsReducer, assetModalReducer } from '@/store/assets'
import settingsReducer from '@/store/settings'
import websocketReducer from '@/store/websocket'

const environment = process.env.ENVIRONMENT || 'production'

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
    assetModal: assetModalReducer,
    settings: settingsReducer,
    websocket: websocketReducer,
  },
  devTools: environment === 'development',
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
