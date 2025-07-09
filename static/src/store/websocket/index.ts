import {
  AnyAction,
  createSlice,
  createAsyncThunk,
  ThunkDispatch,
} from '@reduxjs/toolkit';
import { handleWebSocketMessage } from './message-handler';
import { RootState } from '@/types';

export interface WebSocketMessage {
  type?: string;
  data?: unknown;
  asset_id?: string;
}

export interface WebSocketState {
  isConnected: boolean;
  isConnecting: boolean;
  error: string | null;
  lastMessage: WebSocketMessage | string | null;
  reconnectAttempts: number;
}

interface ExtendedWindow extends Window {
  anthiasWebSocket?: WebSocket;
}

const initialState: WebSocketState = {
  isConnected: false,
  isConnecting: false,
  error: null,
  lastMessage: null,
  reconnectAttempts: 0,
};

const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 3000;

export const connectWebSocket = createAsyncThunk(
  'websocket/connect',
  async (_, { dispatch, getState }) => {
    return new Promise<void>((resolve, reject) => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;

      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        dispatch(setConnected(true));
        dispatch(setConnecting(false));
        dispatch(setReconnectAttempts(0));
        resolve();
      };

      ws.onmessage = async (event) => {
        let messageData: string;

        // Handle different message types (Blob, ArrayBuffer, or string)
        if (event.data instanceof Blob) {
          messageData = await event.data.text();
        } else if (event.data instanceof ArrayBuffer) {
          messageData = new TextDecoder().decode(event.data);
        } else {
          messageData = event.data;
        }

        try {
          const message = JSON.parse(messageData) as WebSocketMessage;
          dispatch(setLastMessage(message));
          handleWebSocketMessage(
            message,
            dispatch as ThunkDispatch<RootState, unknown, AnyAction>,
          );
        } catch {
          // If it's not JSON, treat it as a string message
          dispatch(setLastMessage(messageData));
          handleWebSocketMessage(
            messageData,
            dispatch as ThunkDispatch<RootState, unknown, AnyAction>,
          );
        }
      };

      ws.onerror = () => {
        dispatch(setError('WebSocket connection error'));
        dispatch(setConnecting(false));
        reject(new Error('WebSocket connection failed'));
      };

      ws.onclose = (event) => {
        dispatch(setConnected(false));
        dispatch(setConnecting(false));

        // Attempt to reconnect if not a normal closure
        if (event.code !== 1000) {
          const state = getState() as RootState;
          const currentAttempts = state.websocket.reconnectAttempts;

          if (currentAttempts < MAX_RECONNECT_ATTEMPTS) {
            setTimeout(() => {
              dispatch(setReconnectAttempts(currentAttempts + 1));
              dispatch(connectWebSocket());
            }, RECONNECT_DELAY);
          }
        }
      };

      // Store the WebSocket instance for later use
      (window as ExtendedWindow).anthiasWebSocket = ws;
    });
  },
);

export const disconnectWebSocket = createAsyncThunk(
  'websocket/disconnect',
  async () => {
    const ws = (window as ExtendedWindow).anthiasWebSocket;
    if (ws) {
      ws.close(1000); // Normal closure
      (window as ExtendedWindow).anthiasWebSocket = undefined;
    }
  },
);

const websocketSlice = createSlice({
  name: 'websocket',
  initialState,
  reducers: {
    setConnected: (state, action: { payload: boolean }) => {
      state.isConnected = action.payload;
    },
    setConnecting: (state, action: { payload: boolean }) => {
      state.isConnecting = action.payload;
    },
    setError: (state, action: { payload: string }) => {
      state.error = action.payload;
    },
    setLastMessage: (
      state,
      action: { payload: WebSocketMessage | string | null },
    ) => {
      state.lastMessage = action.payload;
    },
    setReconnectAttempts: (state, action: { payload: number }) => {
      state.reconnectAttempts = action.payload;
    },
    clearError: (state) => {
      state.error = null;
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(connectWebSocket.pending, (state) => {
        state.isConnecting = true;
        state.error = null;
      })
      .addCase(connectWebSocket.fulfilled, (state) => {
        state.isConnecting = false;
        state.error = null;
      })
      .addCase(connectWebSocket.rejected, (state, action) => {
        state.isConnecting = false;
        state.error = action.error.message || 'Failed to connect to WebSocket';
      })
      .addCase(disconnectWebSocket.fulfilled, (state) => {
        state.isConnected = false;
        state.isConnecting = false;
      });
  },
});

export const {
  setConnected,
  setConnecting,
  setError,
  setLastMessage,
  setReconnectAttempts,
  clearError,
} = websocketSlice.actions;

export default websocketSlice.reducer;
