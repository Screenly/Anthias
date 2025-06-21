import React, { useEffect, useState } from 'react'
import classNames from 'classnames'
import { useDispatch } from 'react-redux'

import { handleSubmit } from '@/components/edit-asset-modal/utils'
import { NameField } from '@/components/edit-asset-modal/name-field'
import { AssetLocationField } from '@/components/edit-asset-modal/asset-location-field'
import { AssetTypeField } from '@/components/edit-asset-modal/asset-type-field'
import { PlayForField } from '@/components/edit-asset-modal/play-for-field'
import { DateFields } from '@/components/edit-asset-modal/date-fields'
import { DurationField } from '@/components/edit-asset-modal/duration-field'
import { ModalFooter } from '@/components/edit-asset-modal/modal-footer'
import { AdvancedFields } from '@/components/edit-asset-modal/advanced'

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
                <form
                  id="edit-form"
                  onSubmit={(e) =>
                    handleSubmit({
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
                    })
                  }
                >
                  <NameField
                    formData={formData}
                    handleInputChange={handleInputChange}
                  />
                  <AssetLocationField asset={asset} />
                  <AssetTypeField
                    formData={formData}
                    handleInputChange={handleInputChange}
                  />
                  <hr />
                  <PlayForField
                    loopTimes={loopTimes}
                    startDateDate={startDateDate}
                    startDateTime={startDateTime}
                    setLoopTimes={setLoopTimes}
                    setEndDateDate={setEndDateDate}
                    setEndDateTime={setEndDateTime}
                    setFormData={setFormData}
                  />
                  <DateFields
                    startDateDate={startDateDate}
                    startDateTime={startDateTime}
                    endDateDate={endDateDate}
                    endDateTime={endDateTime}
                    handleDateChange={handleDateChange}
                  />
                  <DurationField
                    formData={formData}
                    handleInputChange={handleInputChange}
                  />
                  <AdvancedFields
                    formData={formData}
                    handleInputChange={handleInputChange}
                  />
                </form>
              </div>
            </div>
            <ModalFooter
              asset={asset}
              formData={formData}
              startDateDate={startDateDate}
              startDateTime={startDateTime}
              endDateDate={endDateDate}
              endDateTime={endDateTime}
              dispatch={dispatch}
              onClose={onClose}
              handleClose={handleClose}
              isSubmitting={isSubmitting}
              handleSubmit={handleSubmit}
              setIsSubmitting={setIsSubmitting}
            />
          </div>
        </div>
      </div>
    </div>
  )
}
