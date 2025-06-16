export const EmptyAssetMessage = ({ onAddAssetClick }) => {
  return (
    <div className="table-assets-help-text">
      Currently, there are no assets.
      {' '}
      <a className="add-asset-button" href="#" onClick={onAddAssetClick}>
        Add asset
      </a>
      {' '}
      now.
    </div>
  )
}
