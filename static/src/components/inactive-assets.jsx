import { useSelector } from 'react-redux'
import { selectInactiveAssets } from '@/store/assets'
import { AssetRow } from '@/components/asset-row'

export const InactiveAssetsTable = ({ onEditAsset }) => {
  const inactiveAssets = useSelector(selectInactiveAssets)

  return (
    <>
      <table className="table">
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
            ></th>
          </tr>
        </thead>
      </table>
      <div className="mb-1"></div>
      <table className="table">
        <tbody id="inactive-assets" className="table-borderless">
          {inactiveAssets.map((asset) => (
            <AssetRow
              key={asset.asset_id}
              name={asset.name}
              startDate={asset.start_date}
              endDate={asset.end_date}
              duration={asset.duration}
              isEnabled={asset.is_enabled}
              assetId={asset.asset_id}
              isProcessing={asset.is_processing}
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
    </>
  )
}
