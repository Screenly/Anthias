import { configureStore } from '@reduxjs/toolkit'
import { assetsReducer, assetModalReducer } from '@/store/assets'

const environment = process.env.ENVIRONMENT || 'production'

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
    assetModal: assetModalReducer,
  },
  devTools: environment === 'development',
})
