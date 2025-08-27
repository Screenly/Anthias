import { AssetEditData } from '@/types'

interface AssetLocationFieldProps {
  asset: AssetEditData | null
}

export const AssetLocationField = ({ asset }: AssetLocationFieldProps) => {
  return (
    <div className="row mb-3">
      <label className="col-4 col-form-label">Asset Location</label>
      <div className="col-8 controls">
        <div
          className="uri-text first text-break h-100"
          style={{
            display: 'flex',
            alignItems: 'center',
          }}
        >
          <span>{asset?.uri ?? ''}</span>
        </div>
      </div>
    </div>
  )
}
