import { AssetEditData } from '@/types'

import { ActiveAssetsTable } from '@/components/active-assets'
import { EmptyAssetMessage } from '@/components/empty-asset-message'

interface ActiveAssetsSectionProps {
  activeAssetsCount: number
  onEditAsset: (asset: AssetEditData) => void
  onAddAssetClick: (event: React.MouseEvent) => void
}

export const ActiveAssetsSection = ({
  activeAssetsCount,
  onEditAsset,
  onAddAssetClick,
}: ActiveAssetsSectionProps) => {
  return (
    <div className="container">
      <div className="row content active-content px-2 pt-4">
        <div className="col-12 mb-5">
          <section id="active-assets-section">
            <h5>
              <b>Active assets</b>
            </h5>
            <ActiveAssetsTable onEditAsset={onEditAsset} />
            {activeAssetsCount === 0 && (
              <EmptyAssetMessage
                onAddAssetClick={onAddAssetClick}
                isActive={true}
              />
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
