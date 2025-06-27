interface EmptyAssetMessageProps {
  onAddAssetClick: (e: React.MouseEvent<HTMLAnchorElement>) => void;
}

export const EmptyAssetMessage = ({
  onAddAssetClick,
}: EmptyAssetMessageProps) => {
  return (
    <div className="table-assets-help-text">
      Currently, there are no assets.{' '}
      <a className="add-asset-button" href="#" onClick={onAddAssetClick}>
        Add asset
      </a>{' '}
      now.
    </div>
  );
};
