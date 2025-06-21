export const NameField = ({ formData, handleInputChange }) => {
  return (
    <div className="form-group row name">
      <label className="col-4 col-form-label">Name</label>
      <div className="col-7">
        <input
          className="form-control shadow-none"
          name="name"
          placeholder="Nickname for this asset"
          type="text"
          value={formData.name}
          onChange={handleInputChange}
        />
      </div>
    </div>
  )
}
