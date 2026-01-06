import classNames from 'classnames'
import { FaFastBackward, FaFastForward, FaPlus } from 'react-icons/fa'

interface ScheduleActionButtonsProps {
  onPreviousAsset: (event: React.MouseEvent) => void
  onNextAsset: (event: React.MouseEvent) => void
  onAddAsset: (event: React.MouseEvent) => void
}

export const ScheduleActionButtons = ({
  onPreviousAsset,
  onNextAsset,
  onAddAsset,
}: ScheduleActionButtonsProps) => {
  return (
    <div className="d-flex flex-column flex-sm-row gap-2 mb-3 mt-4">
      <button
        id="previous-asset-button"
        className={classNames(
          'btn',
          'btn-long',
          'btn-outline-primary',
          'fw-bold',
        )}
        onClick={onPreviousAsset}
      >
        <span className="d-flex align-items-center justify-content-center">
          <FaFastBackward className="pe-2 fs-4" />
          Previous Asset
        </span>
      </button>
      <button
        id="next-asset-button"
        className={classNames(
          'btn',
          'btn-long',
          'btn-outline-primary',
          'fw-bold',
        )}
        onClick={onNextAsset}
      >
        <span className="d-flex align-items-center justify-content-center">
          <FaFastForward className="pe-2 fs-4" />
          Next Asset
        </span>
      </button>
      <button
        id="add-asset-button"
        className={classNames(
          'add-asset-button',
          'btn',
          'btn-long',
          'btn-primary',
        )}
        onClick={onAddAsset}
      >
        <span className="d-flex align-items-center justify-content-center">
          <FaPlus className="pe-2 fs-5" />
          Add Asset
        </span>
      </button>
    </div>
  )
}
