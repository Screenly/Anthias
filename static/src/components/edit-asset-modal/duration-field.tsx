export const DurationField = ({ formData, handleInputChange }) => {
  return (
    <div className="form-group row duration">
      <label className="col-4 col-form-label">Duration</label>
      <div className="col-7 controls">
        <input
          className="form-control shadow-none"
          name="duration"
          type="number"
          value={formData.duration}
          onChange={handleInputChange}
          disabled={formData.mimetype === 'video'}
        />
        seconds &nbsp;
      </div>
    </div>
  );
};
