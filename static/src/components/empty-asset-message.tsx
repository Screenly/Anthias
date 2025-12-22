import classNames from 'classnames'

interface EmptyAssetMessageProps {
  onAddAssetClick: (e: React.MouseEvent<HTMLAnchorElement>) => void
  isActive?: boolean
}

export const EmptyAssetMessage = ({
  onAddAssetClick,
  isActive,
}: EmptyAssetMessageProps) => {
  return (
    <div className="EmptyAssetMessage table-assets-help-text">
      Currently, there are no assets.{' '}
      <a
        className={classNames('add-asset-button', {
          'text-primary': isActive,
          'text-info': !isActive,
        })}
        href="#"
        onClick={onAddAssetClick}
      >
        Add asset
      </a>{' '}
      now.
    </div>
  )
}
