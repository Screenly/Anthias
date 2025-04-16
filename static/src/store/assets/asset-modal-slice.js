import { createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { addAsset } from './assets-list-slice'

// Async thunks for API operations
export const uploadFile = createAsyncThunk(
  'assetModal/uploadFile',
  async ({ file, skipAssetCheck }, { dispatch, rejectWithValue }) => {
    try {
      const formData = new FormData()
      formData.append('file_upload', file)

      // Create XMLHttpRequest for progress tracking
      const xhr = new XMLHttpRequest()

      const uploadPromise = new Promise((resolve, reject) => {
        xhr.upload.addEventListener('progress', (e) => {
          if (e.lengthComputable) {
            const progress = Math.round((e.loaded / e.total) * 100)
            dispatch(setUploadProgress(progress))
          }
        })

        xhr.addEventListener('load', () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const response = JSON.parse(xhr.responseText)
              resolve(response)
            } catch (error) {
              reject(new Error('Invalid JSON response'))
            }
          } else {
            reject(new Error(`Upload failed with status ${xhr.status}`))
          }
        })

        xhr.addEventListener('error', () => {
          reject(new Error('Network error during upload'))
        })

        xhr.addEventListener('abort', () => {
          reject(new Error('Upload aborted'))
        })
      })

      // Start the upload
      xhr.open('POST', '/api/v2/file_asset')
      xhr.send(formData)

      // Wait for upload to complete
      const response = await uploadPromise

      // Get mimetype and duration
      const mimetype = getMimetype(file.name)
      const duration = getDurationForMimetype(mimetype, 60, 3600)
      const dates = getDefaultDates()

      return {
        fileData: response,
        filename: file.name,
        skipAssetCheck,
        mimetype,
        duration,
        dates,
      }
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

export const saveAsset = createAsyncThunk(
  'assetModal/saveAsset',
  async ({ assetData }, { dispatch, rejectWithValue }) => {
    try {
      const response = await fetch('/api/v2/assets', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(assetData),
      })

      if (!response.ok) {
        return rejectWithValue('Failed to save asset')
      }

      const data = await response.json()

      // Create the complete asset object with the response data
      const completeAsset = {
        ...assetData,
        asset_id: data.asset_id,
        ...data,
      }

      // Dispatch the addAsset action to update the assets list
      dispatch(addAsset(completeAsset))

      return completeAsset
    } catch (error) {
      return rejectWithValue(error.message)
    }
  },
)

// Helper functions
const getMimetype = (filename) => {
  // Implementation based on anthias.coffee
  const viduris = ['rtsp', 'rtmp']
  const mimetypes = [
    [['jpe', 'jpg', 'jpeg', 'png', 'pnm', 'gif', 'bmp'], 'image'],
    [['avi', 'mkv', 'mov', 'mpg', 'mpeg', 'mp4', 'ts', 'flv'], 'video'],
  ]
  const domains = [[['www.youtube.com', 'youtu.be'], 'youtube_asset']]

  // Check if it's a streaming URL
  const scheme = filename.split(':')[0].toLowerCase()
  if (viduris.includes(scheme)) {
    return 'streaming'
  }

  // Check if it's a domain-specific asset
  try {
    const domain = filename.split('//')[1].toLowerCase().split('/')[0]
    for (const [domainList, type] of domains) {
      if (domainList.includes(domain)) {
        return type
      }
    }
  } catch (e) {
    // Invalid URL format
  }

  // Check file extension
  try {
    const ext = filename.split('.').pop().toLowerCase()
    for (const [extList, type] of mimetypes) {
      if (extList.includes(ext)) {
        return type
      }
    }
  } catch (e) {
    // No extension found
  }

  // Default to webpage
  return 'webpage'
}

const getDurationForMimetype = (
  mimetype,
  defaultDuration,
  defaultStreamingDuration,
) => {
  // Implementation based on anthias.coffee change_mimetype method
  if (mimetype === 'video') {
    return 0
  } else if (mimetype === 'streaming') {
    return defaultStreamingDuration
  } else {
    return defaultDuration
  }
}

const getDefaultDates = () => {
  // Implementation based on anthias.coffee initialize method
  const now = new Date()
  const endDate = new Date()
  endDate.setDate(endDate.getDate() + 30) // 30 days from now

  return {
    start_date: now.toISOString(),
    end_date: endDate.toISOString(),
  }
}

// Slice definition
const assetModalSlice = createSlice({
  name: 'assetModal',
  initialState: {
    activeTab: 'uri',
    formData: {
      uri: '',
      skipAssetCheck: false,
    },
    isValid: true,
    errorMessage: '',
    statusMessage: '',
    isSubmitting: false,
    uploadProgress: 0,
    defaultDuration: 60, // 60 seconds for webpage
    defaultStreamingDuration: 3600, // 1 hour for streaming
  },
  reducers: {
    setActiveTab: (state, action) => {
      state.activeTab = action.payload
    },
    updateFormData: (state, action) => {
      state.formData = { ...state.formData, ...action.payload }
    },
    setValid: (state, action) => {
      state.isValid = action.payload
    },
    setErrorMessage: (state, action) => {
      state.errorMessage = action.payload
    },
    setStatusMessage: (state, action) => {
      state.statusMessage = action.payload
    },
    setUploadProgress: (state, action) => {
      state.uploadProgress = action.payload
    },
    resetForm: (state) => {
      state.formData = {
        uri: '',
        skipAssetCheck: false,
      }
      state.isValid = true
      state.errorMessage = ''
      state.statusMessage = ''
      state.isSubmitting = false
      state.uploadProgress = 0
    },
    validateUrl: (state, action) => {
      const url = action.payload
      if (!url) {
        state.isValid = true
        state.errorMessage = ''
        return
      }

      // URL validation pattern from anthias.coffee
      const urlPattern =
        /(http|https|rtsp|rtmp):\/\/[\w-]+(\.?[\w-]+)+([\w.,@?^=%&amp;:\/~+#-]*[\w@?^=%&amp;\/~+#-])?/
      const isValidUrl = urlPattern.test(url)

      state.isValid = isValidUrl
      state.errorMessage = isValidUrl ? '' : 'Please enter a valid URL'
    },
  },
  extraReducers: (builder) => {
    builder
      // Upload file
      .addCase(uploadFile.pending, (state) => {
        state.isSubmitting = true
        state.statusMessage = ''
        state.uploadProgress = 0
      })
      .addCase(uploadFile.fulfilled, (state, action) => {
        const {
          fileData,
          filename,
          skipAssetCheck,
          mimetype,
          duration,
          dates,
        } = action.payload

        // Update form data with file name and other details
        state.formData = {
          ...state.formData,
          name: filename,
          uri: fileData.uri,
          skipAssetCheck,
          mimetype,
          duration,
          dates,
        }

        state.statusMessage = 'Upload completed.'
        state.isSubmitting = false
        state.uploadProgress = 0
      })
      .addCase(uploadFile.rejected, (state, action) => {
        state.errorMessage = `Upload failed: ${action.payload}`
        state.isSubmitting = false
        state.uploadProgress = 0
      })
      // Save asset
      .addCase(saveAsset.pending, (state) => {
        state.isSubmitting = true
      })
      .addCase(saveAsset.fulfilled, (state) => {
        state.isSubmitting = false
        state.statusMessage = 'Asset saved successfully.'
      })
      .addCase(saveAsset.rejected, (state, action) => {
        state.errorMessage = `Failed to save asset: ${action.payload}`
        state.isSubmitting = false
      })
  },
})

// Export actions
export const {
  setActiveTab,
  updateFormData,
  setValid,
  setErrorMessage,
  setStatusMessage,
  setUploadProgress,
  resetForm,
  validateUrl,
} = assetModalSlice.actions

// Export selectors
export const selectAssetModalState = (state) => state.assetModal

// Export reducer
export default assetModalSlice.reducer
