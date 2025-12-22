import { GiHamburgerMenu } from 'react-icons/gi'
import classNames from 'classnames'
import { useEffect, forwardRef, useState } from 'react'
import { useDispatch } from 'react-redux'

import { toggleAssetEnabled, fetchAssets } from '@/store/assets'
import { AssetRowProps, AppDispatch } from '@/types'

import {
  formatDate,
  formatDuration,
  handleDelete,
  handleDownload,
} from '@/components/asset-row/utils'
import { MimetypeIcon } from '@/components/asset-row/mimetype-icon'
import { ActionButtons } from '@/components/asset-row/action-buttons'

export const AssetRow = forwardRef<HTMLTableRowElement, AssetRowProps>(
  (props, ref) => {
    const defaultDateFormat = 'mm/dd/yyyy'
    const dispatch = useDispatch<AppDispatch>()
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
        } catch {
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
      } catch {
      } finally {
        setIsDisabled(false)
      }
    }

    const handleDownloadWrapper = (event: React.MouseEvent) => {
      handleDownload(event, props.assetId)
    }

    const handleDeleteWrapper = () => {
      handleDelete(props.assetId, setIsDisabled, dispatch, fetchAssets)
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
        <tr
          ref={ref}
          style={props.style}
          className={classNames({ warning: isDisabled })}
        >
          <td className={classNames('asset_row_name')}>
            {props.showDragHandle && (
              <span
                {...props.dragHandleProps}
                style={{
                  cursor: props.isDragging ? 'grabbing' : 'grab',
                  display: 'inline-block',
                  verticalAlign: 'middle',
                }}
              >
                <GiHamburgerMenu
                  className="me-3 align-middle"
                  style={{ verticalAlign: 'middle' }}
                />
              </span>
            )}
            <MimetypeIcon
              mimetype={props.mimetype}
              className="me-2 align-middle"
              style={{ verticalAlign: 'middle' }}
            />
            <span
              data-bs-toggle="tooltip"
              data-bs-placement="top"
              title={props.name}
              style={{
                verticalAlign: 'middle',
              }}
            >
              {props.name}
            </span>
          </td>
          <td
            style={{ width: '21%', maxWidth: '200px' }}
            className="text-truncate"
            data-bs-toggle="tooltip"
            data-bs-placement="top"
            title={formatDate(props.startDate, dateFormat, use24HourClock)}
          >
            {formatDate(props.startDate, dateFormat, use24HourClock)}
          </td>
          <td
            style={{ width: '21%', maxWidth: '200px' }}
            className="text-truncate"
            data-bs-toggle="tooltip"
            data-bs-placement="top"
            title={formatDate(props.endDate, dateFormat, use24HourClock)}
          >
            {formatDate(props.endDate, dateFormat, use24HourClock)}
          </td>
          <td
            style={{ width: '13%', maxWidth: '150px' }}
            className={classNames('text-truncate')}
            data-bs-toggle="tooltip"
            data-bs-placement="top"
            title={formatDuration(props.duration)}
          >
            {formatDuration(props.duration)}
          </td>
          <td style={{ width: '7%' }}>
            {props.isProcessing === 1 ? (
              <div className="text-center">
                <small className="text-muted">Processing</small>
              </div>
            ) : (
              <div
                className={classNames(
                  'form-check',
                  'form-switch',
                  'd-flex',
                  'justify-content-center',
                )}
              >
                <input
                  className={classNames('form-check-input', 'shadow-none')}
                  type="checkbox"
                  role="switch"
                  id={`asset-switch-${props.assetId}`}
                  checked={props.isEnabled}
                  onChange={handleToggle}
                  disabled={isDisabled}
                />
              </div>
            )}
          </td>
          <td className={classNames('asset_row_btns', 'text-center')}>
            <ActionButtons
              isDisabled={isDisabled || props.isProcessing === 1}
              handleDownload={handleDownloadWrapper}
              handleEdit={handleEdit}
              handleDelete={handleDeleteWrapper}
            />
          </td>
        </tr>
      </>
    )
  },
)
