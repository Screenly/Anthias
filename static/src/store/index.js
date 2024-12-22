import { configureStore } from '@reduxjs/toolkit';
import assetsReducer from './assetsSlice';

export const store = configureStore({
  reducer: {
    assets: assetsReducer,
  },
});