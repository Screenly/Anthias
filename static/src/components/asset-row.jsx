import {
  FaGripVertical,
  FaDownload,
  FaPencilAlt,
  FaTrashAlt,
} from 'react-icons/fa'
import classNames from 'classnames'
import { forwardRef, useState } from 'react'
import { useDispatch } from 'react-redux'
import { toggleAssetEnabled, fetchAssets } from '@/store/assets-slice'
import Swal from 'sweetalert2'

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

export const AssetRow = forwardRef((props, ref) => {
  const dispatch = useDispatch()
  const [isDisabled, setIsDisabled] = useState(false)

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

  return (
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
        {props.name}
      </td>
      <td style={{ width: '21%' }}>{props.startDate}</td>
      <td style={{ width: '21%' }}>{props.endDate}</td>
      <td style={{ width: '13%' }}>{formatDuration(props.duration)}</td>
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
  )
})
