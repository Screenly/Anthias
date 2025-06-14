import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'

// Async thunks
export const fetchSettings = createAsyncThunk(
  'settings/fetchSettings',
  async (_, { rejectWithValue }) => {
    try {
      const response = await fetch('/api/v2/device_settings')
      if (response?.url?.endsWith('/login/')) {
        window.location.href = response.url
      }

      if (!response.ok) {
        return rejectWithValue('Failed to fetch device settings')
      }

      const data = await response.json()
      return {
        playerName: data.player_name || '',
        defaultDuration: data.default_duration || 0,
        defaultStreamingDuration: data.default_streaming_duration || 0,
        audioOutput: data.audio_output || 'hdmi',
        dateFormat: data.date_format || 'mm/dd/yyyy',
        authBackend: data.auth_backend || '',
        user: data.username || '',
        showSplash: data.show_splash || false,
        defaultAssets: data.default_assets || false,
        shufflePlaylist: data.shuffle_playlist || false,
        use24HourClock: data.use_24_hour_clock || false,
        debugLogging: data.debug_logging || false,
      }
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

export const fetchDeviceModel = createAsyncThunk(
  'settings/fetchDeviceModel',
  async () => {
    const response = await fetch('/api/v2/info')
    const data = await response.json()
    return data.device_model || ''
  },
)

export const updateSettings = createAsyncThunk(
  'settings/updateSettings',
  async (settings, { rejectWithValue }) => {
    try {
      const response = await fetch('/api/v2/device_settings', {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          player_name: settings.playerName,
          default_duration: settings.defaultDuration,
          default_streaming_duration: settings.defaultStreamingDuration,
          audio_output: settings.audioOutput,
          date_format: settings.dateFormat,
          auth_backend: settings.authBackend,
          current_password: settings.currentPassword,
          username: settings.user,
          password: settings.password,
          password_2: settings.confirmPassword,
          show_splash: settings.showSplash,
          default_assets: settings.defaultAssets,
          shuffle_playlist: settings.shufflePlaylist,
          use_24_hour_clock: settings.use24HourClock,
          debug_logging: settings.debugLogging,
        }),
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.error || 'Failed to save settings')
      }

      return data
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

export const createBackup = createAsyncThunk(
  'settings/createBackup',
  async (_, { rejectWithValue }) => {
    try {
      const response = await fetch('/api/v2/backup', {
        method: 'POST',
      })

      if (!response.ok) {
        throw new Error('Failed to create backup')
      }

      const data = await response.json()
      return data
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

export const uploadBackup = createAsyncThunk(
  'settings/uploadBackup',
  async (file, { rejectWithValue }) => {
    try {
      const formData = new FormData()
      formData.append('backup_upload', file)

      const response = await fetch('/api/v2/recover', {
        method: 'POST',
        body: formData,
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.error || 'Failed to upload backup')
      }

      return data
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

export const systemOperation = createAsyncThunk(
  'settings/systemOperation',
  async ({ operation, endpoint, successMessage }, { rejectWithValue }) => {
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
      })

      if (!response.ok) {
        throw new Error(`Failed to ${operation} device`)
      }

      return { operation, successMessage }
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

const initialState = {
  settings: {
    playerName: '',
    defaultDuration: 0,
    defaultStreamingDuration: 0,
    audioOutput: 'hdmi',
    dateFormat: 'mm/dd/yyyy',
    authBackend: '',
    currentPassword: '',
    user: '',
    password: '',
    confirmPassword: '',
    showSplash: false,
    defaultAssets: false,
    shufflePlaylist: false,
    use24HourClock: false,
    debugLogging: false,
  },
  deviceModel: '',
  prevAuthBackend: '',
  isLoading: false,
  isUploading: false,
  uploadProgress: 0,
  error: null,
}

const settingsSlice = createSlice({
  name: 'settings',
  initialState,
  reducers: {
    updateSetting: (state, action) => {
      const { name, value } = action.payload
      if (name === 'authBackend') {
        state.prevAuthBackend = state.settings.authBackend
      }
      state.settings[name] = value
    },
    setUploadProgress: (state, action) => {
      state.uploadProgress = action.payload
    },
    resetUploadState: (state) => {
      state.isUploading = false
      state.uploadProgress = 0
      state.error = null
    },
    clearError: (state) => {
      state.error = null
    },
  },
  extraReducers: (builder) => {
    builder
      // Fetch Settings
      .addCase(fetchSettings.pending, (state) => {
        state.isLoading = true
        state.error = null
      })
      .addCase(fetchSettings.fulfilled, (state, action) => {
        state.settings = { ...state.settings, ...action.payload }
        state.isLoading = false
      })
      .addCase(fetchSettings.rejected, (state, action) => {
        state.isLoading = false
        state.error = action.payload
      })
      // Fetch Device Model
      .addCase(fetchDeviceModel.fulfilled, (state, action) => {
        state.deviceModel = action.payload
      })
      // Update Settings
      .addCase(updateSettings.pending, (state) => {
        state.isLoading = true
        state.error = null
      })
      .addCase(updateSettings.fulfilled, (state) => {
        state.isLoading = false
        state.settings.currentPassword = ''
      })
      .addCase(updateSettings.rejected, (state, action) => {
        state.isLoading = false
        state.error = action.payload
      })
      // Create Backup
      .addCase(createBackup.rejected, (state, action) => {
        state.error = action.payload
      })
      // Upload Backup
      .addCase(uploadBackup.pending, (state) => {
        state.isUploading = true
        state.error = null
      })
      .addCase(uploadBackup.fulfilled, (state) => {
        state.isUploading = false
      })
      .addCase(uploadBackup.rejected, (state, action) => {
        state.isUploading = false
        state.error = action.payload
      })
      // System Operation
      .addCase(systemOperation.rejected, (state, action) => {
        state.error = action.payload
      })
  },
})

export const {
  updateSetting,
  setUploadProgress,
  resetUploadState,
  clearError,
} = settingsSlice.actions

// Selectors
export const selectSettings = (state) => state.settings.settings

export default settingsSlice.reducer
