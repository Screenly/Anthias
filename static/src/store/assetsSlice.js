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
  async ({ assetId, newValue }, { dispatch, getState }) => {
    // First, fetch the current assets to determine the next play_order
    const response = await fetch('/api/v2/assets');
    const assets = await response.json();

    // Get the current active assets to determine the next play_order
    const activeAssets = assets.filter(asset => asset.is_active);

    // If enabling the asset, set play_order to the next available position
    // If disabling the asset, set play_order to 0
    const playOrder = newValue === 1 ? activeAssets.length : 0;

    const updateResponse = await fetch(`/api/v2/assets/${assetId}`, {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        is_enabled: newValue,
        play_order: playOrder
      })
    });

    const activeAssetIds = getState().assets.items
      .filter(asset => asset.is_active)
      .sort((a, b) => a.play_order - b.play_order)
      .map(asset => asset.asset_id)
      .concat(assetId);

    await dispatch(updateAssetOrder(activeAssetIds.join(',')));

    if (!updateResponse.ok) {
      throw new Error('Failed to update asset');
    }

    // Return both the assetId and newValue for the reducer
    return { assetId, newValue, playOrder };
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
        const { assetId, newValue, playOrder } = action.payload;
        const asset = state.items.find(item => item.asset_id === assetId);
        if (asset) {
          asset.is_enabled = newValue;
          asset.play_order = playOrder;
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
