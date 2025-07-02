// Centralized type definitions for the Anthias application
import { store } from '@/store/index';

// Asset-related types
export interface Asset {
  asset_id: string;
  name: string;
  start_date: string;
  end_date: string;
  duration: number;
  uri: string;
  mimetype: string;
  is_enabled: number;
  nocache: boolean;
  skip_asset_check: boolean;
  is_active: boolean;
  play_order: number;
  is_processing: boolean;
  zoom_level: number;
}

export interface AssetEditData {
  id: string;
  name: string;
  start_date: string;
  end_date: string;
  duration: number;
  uri: string;
  mimetype: string;
  is_enabled: boolean;
  nocache: boolean;
  skip_asset_check: boolean;
  play_order?: number;
  zoom_level?: number;
}

export interface EditFormData {
  name: string;
  start_date: string;
  end_date: string;
  duration: string;
  mimetype: string;
  nocache: boolean;
  skip_asset_check: boolean;
  zoom_level?: number;
}

export interface HandleSubmitParams {
  e: React.FormEvent;
  asset: AssetEditData;
  formData: EditFormData;
  startDateDate: string;
  startDateTime: string;
  endDateDate: string;
  endDateTime: string;
  dispatch: AppDispatch;
  onClose: () => void;
  setIsSubmitting: (isSubmitting: boolean) => void;
}

// Redux store types
export interface RootState {
  assets: {
    items: Asset[];
    status: 'idle' | 'loading' | 'succeeded' | 'failed';
    error: string | null;
  };
  assetModal: {
    activeTab: string;
    formData: {
      uri: string;
      skipAssetCheck: boolean;
      name?: string;
      mimetype?: string;
      duration?: number;
      dates?: {
        start_date: string;
        end_date: string;
      };
    };
    isValid: boolean;
    errorMessage: string;
    statusMessage: string;
    uploadProgress: number;
    isSubmitting: boolean;
  };
  settings: {
    settings: {
      playerName: string;
      defaultDuration: number;
      defaultStreamingDuration: number;
      audioOutput: string;
      dateFormat: string;
      authBackend: string;
      currentPassword: string;
      user: string;
      password: string;
      confirmPassword: string;
      showSplash: boolean;
      defaultAssets: boolean;
      shufflePlaylist: boolean;
      use24HourClock: boolean;
      debugLogging: boolean;
    };
    deviceModel: string;
    prevAuthBackend: string;
    hasSavedBasicAuth: boolean;
    isLoading: boolean;
    isUploading: boolean;
    uploadProgress: number;
    error: string | null;
  };
}

// Component prop types
export interface ActiveAssetsTableProps {
  onEditAsset: (asset: AssetEditData) => void;
}

export interface InactiveAssetsTableProps {
  onEditAsset: (asset: AssetEditData) => void;
}

export interface AssetRowProps {
  assetId: string;
  name: string;
  startDate: string;
  endDate: string;
  duration: number;
  uri: string;
  mimetype: string;
  isEnabled: boolean;
  nocache: boolean;
  skipAssetCheck: boolean;
  isProcessing?: number;
  style?: React.CSSProperties;
  showDragHandle?: boolean;
  dragHandleProps?: React.HTMLAttributes<HTMLElement>;
  isDragging?: boolean;
  onEditAsset?: (asset: AssetEditData) => void;
  zoomLevel?: number;
}

// Settings-related types
export interface SettingsData {
  playerName: string;
  defaultDuration: number;
  defaultStreamingDuration: number;
  audioOutput: string;
  dateFormat: string;
  authBackend: string;
  currentPassword: string;
  user: string;
  password: string;
  confirmPassword: string;
  showSplash: boolean;
  defaultAssets: boolean;
  shufflePlaylist: boolean;
  use24HourClock: boolean;
  debugLogging: boolean;
}

export interface SystemOperationParams {
  operation: string;
  endpoint: string;
  successMessage: string;
}

export interface OperationConfig {
  operation?: string;
  endpoint: string;
  successMessage: string;
  confirmMessage?: string;
  title?: string;
  text?: string;
  confirmButtonText?: string;
  errorMessage?: string;
}

// Asset modal types
export interface UploadFileParams {
  file: File;
  skipAssetCheck: boolean;
}

export interface SaveAssetParams {
  assetData: {
    duration: number;
    end_date: string;
    is_active: number;
    is_enabled: number;
    is_processing: number;
    mimetype: string;
    name: string;
    nocache: number;
    play_order: number;
    skip_asset_check: number;
    start_date: string;
    uri: string;
  };
}

export interface FileData {
  uri: string;
  ext: string;
}

export interface FormData {
  uri: string;
  skipAssetCheck: boolean;
  name?: string;
  mimetype?: string;
  duration?: number;
  dates?: {
    start_date: string;
    end_date: string;
  };
}

// Redux Toolkit types
export type AppDispatch = typeof store.dispatch;
export type AsyncThunkAction = ReturnType<typeof store.dispatch>;

// Asset thunk types
export interface ToggleAssetParams {
  assetId: string;
  newValue: number;
}

// System info types
export interface AnthiasVersionValueProps {
  version: string;
}

export interface SkeletonProps {
  children: React.ReactNode;
  isLoading: boolean;
}

export interface MemoryInfo {
  total: number;
  used: number;
  free: number;
  shared: number;
  buff: number;
  available: number;
  percentage?: number;
}

export interface UptimeInfo {
  days: number;
  hours: number;
  minutes?: number;
  seconds?: number;
}
