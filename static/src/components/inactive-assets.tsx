import { useSelector } from 'react-redux';
import { selectInactiveAssets } from '@/store/assets';
import { AssetRow } from '@/components/asset-row';
import { Asset, InactiveAssetsTableProps } from '@/types';

export const InactiveAssetsTable = ({
  onEditAsset,
}: InactiveAssetsTableProps) => {
  const inactiveAssets = useSelector(selectInactiveAssets) as Asset[];

  return (
    <table className="InactiveAssets table">
      <thead className="table-borderless">
        <tr>
          <th className="text-secondary font-weight-normal asset_row_name">
            Name
          </th>
          <th
            className="text-secondary font-weight-normal"
            style={{ width: '21%' }}
          >
            Start
          </th>
          <th
            className="text-secondary font-weight-normal"
            style={{ width: '21%' }}
          >
            End
          </th>
          <th
            className="text-secondary font-weight-normal"
            style={{ width: '13%' }}
          >
            Duration
          </th>
          <th
            className="text-secondary font-weight-normal"
            style={{ width: '7%' }}
          >
            Activity
          </th>
          <th
            className="text-secondary font-weight-normal"
            style={{ width: '13%' }}
          >
            Actions
          </th>
        </tr>
      </thead>
      <tbody id="inactive-assets" className="table-borderless">
        {inactiveAssets.map((asset) => (
          <AssetRow
            key={asset.asset_id}
            name={asset.name}
            startDate={asset.start_date}
            endDate={asset.end_date}
            duration={asset.duration}
            isEnabled={Boolean(asset.is_enabled)}
            assetId={asset.asset_id}
            isProcessing={asset.is_processing ? 1 : 0}
            uri={asset.uri}
            mimetype={asset.mimetype}
            nocache={asset.nocache}
            skipAssetCheck={asset.skip_asset_check}
            onEditAsset={onEditAsset}
            showDragHandle={false}
          />
        ))}
      </tbody>
    </table>
  );
};
