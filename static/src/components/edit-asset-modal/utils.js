import { fetchAssets, updateAssetOrder } from '@/store/assets'

/**
 * Handle form submission for asset editing
 * @param {Object} params - Parameters for form submission
 * @param {Event} params.e - Form submission event
 * @param {Object} params.asset - The asset to update
 * @param {Object} params.formData - Form data
 * @param {string} params.startDateDate - Start date part
 * @param {string} params.startDateTime - Start time part
 * @param {string} params.endDateDate - End date part
 * @param {string} params.endDateTime - End time part
 * @param {Function} params.dispatch - Redux dispatch function
 * @param {Function} params.onClose - Callback to close modal
 * @param {Function} params.setIsSubmitting - Function to set submitting state
 * @returns {Promise<void>}
 */
export const handleSubmit = async ({
  e,
  asset,
  formData,
  startDateDate,
  startDateTime,
  endDateDate,
  endDateTime,
  dispatch,
  onClose,
  setIsSubmitting,
}) => {
  e.preventDefault()
  setIsSubmitting(true)

  try {
    // Combine date and time parts
    const startDate = new Date(`${startDateDate}T${startDateTime}`)
    const endDate = new Date(`${endDateDate}T${endDateTime}`)

    // Prepare data for API
    const updatedAsset = {
      ...formData,
      start_date: startDate.toISOString(),
      end_date: endDate.toISOString(),
      asset_id: asset.id,
      is_enabled: asset.is_enabled,
      play_order: asset.play_order,
    }

    // Make API call to update asset
    const response = await fetch(`/api/v2/assets/${asset.id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(updatedAsset),
    })

    if (!response.ok) {
      throw new Error('Failed to update asset')
    }

    // Get active assets from Redux store and update order
    const activeAssetIds = dispatch((_, getState) => {
      const state = getState()
      return state.assets.items
        .filter((asset) => asset.is_active)
        .sort((a, b) => a.play_order - b.play_order)
        .map((asset) => asset.asset_id)
        .join(',')
    })

    // Update the asset order so that active assets won't be reordered
    // unexpectedly when the asset is updated and saved.
    await dispatch(updateAssetOrder(activeAssetIds))

    // Refresh assets list
    dispatch(fetchAssets())

    // Close modal
    onClose()
  } catch (error) {
    // Error handling can be added here if needed
  } finally {
    setIsSubmitting(false)
  }
}

/**
 * Handle loop times change for asset editing
 * @param {Object} params - Parameters for loop times change
 * @param {Event} params.e - Change event
 * @param {string} params.startDateDate - Start date part
 * @param {string} params.startDateTime - Start time part
 * @param {Function} params.setLoopTimes - Function to set loop times state
 * @param {Function} params.setEndDateDate - Function to set end date state
 * @param {Function} params.setEndDateTime - Function to set end time state
 * @param {Function} params.setFormData - Function to set form data state
 * @returns {void}
 */
export const handleLoopTimesChange = ({
  e,
  startDateDate,
  startDateTime,
  setLoopTimes,
  setEndDateDate,
  setEndDateTime,
  setFormData,
}) => {
  const playFor = e.target.value
  setLoopTimes(playFor)

  if (playFor === 'manual') {
    return
  }

  // Get current start date and time in UTC
  const startDate = new Date(`${startDateDate}T${startDateTime}Z`)
  let endDate = new Date(startDate)

  // Add time based on selection
  switch (playFor) {
    case 'day':
      endDate.setUTCDate(endDate.getUTCDate() + 1)
      break
    case 'week':
      endDate.setUTCDate(endDate.getUTCDate() + 7)
      break
    case 'month':
      endDate.setUTCMonth(endDate.getUTCMonth() + 1)
      break
    case 'year':
      endDate.setUTCFullYear(endDate.getUTCFullYear() + 1)
      break
    case 'forever':
      endDate.setUTCFullYear(9999)
      break
  }

  // Format the new end date in ISO format with timezone
  const formatDatePart = (date) => {
    const year = date.getUTCFullYear()
    const month = String(date.getUTCMonth() + 1).padStart(2, '0')
    const day = String(date.getUTCDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
  }

  const formatTimePart = (date) => {
    const hours = String(date.getUTCHours()).padStart(2, '0')
    const minutes = String(date.getUTCMinutes()).padStart(2, '0')
    return `${hours}:${minutes}`
  }

  // Update end date and time
  setEndDateDate(formatDatePart(endDate))
  setEndDateTime(formatTimePart(endDate))

  // Update formData with the ISO string
  setFormData((prev) => ({
    ...prev,
    end_date: endDate.toISOString(),
  }))
}
