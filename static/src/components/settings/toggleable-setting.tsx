export const ToggleableSetting = ({
  settings,
  handleInputChange,
  label,
  name,
}) => {
  return (
    <div className="form-inline mt-4">
      <label>{label}</label>
      <div className="ml-auto">
        <label className="is_enabled-toggle toggle switch-light switch-material small m-0">
          <input
            name={name}
            type="checkbox"
            checked={settings[name]}
            onChange={handleInputChange}
          />
          <span>
            <span></span>
            <span></span>
            <a></a>
          </span>
        </label>
      </div>
    </div>
  );
};
