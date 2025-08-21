import { useSelector } from 'react-redux'
import { selectInactiveAssets } from '@/store/assets'
import { AssetRow } from '@/components/asset-row'
import { Asset, InactiveAssetsTableProps } from '@/types'

export const InactiveAssetsTable = ({
  onEditAsset,
}: InactiveAssetsTableProps) => {
  const inactiveAssets = useSelector(selectInactiveAssets) as Asset[]

  return (
    <div className="table-responsive">
      <table className="InactiveAssets table table-borderless">
        <thead>
          <tr>
            <th className="text-secondary fw-bold asset_row_name">Name</th>
            <th
              className="text-secondary fw-bold d-none d-md-table-cell"
              style={{ width: '21%' }}
            >
              Start
            </th>
            <th
              className="text-secondary fw-bold d-none d-md-table-cell"
              style={{ width: '21%' }}
            >
              End
            </th>
            <th
              className="text-secondary fw-bold d-none d-sm-table-cell"
              style={{ width: '13%' }}
            >
              Duration
            </th>
            <th
              className="text-secondary fw-bold d-none d-lg-table-cell"
              style={{ width: '7%' }}
            >
              Activity
            </th>
            <th className="text-secondary fw-bold" style={{ width: '13%' }}>
              Actions
            </th>
          </tr>
        </thead>
        <tbody id="inactive-assets">
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
    </div>
  )
}
