import React, { useEffect, useState } from 'react'
import classNames from 'classnames'
import { useDispatch } from 'react-redux'
import { fetchAssets, updateAssetOrder } from '@/store/assets'

/**
 * Edit Asset Modal component
 * @param {Object} props - Component props
 * @param {boolean} props.isOpen - Whether the modal is open
 * @param {Function} props.onClose - Callback function to call after closing
 * @param {Object} props.asset - The asset to edit
 * @returns {JSX.Element|null} - Edit Asset Modal component
 */
export const EditAssetModal = ({ isOpen, onClose, asset }) => {
  const dispatch = useDispatch()
  const [isVisible, setIsVisible] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formData, setFormData] = useState({
    name: '',
    start_date: '',
    end_date: '',
    duration: '',
    mimetype: 'webpage',
    nocache: false,
    skip_asset_check: false,
  })
  const [loopTimes, setLoopTimes] = useState('manual')
  const [startDateDate, setStartDateDate] = useState('')
  const [startDateTime, setStartDateTime] = useState('')
  const [endDateDate, setEndDateDate] = useState('')
  const [endDateTime, setEndDateTime] = useState('')

  // Initialize form data when asset changes
  useEffect(() => {
    if (asset) {
      // Parse dates from UTC
      const startDate = new Date(asset.start_date)
      const endDate = new Date(asset.end_date)

      // Format date and time parts in local timezone
      const formatDatePart = (date) => {
        const year = date.getFullYear()
        const month = String(date.getMonth() + 1).padStart(2, '0')
        const day = String(date.getDate()).padStart(2, '0')
        return `${year}-${month}-${day}`
      }

      const formatTimePart = (date) => {
        const hours = String(date.getHours()).padStart(2, '0')
        const minutes = String(date.getMinutes()).padStart(2, '0')
        return `${hours}:${minutes}`
      }

      setFormData({
        name: asset.name || '',
        start_date: asset.start_date || '',
        end_date: asset.end_date || '',
        duration: asset.duration || '',
        mimetype: asset.mimetype || 'webpage',
        nocache: asset.nocache || false,
        skip_asset_check: asset.skip_asset_check || false,
      })

      setStartDateDate(formatDatePart(startDate))
      setStartDateTime(formatTimePart(startDate))
      setEndDateDate(formatDatePart(endDate))
      setEndDateTime(formatTimePart(endDate))
    }
  }, [asset])

  // Handle modal visibility
  useEffect(() => {
    setLoopTimes('manual')

    if (isOpen) {
      setIsVisible(true)
    } else {
      const timer = setTimeout(() => {
        setIsVisible(false)
      }, 300) // Match the transition duration
      return () => clearTimeout(timer)
    }
  }, [isOpen])

  const handleClose = () => {
    setIsVisible(false)
    setTimeout(() => {
      onClose()
    }, 300) // Match the transition duration
  }

  const handleModalClick = (e) => {
    // Only close if clicking the modal backdrop (outside the modal content)
    if (e.target === e.currentTarget) {
      handleClose()
    }
  }

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target
    setFormData({
      ...formData,
      [name]: type === 'checkbox' ? checked : value,
    })
  }

  const handleLoopTimesChange = (e) => {
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

  const handleDateChange = (e, type) => {
    const { value } = e.target
    if (type === 'startDate') {
      setStartDateDate(value)
    } else if (type === 'startTime') {
      setStartDateTime(value)
    } else if (type === 'endDate') {
      setEndDateDate(value)
    } else if (type === 'endTime') {
      setEndDateTime(value)
    }
  }

  const handleSubmit = async (e) => {
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
    } finally {
      setIsSubmitting(false)
    }
  }

  if (!isOpen && !isVisible) return null

  return (
    <div
      className={classNames('modal', {
        show: isOpen,
        fade: true,
        'd-block': isOpen,
        'modal-visible': isVisible,
      })}
      aria-hidden="true"
      role="dialog"
      tabIndex="-1"
      onClick={handleModalClick}
      style={{
        display: isOpen ? 'block' : 'none',
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        transition: 'opacity 0.3s ease-in-out',
        opacity: isVisible ? 1 : 0,
      }}
    >
      <div
        className="modal-dialog"
        role="document"
        style={{
          transition: 'transform 0.3s ease-in-out',
          transform: isVisible ? 'translate(0, 0)' : 'translate(0, -25%)',
        }}
      >
        <div className="modal-content">
          <div className="form-horizontal">
            <div className="modal-header">
              <h3 id="modalLabel">Edit Asset</h3>
              <button type="button" className="close" onClick={handleClose}>
                <span aria-hidden="true">&times;</span>
              </button>
            </div>
            <div className="modal-body">
              <div className="asset-location edit">
                <form id="edit-form" onSubmit={handleSubmit}>
                  <div className="form-group row name">
                    <label className="col-4 col-form-label">Name</label>
                    <div className="col-7">
                      <input
                        className="form-control shadow-none"
                        name="name"
                        placeholder="Nickname for this asset"
                        type="text"
                        value={formData.name}
                        onChange={handleInputChange}
                      />
                    </div>
                  </div>
                  <div className="form-group row">
                    <label className="col-4 col-form-label">
                      Asset Location
                    </label>
                    <div className="col-8 controls">
                      <div
                        className="uri-text first text-break"
                        style={{ wordBreak: 'break-all' }}
                      >
                        {asset?.uri || ''}
                      </div>
                    </div>
                  </div>
                  <div className="form-group row mimetype">
                    <label className="col-4 col-form-label">Asset Type</label>
                    <div className="col-4 controls">
                      <select
                        className="mime-select form-control shadow-none"
                        name="mimetype"
                        value={formData.mimetype}
                        onChange={handleInputChange}
                        disabled={true}
                      >
                        <option value="webpage">Webpage</option>
                        <option value="image">Image</option>
                        <option value="video">Video</option>
                        <option value="streaming">Streaming</option>
                        <option value="youtube_asset">YouTubeAsset</option>
                      </select>
                    </div>
                  </div>
                  <hr />
                  <div className="row form-group loop_date">
                    <label className="col-4 col-form-label">Play for</label>
                    <div className="controls col-7">
                      <select
                        className="form-control shadow-none"
                        id="loop_times"
                        value={loopTimes}
                        onChange={handleLoopTimesChange}
                      >
                        <option value="day">1 Day</option>
                        <option value="week">1 Week</option>
                        <option value="month">1 Month</option>
                        <option value="year">1 Year</option>
                        <option value="forever">Forever</option>
                        <option value="manual">Manual</option>
                      </select>
                    </div>
                  </div>
                  <div id="manul_date">
                    <div className="form-group row start_date">
                      <label className="col-4 col-form-label">Start Date</label>
                      <div className="controls col-7">
                        <input
                          className="form-control date shadow-none"
                          name="start_date_date"
                          type="date"
                          value={startDateDate}
                          onChange={(e) => handleDateChange(e, 'startDate')}
                          style={{ marginRight: '5px' }}
                        />
                        <input
                          className="form-control time shadow-none"
                          name="start_date_time"
                          type="time"
                          value={startDateTime}
                          onChange={(e) => handleDateChange(e, 'startTime')}
                        />
                      </div>
                    </div>
                    <div className="form-group row end_date">
                      <label className="col-4 col-form-label">End Date</label>
                      <div className="controls col-7">
                        <input
                          className="form-control date shadow-none"
                          name="end_date_date"
                          type="date"
                          value={endDateDate}
                          onChange={(e) => handleDateChange(e, 'endDate')}
                          style={{ marginRight: '5px' }}
                        />
                        <input
                          className="form-control time shadow-none"
                          name="end_date_time"
                          type="time"
                          value={endDateTime}
                          onChange={(e) => handleDateChange(e, 'endTime')}
                        />
                      </div>
                    </div>
                  </div>
                  <div className="form-group row duration">
                    <label className="col-4 col-form-label">Duration</label>
                    <div className="col-7 controls">
                      <input
                        className="form-control shadow-none"
                        name="duration"
                        type="number"
                        value={formData.duration}
                        onChange={handleInputChange}
                        disabled={formData.mimetype === 'video'}
                      />
                      seconds &nbsp;
                    </div>
                  </div>
                  <div className="advanced-accordion accordion">
                    <div className="accordion-group">
                      <div className="accordion-heading">
                        <i className="fas fa-play unrotated"></i>
                        <a className="advanced-toggle" href="#">
                          Advanced
                        </a>
                      </div>
                      <div className="collapse-advanced accordion-body collapse">
                        <div className="accordion-inner">
                          <div className="form-group row">
                            <label className="col-4 col-form-label">
                              Disable cache
                            </label>
                            <div className="col-8 nocache controls justify-content-center align-self-center">
                              <label className="nocache-toggle toggle switch-light switch-ios small m-0">
                                <input
                                  type="checkbox"
                                  name="nocache"
                                  checked={formData.nocache}
                                  onChange={handleInputChange}
                                />
                                <span>
                                  <span></span>
                                  <span></span>
                                  <a></a>
                                </span>
                              </label>
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </form>
              </div>
            </div>
            <div className="modal-footer">
              <div
                className="float-left progress active"
                style={{ display: 'none' }}
              >
                <div className="bar progress-bar-striped progress-bar progress-bar-animated"></div>
              </div>
              <button
                className="btn btn-outline-primary btn-long cancel"
                type="button"
                onClick={handleClose}
                disabled={isSubmitting}
              >
                Cancel
              </button>
              <button
                id="save-asset"
                className="btn btn-primary btn-long"
                type="submit"
                onClick={handleSubmit}
                disabled={isSubmitting}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
