import { configureStore } from '@reduxjs/toolkit'
import assetsReducer from '@/store/assets-slice'

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
  },
})
