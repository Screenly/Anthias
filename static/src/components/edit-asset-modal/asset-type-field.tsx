export const AssetTypeField = ({ formData, handleInputChange }) => {
  return (
    <div className="form-group row mimetype">
      <label className="col-4 col-form-label">Asset Type</label>
      <div className="col-4 controls">
        <select
          className="mime-select form-control shadow-none"
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
  );
};
