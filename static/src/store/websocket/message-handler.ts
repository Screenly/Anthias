import { UnknownAction, ThunkDispatch } from '@reduxjs/toolkit';
import { fetchAssets } from '@/store/assets';
import { RootState, WebSocketMessage } from '@/types';

export const handleWebSocketMessage = (
  message: WebSocketMessage | string,
  dispatch: ThunkDispatch<RootState, unknown, UnknownAction>,
) => {
  // Only refresh assets if the message is a string resembling an asset ID (32-char hex)
  if (typeof message === 'string' && /^[a-fA-F0-9]{32}$/.test(message)) {
    dispatch(fetchAssets());
  }
};
