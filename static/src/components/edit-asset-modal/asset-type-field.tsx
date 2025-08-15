import { EditFormData } from '@/types'

interface AssetTypeFieldProps {
  formData: EditFormData
  handleInputChange: (e: React.ChangeEvent<HTMLSelectElement>) => void
}

export const AssetTypeField = ({
  formData,
  handleInputChange,
}: AssetTypeFieldProps) => {
  return (
    <div className="row mb-3 mimetype">
      <label className="col-4 col-form-label">Asset Type</label>
      <div className="col-4 controls">
        <select
          className="mime-select form-control shadow-none form-select"
          name="mimetype"
          value={formData.mimetype}
          onChange={handleInputChange}
          disabled={true}
        >
          <option value="webpage">Webpage</option>
          <option value="image">Image</option>
          <option value="video">Video</option>
          <option value="streaming">Streaming</option>
          <option value="youtube_asset">YouTubeAsset</option>
        </select>
      </div>
    </div>
  )
}
