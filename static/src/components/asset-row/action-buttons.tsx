import { FaDownload, FaPencilAlt, FaTrashAlt } from 'react-icons/fa'
import classNames from 'classnames'

interface ActionButtonsProps {
  isDisabled: boolean
  handleDownload: (event: React.MouseEvent) => void
  handleEdit: () => void
  handleDelete: () => void
}

export const ActionButtons = ({
  isDisabled,
  handleDownload,
  handleEdit,
  handleDelete,
}: ActionButtonsProps) => {
  const buttonClass = classNames(
    'btn',
    'btn-outline-dark',
    'mr-1',
    'd-inline-flex',
    'p-2',
    {
      disabled: isDisabled,
    },
  )

  const tooltipText = isDisabled ? 'Asset is currently being processed' : ''

  return (
    <>
      <button
        className={classNames(buttonClass, 'download-asset-button')}
        type="button"
        disabled={isDisabled}
        onClick={handleDownload}
        title={tooltipText}
        data-toggle="tooltip"
        data-placement="top"
      >
        <FaDownload />
      </button>
      <button
        className={classNames(buttonClass, 'edit-asset-button')}
        type="button"
        disabled={isDisabled}
        onClick={handleEdit}
        title={tooltipText}
        data-toggle="tooltip"
        data-placement="top"
      >
        <FaPencilAlt />
      </button>
      <button
        className={classNames(buttonClass, 'delete-asset-button')}
        type="button"
        onClick={handleDelete}
        disabled={isDisabled}
        title={tooltipText}
        data-toggle="tooltip"
        data-placement="top"
      >
        <FaTrashAlt />
      </button>
    </>
  )
}
