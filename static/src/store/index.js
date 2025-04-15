import { configureStore } from '@reduxjs/toolkit'
import { assetsReducer, assetModalReducer } from '@/store/assets'

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
    assetModal: assetModalReducer,
  },
})
