import { FaDownload, FaPencilAlt, FaTrashAlt } from 'react-icons/fa';
import classNames from 'classnames';

interface ActionButtonsProps {
  isDisabled: boolean;
  handleDownload: (event: React.MouseEvent) => void;
  handleEdit: () => void;
  handleDelete: () => void;
}

export const ActionButtons = ({
  isDisabled,
  handleDownload,
  handleEdit,
  handleDelete,
}: ActionButtonsProps) => {
  return (
    <>
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
    </>
  );
};
