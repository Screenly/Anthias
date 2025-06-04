import { useDispatch, useSelector } from 'react-redux'
import {
  updateFormData,
  validateUrl,
  setValid,
  setErrorMessage,
  resetForm,
  saveAsset,
  selectAssetModalState,
} from '@/store/assets'
import {
  getMimetype,
  getDurationForMimetype,
  getDefaultDates,
} from '@/components/asset-modal/file-upload-utils'

/**
 * Custom hook for asset form handling
 * @param {Function} onSave - Callback function to call after successful save
 * @param {Function} onClose - Callback function to call after closing
 * @returns {Object} - Form handlers and state
 */
export const useAssetForm = (onSave, onClose) => {
  const dispatch = useDispatch()
  const {
    activeTab,
    formData,
    isValid,
    errorMessage,
    statusMessage,
    isSubmitting,
    defaultDuration,
    defaultStreamingDuration,
  } = useSelector(selectAssetModalState)

  /**
   * Handle input change
   * @param {Event} e - The input change event
   */
  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    dispatch(
      updateFormData({
        [name]: type === 'checkbox' ? checked : value,
      }),
    )

    // Validate URL when it changes
    if (name === 'uri' && activeTab === 'uri') {
      dispatch(validateUrl(value))
    }
  }

  /**
   * Handle form submission
   * @param {Event} e - The form submission event
   */
  const handleSubmit = async (e) => {
    e.preventDefault()

    if (activeTab === 'uri') {
      if (!formData.uri) {
        dispatch(setErrorMessage('Please enter a URL'))
        dispatch(setValid(false))
        return
      }

      if (!isValid) {
        return
      }

      // Determine mimetype based on URL
      const mimetype = getMimetype(formData.uri)

      // Get duration based on mimetype
      const duration = getDurationForMimetype(
        mimetype,
        defaultDuration,
        defaultStreamingDuration,
      )

      // Get default dates
      const dates = getDefaultDates()

      // Create asset data
      const assetData = {
        ...formData,
        mimetype,
        name: formData.uri, // Use URI as name by default
        is_active: 1,
        is_enabled: 0,
        is_processing: 0,
        nocache: 0,
        play_order: 0,
        skip_asset_check: formData.skipAssetCheck ? 1 : 0,
        duration,
        ...dates,
      }

      try {
        // Save the asset
        const savedAsset = await dispatch(saveAsset({ assetData })).unwrap()

        // Call the onSave callback with the asset data
        onSave(savedAsset)

        // Reset form
        dispatch(resetForm())

        // Close the modal
        onClose()
      } catch (error) {
        dispatch(setErrorMessage('Failed to save asset. Please try again.'))
      }
    }
  }

  return {
    activeTab,
    formData,
    isValid,
    errorMessage,
    statusMessage,
    isSubmitting,
    handleInputChange,
    handleSubmit,
  }
}
