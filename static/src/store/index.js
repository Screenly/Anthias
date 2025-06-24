import { configureStore } from '@reduxjs/toolkit'
import { assetsReducer, assetModalReducer } from '@/store/assets'
import settingsReducer from '@/store/settings'

const environment = process.env.ENVIRONMENT || 'production'

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
    assetModal: assetModalReducer,
    settings: settingsReducer,
  },
  devTools: environment === 'development',
})
