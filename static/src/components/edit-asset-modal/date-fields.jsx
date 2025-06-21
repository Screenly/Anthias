export const DateFields = ({
  startDateDate,
  startDateTime,
  endDateDate,
  endDateTime,
  handleDateChange,
}) => {
  return (
    <div id="manul_date">
      <div className="form-group row start_date">
        <label className="col-4 col-form-label">Start Date</label>
        <div className="controls col-7">
          <input
            className="form-control date shadow-none"
            name="start_date_date"
            type="date"
            value={startDateDate}
            onChange={(e) => handleDateChange(e, 'startDate')}
            style={{ marginRight: '5px' }}
          />
          <input
            className="form-control time shadow-none"
            name="start_date_time"
            type="time"
            value={startDateTime}
            onChange={(e) => handleDateChange(e, 'startTime')}
          />
        </div>
      </div>
      <div className="form-group row end_date">
        <label className="col-4 col-form-label">End Date</label>
        <div className="controls col-7">
          <input
            className="form-control date shadow-none"
            name="end_date_date"
            type="date"
            value={endDateDate}
            onChange={(e) => handleDateChange(e, 'endDate')}
            style={{ marginRight: '5px' }}
          />
          <input
            className="form-control time shadow-none"
            name="end_date_time"
            type="time"
            value={endDateTime}
            onChange={(e) => handleDateChange(e, 'endTime')}
          />
        </div>
      </div>
    </div>
  )
}
