interface DateFieldsProps {
  startDateDate: string
  startDateTime: string
  endDateDate: string
  endDateTime: string
  handleDateChange: (
    e: React.ChangeEvent<HTMLInputElement>,
    type: string,
  ) => void
}

export const DateFields = ({
  startDateDate,
  startDateTime,
  endDateDate,
  endDateTime,
  handleDateChange,
}: DateFieldsProps) => {
  return (
    <div id="manul_date">
      <div className="row mb-3 start_date">
        <label className="col-4 col-form-label">Start Date</label>
        <div className="controls col-8 d-flex flex-wrap flex-sm-nowrap gap-2">
          <input
            className="form-control date shadow-none flex-grow-1"
            style={{ minWidth: '8rem' }}
            name="start_date_date"
            type="date"
            value={startDateDate}
            onChange={(e) => handleDateChange(e, 'startDate')}
          />
          <input
            className="form-control time shadow-none flex-grow-1"
            style={{ minWidth: '6rem' }}
            name="start_date_time"
            type="time"
            value={startDateTime}
            onChange={(e) => handleDateChange(e, 'startTime')}
          />
        </div>
      </div>
      <div className="row mb-3 end_date">
        <label className="col-4 col-form-label">End Date</label>
        <div className="controls col-8 d-flex flex-wrap flex-sm-nowrap gap-2">
          <input
            className="form-control date shadow-none flex-grow-1"
            style={{ minWidth: '8rem' }}
            name="end_date_date"
            type="date"
            value={endDateDate}
            onChange={(e) => handleDateChange(e, 'endDate')}
          />
          <input
            className="form-control time shadow-none flex-grow-1"
            style={{ minWidth: '6rem' }}
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
