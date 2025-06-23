import { handleLoopTimesChange } from '@/components/edit-asset-modal/utils';

export const PlayForField = ({
  loopTimes,
  startDateDate,
  startDateTime,
  setLoopTimes,
  setEndDateDate,
  setEndDateTime,
  setFormData,
}) => {
  return (
    <div className="row form-group loop_date">
      <label className="col-4 col-form-label">Play for</label>
      <div className="controls col-7">
        <select
          className="form-control shadow-none"
          id="loop_times"
          value={loopTimes}
          onChange={(e) =>
            handleLoopTimesChange({
              e,
              startDateDate,
              startDateTime,
              setLoopTimes,
              setEndDateDate,
              setEndDateTime,
              setFormData,
            })
          }
        >
          <option value="day">1 Day</option>
          <option value="week">1 Week</option>
          <option value="month">1 Month</option>
          <option value="year">1 Year</option>
          <option value="forever">Forever</option>
          <option value="manual">Manual</option>
        </select>
      </div>
    </div>
  );
};
