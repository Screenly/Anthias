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
import { selectSettings } from '@/store/settings/index'
import {
  getMimetype,
  getDurationForMimetype,
  getDefaultDates,
} from '@/components/add-asset-modal/file-upload-utils'
import { Asset, AppDispatch } from '@/types'

export const useAssetForm = (
  onSave: (asset: Asset) => void,
  onClose: () => void,
) => {
  const dispatch = useDispatch<AppDispatch>()
  const {
    activeTab,
    formData,
    isValid,
    errorMessage,
    statusMessage,
    isSubmitting,
  } = useSelector(selectAssetModalState)
  const settings = useSelector(selectSettings)

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
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

  const handleSubmit = async (e: React.FormEvent) => {
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
        settings.defaultDuration,
        settings.defaultStreamingDuration,
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
      } catch {
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
