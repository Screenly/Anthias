import { useSelector, useDispatch } from 'react-redux';
import {
  connectWebSocket,
  disconnectWebSocket,
  WebSocketMessage,
} from '@/store/websocket';
import { RootState, AppDispatch } from '@/types';

interface ExtendedWindow extends Window {
  anthiasWebSocket?: WebSocket;
}

export const useWebSocket = () => {
  const dispatch = useDispatch<AppDispatch>();
  const websocket = useSelector((state: RootState) => state.websocket);

  const connect = () => {
    dispatch(connectWebSocket());
  };

  const disconnect = () => {
    dispatch(disconnectWebSocket());
  };

  const sendMessage = (message: WebSocketMessage | string) => {
    const ws = (window as ExtendedWindow).anthiasWebSocket;
    if (ws && ws.readyState === WebSocket.OPEN) {
      const messageString =
        typeof message === 'string' ? message : JSON.stringify(message);
      ws.send(messageString);
    }
  };

  return {
    ...websocket,
    connect,
    disconnect,
    sendMessage,
  };
};
