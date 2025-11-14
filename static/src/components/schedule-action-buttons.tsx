import classNames from 'classnames'

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
      <a
        id="previous-asset-button"
        className={classNames(
          'btn',
          'btn-long',
          'btn-light',
          'fw-bold',
          'text-dark',
        )}
        href="#"
        onClick={onPreviousAsset}
      >
        <i className="fas fa-chevron-left pe-2"></i>
        Previous Asset
      </a>
      <a
        id="next-asset-button"
        className={classNames(
          'btn',
          'btn-long',
          'btn-light',
          'fw-bold',
          'text-dark',
        )}
        href="#"
        onClick={onNextAsset}
      >
        Next Asset
        <i className="fas fa-chevron-right ps-2"></i>
      </a>
      <a
        id="add-asset-button"
        className={classNames(
          'add-asset-button',
          'btn',
          'btn-long',
          'btn-primary',
        )}
        href="#"
        onClick={onAddAsset}
      >
        Add Asset
      </a>
    </div>
  )
}
