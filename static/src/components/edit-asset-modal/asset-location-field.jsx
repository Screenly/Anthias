export const AssetLocationField = ({ asset }) => {
  return (
    <div className="form-group row">
      <label className="col-4 col-form-label">Asset Location</label>
      <div className="col-8 controls">
        <div
          className="uri-text first text-break"
          style={{ wordBreak: 'break-all' }}
        >
          {asset?.uri || ''}
        </div>
      </div>
    </div>
  )
}
