# WebSocket Implementation

This directory contains the WebSocket implementation for real-time communication in Anthias.

## Overview

The WebSocket implementation provides real-time updates for:

- Asset changes (creation, updates, deletion)
- Settings updates
- System status updates

## Files

- `index.ts` - Main WebSocket store slice with connection management
- `message-handler.ts` - Handles incoming WebSocket messages and dispatches appropriate Redux actions
- `README.md` - This documentation file

## Usage

### Basic Usage

The WebSocket connection is automatically initialized when the App component mounts:

```typescript
import { useWebSocket } from "@/hooks/useWebSocket";

const MyComponent = () => {
  const { isConnected, isConnecting, error, sendMessage } = useWebSocket();

  // Use WebSocket state and methods
};
```

### WebSocket Hook

The `useWebSocket` hook provides:

- `isConnected` - Boolean indicating if WebSocket is connected
- `isConnecting` - Boolean indicating if WebSocket is connecting
- `error` - String containing any connection errors
- `lastMessage` - The last received message
- `reconnectAttempts` - Number of reconnection attempts
- `connect()` - Function to manually connect
- `disconnect()` - Function to manually disconnect
- `sendMessage(message)` - Function to send a message

### Message Handling

The WebSocket automatically handles these message types:

- `asset_update` - Refreshes the assets list
- `asset_created` - Refreshes the assets list
- `asset_deleted` - Refreshes the assets list
- `asset_modified` - Refreshes the assets list
- `settings_update` - Placeholder for settings updates
- `system_status` - Placeholder for system status updates

### Automatic Reconnection

The WebSocket automatically attempts to reconnect up to 5 times with a 3-second delay between attempts when the connection is lost unexpectedly.

### Development

In development mode, a WebSocket status indicator is displayed in the bottom-right corner showing connection status and recent messages.
