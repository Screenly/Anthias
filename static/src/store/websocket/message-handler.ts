import { UnknownAction, ThunkDispatch } from '@reduxjs/toolkit';
import { fetchAssets } from '@/store/assets';
import { RootState } from '@/types';
import { WebSocketMessage } from './index';

export const handleWebSocketMessage = (
  message: WebSocketMessage | string,
  dispatch: ThunkDispatch<RootState, unknown, UnknownAction>,
) => {
  let parsedMessage: WebSocketMessage;

  if (typeof message === 'string') {
    try {
      parsedMessage = JSON.parse(message) as WebSocketMessage;
    } catch {
      // If it's not JSON, treat it as an asset_id (for backward compatibility)
      parsedMessage = {
        type: 'asset_update',
        asset_id: message,
      };
    }
  } else {
    parsedMessage = message;
  }

  const messageType = parsedMessage.type || 'unknown';

  switch (messageType) {
    case 'asset_update':
    case 'asset_created':
    case 'asset_deleted':
    case 'asset_modified':
      // Refresh assets when any asset-related event occurs
      dispatch(fetchAssets());
      break;

    case 'settings_update':
      break;

    case 'system_status':
      break;

    default:
      break;
  }
};
