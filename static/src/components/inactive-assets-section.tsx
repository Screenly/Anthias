import { AssetEditData } from '@/types'

import { EmptyAssetMessage } from '@/components/empty-asset-message'
import { InactiveAssetsTable } from '@/components/inactive-assets'

interface InactiveAssetsSectionProps {
  inactiveAssetsCount: number
  onEditAsset: (asset: AssetEditData) => void
  onAddAssetClick: (event: React.MouseEvent) => void
}

export const InactiveAssetsSection = ({
  inactiveAssetsCount,
  onEditAsset,
  onAddAssetClick,
}: InactiveAssetsSectionProps) => {
  return (
    <div className="container mt-4">
      <div className="row content inactive-content px-2 pt-4">
        <div className="col-12 mb-5">
          <section id="inactive-assets-section">
            <h5>
              <b>Inactive assets</b>
            </h5>
            <InactiveAssetsTable onEditAsset={onEditAsset} />
            {inactiveAssetsCount === 0 && (
              <EmptyAssetMessage
                onAddAssetClick={onAddAssetClick}
                isActive={false}
              />
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
