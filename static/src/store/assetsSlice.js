import { createSlice, createAsyncThunk } from '@reduxjs/toolkit';

export const fetchAssets = createAsyncThunk(
  'assets/fetchAssets',
  async () => {
    const response = await fetch('/api/v2/assets');
    const data = await response.json();
    return data;
  }
);

export const updateAssetOrder = createAsyncThunk(
  'assets/updateOrder',
  async (orderedIds) => {
    const response = await fetch('/api/v2/assets/order', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ids: orderedIds })
    });
    if (!response.ok) {
      throw new Error('Failed to update order');
    }
    return orderedIds;
  }
);

export const toggleAssetEnabled = createAsyncThunk(
  'assets/toggleEnabled',
  async ({ assetId, newValue }) => {
    const response = await fetch(`/api/v2/assets/${assetId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        is_enabled: newValue,
        play_order: newValue === 0 ? 0 : undefined
      })
    });
    if (!response.ok) {
      throw new Error('Failed to update asset');
    }
    return { assetId, newValue };
  }
);

const assetsSlice = createSlice({
  name: 'assets',
  initialState: {
    items: [],
    status: 'idle',
    error: null,
  },
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchAssets.pending, (state) => {
        state.status = 'loading';
      })
      .addCase(fetchAssets.fulfilled, (state, action) => {
        state.status = 'succeeded';
        state.items = action.payload;
      })
      .addCase(fetchAssets.rejected, (state, action) => {
        state.status = 'failed';
        state.error = action.error.message;
      })
      .addCase(updateAssetOrder.fulfilled, (state) => {
        state.status = 'succeeded';
      })
      .addCase(toggleAssetEnabled.fulfilled, (state, action) => {
        const { assetId, newValue } = action.payload;
        const asset = state.items.find(item => item.asset_id === assetId);
        if (asset) {
          asset.is_enabled = newValue;
        }
      });
  },
});

export const selectActiveAssets = (state) =>
  state.assets.items
    .filter(asset => asset.is_active)
    .sort((a, b) => a.play_order - b.play_order);

export const selectInactiveAssets = (state) =>
  state.assets.items.filter(asset => !asset.is_active);

export default assetsSlice.reducer;