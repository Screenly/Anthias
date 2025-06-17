export const DateFormat = ({ settings, handleInputChange }) => {
  return (
    <div className="form-group">
      <label className="small text-secondary">
        <small>Date format</small>
      </label>
      <select
        className="form-control shadow-none"
        name="dateFormat"
        value={settings.dateFormat}
        onChange={handleInputChange}
      >
        <option value="mm/dd/yyyy">month/day/year</option>
        <option value="dd/mm/yyyy">day/month/year</option>
        <option value="yyyy/mm/dd">year/month/day</option>
        <option value="mm-dd-yyyy">month-day-year</option>
        <option value="dd-mm-yyyy">day-month-year</option>
        <option value="yyyy-mm-dd">year-month-day</option>
        <option value="mm.dd.yyyy">month.day.year</option>
        <option value="dd.mm.yyyy">day.month.year</option>
        <option value="yyyy.mm.dd">year.month.day</option>
      </select>
    </div>
  )
}
