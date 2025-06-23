import { createAsyncThunk } from '@reduxjs/toolkit';
import { ToggleAssetParams, RootState } from '@/types';

export const fetchAssets = createAsyncThunk('assets/fetchAssets', async () => {
  const response = await fetch('/api/v2/assets');
  const data = await response.json();
  return data;
});

export const updateAssetOrder = createAsyncThunk(
  'assets/updateOrder',
  async (orderedIds: string) => {
    const response = await fetch('/api/v2/assets/order', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ ids: orderedIds }),
    });
    if (!response.ok) {
      throw new Error('Failed to update order');
    }
    return orderedIds;
  },
);

export const toggleAssetEnabled = createAsyncThunk(
  'assets/toggleEnabled',
  async ({ assetId, newValue }: ToggleAssetParams, { dispatch, getState }) => {
    // First, fetch the current assets to determine the next play_order
    const response = await fetch('/api/v2/assets');
    const assets = await response.json();

    // Get the current active assets to determine the next play_order
    const activeAssets = assets.filter((asset) => asset.is_active);

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
        play_order: playOrder,
      }),
    });

    const state = getState() as RootState;
    const activeAssetIds = state.assets.items
      .filter((asset) => asset.is_active)
      .sort((a, b) => a.play_order - b.play_order)
      .map((asset) => asset.asset_id)
      .concat(assetId);

    await dispatch(updateAssetOrder(activeAssetIds.join(',')));

    if (!updateResponse.ok) {
      throw new Error('Failed to update asset');
    }

    // Return both the assetId and newValue for the reducer
    return { assetId, newValue, playOrder };
  },
);
