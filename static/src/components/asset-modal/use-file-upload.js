import { useRef } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import {
  uploadFile,
  saveAsset,
  setErrorMessage,
  setStatusMessage,
  setUploadProgress,
  resetForm,
  selectAssetModalState,
} from '@/store/assets'

/**
 * Custom hook for file upload functionality
 * @returns {Object} - File upload handlers and refs
 */
export const useFileUpload = () => {
  const dispatch = useDispatch()
  const { formData } = useSelector(selectAssetModalState)
  const fileInputRef = useRef(null)
  const dropZoneRef = useRef(null)

  /**
   * Handle file selection from input
   * @param {Event} e - The file input change event
   */
  const handleFileSelect = (e) => {
    const file = e.target.files[0]
    if (file) {
      handleFileUpload(file)
    }
  }

  /**
   * Handle file drop
   * @param {Event} e - The drop event
   */
  const handleFileDrop = (e) => {
    e.preventDefault()
    e.stopPropagation()

    const file = e.dataTransfer.files[0]
    if (file) {
      handleFileUpload(file)
    }
  }

  /**
   * Handle drag over event
   * @param {Event} e - The drag over event
   */
  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  /**
   * Handle drag enter event
   * @param {Event} e - The drag enter event
   */
  const handleDragEnter = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  /**
   * Handle drag leave event
   * @param {Event} e - The drag leave event
   */
  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
  }

  /**
   * Main file upload function
   * @param {File} file - The file to upload
   */
  const handleFileUpload = async (file) => {
    try {
      // Upload the file
      const result = await dispatch(
        uploadFile({ file, skipAssetCheck: formData.skipAssetCheck }),
      ).unwrap()

      // Create asset data
      const assetData = {
        uri: result.fileData.uri,
        ext: result.fileData.ext,
        name: file.name,
        mimetype: result.mimetype,
        is_active: 1,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        duration: result.duration,
        skip_asset_check: formData.skipAssetCheck ? 1 : 0,
        ...result.dates,
      }

      // Save the asset
      await dispatch(saveAsset({ assetData })).unwrap()

      // Reset form and show success message
      dispatch(resetForm())
      dispatch(setStatusMessage('Upload completed.'))

      // Reset file input
      if (fileInputRef.current) {
        fileInputRef.current.value = ''
      }

      // Hide status message after 5 seconds
      setTimeout(() => {
        dispatch(setStatusMessage(''))
      }, 5000)
    } catch (error) {
      dispatch(setErrorMessage(`Upload failed: ${error.message}`))
      dispatch(setUploadProgress(0))

      // Reset the progress bar width directly
      const progressBar = document.querySelector('.progress .bar')
      if (progressBar) {
        progressBar.style.width = '0%'
      }
    }
  }

  return {
    fileInputRef,
    dropZoneRef,
    handleFileSelect,
    handleFileDrop,
    handleDragOver,
    handleDragEnter,
    handleDragLeave,
    handleFileUpload,
  }
}
