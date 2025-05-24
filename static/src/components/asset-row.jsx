import {
  FaGripVertical,
  FaDownload,
  FaPencilAlt,
  FaTrashAlt,
} from 'react-icons/fa'
import Swal from 'sweetalert2'
import classNames from 'classnames'
import { useEffect, forwardRef, useState } from 'react'
import { useDispatch } from 'react-redux'

import { toggleAssetEnabled, fetchAssets } from '@/store/assets'

const tooltipStyles = `
  .tooltip {
    opacity: 1 !important;
    transition: opacity 0s ease-in-out !important;
  }
  .tooltip.fade {
    opacity: 0;
  }
  .tooltip.show {
    opacity: 1;
  }
  .tooltip-inner {
    background-color: #2c3e50;
    color: #fff;
    padding: 0.5rem 0.75rem;
    border-radius: 0.375rem;
    font-size: 0.875rem;
    font-weight: 500;
    max-width: 300px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
  }
  .tooltip.bs-tooltip-top .arrow::before {
    border-top-color: #2c3e50;
  }
`

const formatDuration = (seconds) => {
  let durationString = ''
  const secInt = parseInt(seconds)

  const hours = Math.floor(secInt / 3600)
  if (hours > 0) {
    durationString += `${hours} hours `
  }

  const minutes = Math.floor(secInt / 60) % 60
  if (minutes > 0) {
    durationString += `${minutes} min `
  }

  const secs = secInt % 60
  if (secs > 0) {
    durationString += `${secs} sec`
  }

  return durationString
}

const formatDate = (date, dateFormat, use24HourClock = false) => {
  if (!date) return ''

  // Create a Date object from the input date string
  const dateObj = new Date(date)

  // Check if the date is valid
  if (isNaN(dateObj.getTime())) return date

  // Extract the separator from the format
  const separator = dateFormat.includes('/')
    ? '/'
    : dateFormat.includes('-')
      ? '-'
      : dateFormat.includes('.')
        ? '.'
        : '/'

  // Extract the format parts from the dateFormat string
  const formatParts = dateFormat.split(/[\/\-\.]/)

  // Set up the date formatting options
  const options = {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: !use24HourClock, // Use 12-hour format if use24HourClock is false
  }

  // Create a formatter with the specified options
  const formatter = new Intl.DateTimeFormat('en-US', options)

  // Format the date and get the parts
  const formattedParts = formatter.formatToParts(dateObj)

  // Extract the formatted values
  const month = formattedParts.find((p) => p.type === 'month').value
  const day = formattedParts.find((p) => p.type === 'day').value
  const year = formattedParts.find((p) => p.type === 'year').value
  const hour = formattedParts.find((p) => p.type === 'hour').value
  const minute = formattedParts.find((p) => p.type === 'minute').value
  const second = formattedParts.find((p) => p.type === 'second').value

  // Get the period (AM/PM) if using 12-hour format
  let period = ''
  if (!use24HourClock) {
    const periodPart = formattedParts.find((p) => p.type === 'dayPeriod')
    if (periodPart) {
      period = ` ${periodPart.value}`
    }
  }

  // Build the date part according to the format
  let datePart = ''

  // Determine the order based on the format
  if (formatParts[0].includes('mm')) {
    datePart = `${month}${separator}${day}${separator}${year}`
  } else if (formatParts[0].includes('dd')) {
    datePart = `${day}${separator}${month}${separator}${year}`
  } else if (formatParts[0].includes('yyyy')) {
    datePart = `${year}${separator}${month}${separator}${day}`
  } else {
    // Default to mm/dd/yyyy if format is not recognized
    datePart = `${month}${separator}${day}${separator}${year}`
  }

  // Add the time part with AM/PM if using 12-hour format
  const timePart = `${hour}:${minute}:${second}${period}`

  return `${datePart} ${timePart}`
}

export const AssetRow = forwardRef((props, ref) => {
  const defaultDateFormat = 'mm/dd/yyyy'
  const dispatch = useDispatch()
  const [isDisabled, setIsDisabled] = useState(false)
  const [dateFormat, setDateFormat] = useState(defaultDateFormat)
  const [use24HourClock, setUse24HourClock] = useState(false)

  useEffect(() => {
    const fetchDateFormat = async () => {
      try {
        const response = await fetch('/api/v2/device_settings')
        const data = await response.json()
        setDateFormat(data.date_format)
        setUse24HourClock(data.use_24_hour_clock)
      } catch (error) {
        setDateFormat(defaultDateFormat)
        setUse24HourClock(false)
      }
    }

    fetchDateFormat()
  }, [])

  const handleToggle = async () => {
    const newValue = !props.isEnabled ? 1 : 0
    setIsDisabled(true)
    try {
      await dispatch(
        toggleAssetEnabled({ assetId: props.assetId, newValue }),
      ).unwrap()
      dispatch(fetchAssets())
    } catch (error) {
    } finally {
      setIsDisabled(false)
    }
  }

  const handleDownload = async (event) => {
    event.preventDefault()
    const assetId = props.assetId

    try {
      const response = await fetch(`/api/v2/assets/${assetId}/content`)
      const result = await response.json()

      if (result.type === 'url') {
        window.open(result.url)
      } else if (result.type === 'file') {
        // Convert base64 to byte array
        const content = atob(result.content)
        const bytes = new Uint8Array(content.length)
        for (let i = 0; i < content.length; i++) {
          bytes[i] = content.charCodeAt(i)
        }

        const mimetype = result.mimetype
        const filename = result.filename

        // Create blob and download
        const blob = new Blob([bytes], { type: mimetype })
        const url = URL.createObjectURL(blob)

        const a = document.createElement('a')
        document.body.appendChild(a)
        a.download = filename
        a.href = url
        a.click()

        // Clean up
        URL.revokeObjectURL(url)
        a.remove()
      }
    } catch (error) {}
  }

  const handleDelete = () => {
    Swal.fire({
      title: 'Are you sure?',
      showCancelButton: true,
      confirmButtonText: 'Delete',
      cancelButtonText: 'Cancel',
      reverseButtons: true,
      confirmButtonColor: '#d33',
      cancelButtonColor: '#6c757d',
    }).then(async (result) => {
      if (result.isConfirmed) {
        try {
          // Disable the row while deleting
          setIsDisabled(true)

          // Make API call to delete the asset
          const response = await fetch(`/api/v2/assets/${props.assetId}`, {
            method: 'DELETE',
          })

          if (response.ok) {
            // Refresh the assets list after successful deletion
            dispatch(fetchAssets())

            // Show success message
            Swal.fire({
              title: 'Deleted!',
              text: 'Asset has been deleted.',
              icon: 'success',
              timer: 2000,
              showConfirmButton: false,
            })
          } else {
            // Show error message
            Swal.fire({
              title: 'Error!',
              text: 'Failed to delete asset.',
              icon: 'error',
            })
          }
        } catch (error) {
          Swal.fire({
            title: 'Error!',
            text: 'Failed to delete asset.',
            icon: 'error',
          })
        } finally {
          setIsDisabled(false)
        }
      }
    })
  }

  const handleEdit = () => {
    if (props.onEditAsset) {
      props.onEditAsset({
        id: props.assetId,
        name: props.name,
        start_date: props.startDate,
        end_date: props.endDate,
        duration: props.duration,
        uri: props.uri,
        mimetype: props.mimetype,
        is_enabled: props.isEnabled,
        nocache: props.nocache,
        skip_asset_check: props.skipAssetCheck,
      })
    }
  }

  return (
    <>
      <style>{tooltipStyles}</style>
      <tr
        ref={ref}
        style={props.style}
        className={classNames({ warning: isDisabled })}
      >
        <td className={classNames('asset_row_name')}>
          <span
            {...props.dragHandleProps}
            style={{
              cursor: props.isDragging ? 'grabbing' : 'grab',
              display: 'inline-block',
            }}
          >
            <FaGripVertical className="mr-2" />
          </span>
          <i className={classNames('asset-icon', 'mr-2')}></i>
          <span data-toggle="tooltip" data-placement="top" title={props.name}>
            {props.name}
          </span>
        </td>
        <td
          style={{ width: '21%', maxWidth: '200px' }}
          className="text-truncate"
          data-toggle="tooltip"
          data-placement="top"
          title={formatDate(props.startDate, dateFormat, use24HourClock)}
        >
          {formatDate(props.startDate, dateFormat, use24HourClock)}
        </td>
        <td
          style={{ width: '21%', maxWidth: '200px' }}
          className="text-truncate"
          data-toggle="tooltip"
          data-placement="top"
          title={formatDate(props.endDate, dateFormat, use24HourClock)}
        >
          {formatDate(props.endDate, dateFormat, use24HourClock)}
        </td>
        <td
          style={{ width: '13%', maxWidth: '150px' }}
          className={classNames('text-truncate')}
          data-toggle="tooltip"
          data-placement="top"
          title={formatDuration(props.duration)}
        >
          {formatDuration(props.duration)}
        </td>
        <td className={classNames('asset-toggle')} style={{ width: '7%' }}>
          <label
            className={classNames(
              'is_enabled-toggle',
              'toggle',
              'switch-light',
              'switch-material',
              'small',
              'm-0',
            )}
          >
            <input
              type="checkbox"
              checked={props.isEnabled}
              onChange={handleToggle}
              disabled={isDisabled || props.isProcessing === 1}
            />
            <span>
              <span className="off"></span>
              <span className="on"></span>
              <a></a>
            </span>
          </label>
        </td>
        <td className={classNames('asset_row_btns')}>
          <button
            className={classNames(
              'download-asset-button',
              'btn',
              'btn-outline-dark',
              'mr-1',
              'd-inline-flex',
              'p-2',
            )}
            type="button"
            disabled={isDisabled}
            onClick={handleDownload}
          >
            <FaDownload />
          </button>
          <button
            className={classNames(
              'edit-asset-button',
              'btn',
              'btn-outline-dark',
              'mr-1',
              'd-inline-flex',
              'p-2',
            )}
            type="button"
            disabled={isDisabled}
            onClick={handleEdit}
          >
            <FaPencilAlt />
          </button>
          <button
            className={classNames(
              'delete-asset-button',
              'btn',
              'btn-outline-dark',
              'd-inline-flex',
              'p-2',
            )}
            type="button"
            onClick={handleDelete}
            disabled={isDisabled}
          >
            <FaTrashAlt />
          </button>
        </td>
      </tr>
    </>
  )
})
